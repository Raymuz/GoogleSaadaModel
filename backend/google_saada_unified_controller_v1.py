"""GoogleSaada staging controller for provenance-verified fixture inference.

No authentication/credits here. Must be invoked ONLY behind the existing gate.
Not wired to live prediction until provenance-producing feature builder is audited.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import importlib.util
import json
import math

MAX_MATCHES=16
MARKETS=('match_result','over_2_5','btts')

class EvidenceUnavailable(Exception): pass
class ContractViolation(Exception): pass

def _utc(value):
    if isinstance(value,datetime): dt=value
    elif isinstance(value,str):
        try: dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError as exc: raise EvidenceUnavailable('Invalid timestamp') from exc
    else: raise EvidenceUnavailable('Missing timestamp')
    if dt.tzinfo is None: raise EvidenceUnavailable('Timezone-aware timestamp required')
    return dt.astimezone(timezone.utc)

def _validate_fixture(fixture,now):
    if not isinstance(fixture,dict): raise EvidenceUnavailable('Fixture record missing')
    for k in ('fixture_id','source','source_fixture_id','home_id','away_id','home','away','kickoff_utc'):
        if not isinstance(fixture.get(k),str) or not fixture[k].strip():
            raise EvidenceUnavailable('Verified fixture field missing: '+k)
    if fixture['home_id']==fixture['away_id']: raise EvidenceUnavailable('Home and away identities identical')
    kick=_utc(fixture['kickoff_utc'])
    if kick<=now: raise EvidenceUnavailable('Fixture has already started')
    if fixture.get('verified') is not True: raise EvidenceUnavailable('Fixture identity unverified')
    return kick

def _validate_evidence(evidence,fixture,kick,now):
    if not isinstance(evidence,dict): raise EvidenceUnavailable('Feature evidence missing')
    if evidence.get('fixture_id')!=fixture['fixture_id']: raise EvidenceUnavailable('Fixture/evidence identity mismatch')
    if evidence.get('source_fixture_id')!=fixture['source_fixture_id']: raise EvidenceUnavailable('Source fixture mismatch')
    if evidence.get('home_id')!=fixture['home_id'] or evidence.get('away_id')!=fixture['away_id']:
        raise EvidenceUnavailable('Feature evidence team identities mismatch')
    cutoff=_utc(evidence.get('feature_cutoff_utc'))
    if cutoff>=kick or cutoff>now: raise EvidenceUnavailable('Post-kickoff or future feature evidence')
    if evidence.get('verified') is not True: raise EvidenceUnavailable('Feature provenance unverified')
    if not isinstance(evidence.get('data_source'),str) or not evidence['data_source'].strip():
        raise EvidenceUnavailable('Feature data source missing')
    return cutoff

def _registry(registry_path):
    r=json.loads(Path(registry_path).read_text(encoding='utf-8'))
    if r.get('maximum_matches')!=16 or len(r.get('original_models',{}))!=10 or len(r.get('selected_worldwide_models',{}))!=3:
        raise ContractViolation('Unexpected model registry')
    if r.get('live_routing_enabled') is not False: raise ContractViolation('Live routing approval changed')
    return r

def analyze_verified_batch(fixtures, features_by_id, registry_path, *, now_utc=None, markets=MARKETS, predictor=None):
    """Controlled staging invocation using PREVERIFIED pre-kickoff features.

    `predictor` must be an explicitly supplied, audited adapter such as
    staging_predict_one(market,feature_row,registry_path). This function does
    not claim an evidence producer exists and must not be a public endpoint.
    """
    if not isinstance(fixtures,list) or not 1<=len(fixtures)<=MAX_MATCHES:
        raise ContractViolation('Batch must contain 1 to 16 fixtures')
    if not isinstance(features_by_id,dict): raise ContractViolation('Feature mapping missing')
    if not isinstance(markets,(list,tuple)) or not markets or len(set(markets))!=len(markets) or any(m not in MARKETS for m in markets):
        raise ContractViolation('Unsupported or repeated prediction market')
    if not callable(predictor): raise ContractViolation('Verified model adapter not supplied')
    r=_registry(registry_path)
    now=_utc(now_utc) if now_utc is not None else datetime.now(timezone.utc)
    seen=set(); output=[]
    for fixture in fixtures:
        fid=str(fixture.get('fixture_id','')) if isinstance(fixture,dict) else ''
        if not fid or fid in seen: raise ContractViolation('Duplicate or missing fixture identifier')
        seen.add(fid)
        item={'fixture_id':fid,'markets':{},'status':'UNAVAILABLE','staging_only':True}
        try:
            kick=_validate_fixture(fixture,now)
            evidence=features_by_id.get(fid)
            cutoff=_validate_evidence(evidence,fixture,kick,now)
            bymarket=evidence.get('market_features')
            if not isinstance(bymarket,dict): raise EvidenceUnavailable('Market-specific feature rows missing')
            item.update(source=fixture['source'],source_fixture_id=fixture['source_fixture_id'],home=fixture['home'],away=fixture['away'],kickoff_utc=kick.isoformat(),feature_cutoff_utc=cutoff.isoformat())
            for market in markets:
                spec=r['selected_worldwide_models'][market]
                if spec.get('production_approved') is not False: raise ContractViolation('Model staging approval changed')
                row=bymarket.get(market)
                try:
                    if not isinstance(row,dict): raise EvidenceUnavailable('Market features unavailable')
                    expected=spec.get('feature_names')
                    if not isinstance(expected,list) or set(row)!=set(expected): raise EvidenceUnavailable('Feature schema mismatch')
                    if any(isinstance(row[k],bool) or not isinstance(row[k],(float,int)) or not math.isfinite(float(row[k])) for k in expected):
                        raise EvidenceUnavailable('Nonfinite or nonnumeric feature')
                    result=predictor(market,row,registry_path)
                    if not isinstance(result,dict) or result.get('market')!=market or result.get('model_sha256')!=spec['sha256'] or result.get('staging_only') is not True:
                        raise ContractViolation('Model output provenance mismatch')
                    labels=[str(k) for k in spec['required_class_order']]
                    p=result.get('probabilities',{})
                    if not isinstance(p,dict) or set(p)!=set(labels) or any(not isinstance(v,(float,int)) or not math.isfinite(float(v)) or not 0<=v<=1 for v in p.values()) or not math.isclose(sum(p.values()),1,abs_tol=1e-6):
                        raise ContractViolation('Invalid model probabilities')
                    item['markets'][market]={'probabilities':p,'model_version':spec['version'],'model_sha256':spec['sha256']}
                except EvidenceUnavailable as exc:
                    item['markets'][market]={'unavailable':str(exc)}
            item['status']='PARTIAL' if any('unavailable' in v for v in item['markets'].values()) else 'READY_STAGING'
        except EvidenceUnavailable as exc:
            item['reason']=str(exc)
        output.append(item)
    return {'staging_only':True,'count_requested':len(fixtures),'count_complete':sum(x['status']=='READY_STAGING' for x in output),'results':output,
            'disclaimer':'Statistical estimates are uncertain and never guaranteed. Limited team history may increase uncertainty.'}
