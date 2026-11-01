"""GoogleSaada V2 orchestration, staging only.

Trusted caller supplies already-verified fixtures and evidence. No auth, billing,
HTTP, fixture fetching, training, model loading or release switch here.
Individual specialists append to a private evidence board; no unvalidated mixing.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable
import copy
import math

MAX_MATCHES = 16
WORLDWIDE = ('match_result','over_2_5','btts')
ORIGINAL = ('xgboost', 'logistic_regression', 'random_forest', 'elo_only',
            'ft_home_poisson', 'ft_away_poisson', 'ht_home_poisson',
            'ht_away_poisson', 'sh_home_poisson', 'sh_away_poisson')
SPECIALISTS = ORIGINAL + ('worldwide_match_result', 'worldwide_over_2_5', 'worldwide_btts')
MARKET_BY_SPECIALIST = {
    **{k:'match_result' for k in ORIGINAL[:4]},
    **{k:'ft_home_expected_goals' for k in ORIGINAL[4:5]},
    **{k:'ft_away_expected_goals' for k in ORIGINAL[5:6]},
    **{k:'ht_home_expected_goals' for k in ORIGINAL[6:7]},
    **{k:'ht_away_expected_goals' for k in ORIGINAL[7:8]},
    **{k:'sh_home_expected_goals' for k in ORIGINAL[8:9]},
    **{k:'sh_away_expected_goals' for k in ORIGINAL[9:10]},
    'worldwide_match_result':'match_result',
    'worldwide_over_2_5':'over_2_5',
    'worldwide_btts':'btts',
}
CLASSES = {'match_result':('H','D','A'), 'over_2_5':('0','1'),'btts':('0','1')}

class OrchestrationError(ValueError): pass

def utc(value):
    if isinstance(value,str):
        try: value=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError as exc: raise OrchestrationError('Invalid datetime') from exc
    if not isinstance(value,datetime) or value.tzinfo is None:
        raise OrchestrationError('Timezone aware UTC evidence required')
    return value.astimezone(timezone.utc)

def verify_request(fixtures, evidence_by_id, now):
    now=utc(now)
    if not isinstance(fixtures,list) or not 1<=len(fixtures)<=MAX_MATCHES:
        raise OrchestrationError('1–16 fixtures required')
    if not isinstance(evidence_by_id,dict):
        raise OrchestrationError('Evidence map required')
    seen=set()
    for f in fixtures:
        if not isinstance(f,dict): raise OrchestrationError('Fixture invalid')
        for key in ('fixture_id','source','source_fixture_id','home_id','away_id','home','away','kickoff_utc'):
            if not isinstance(f.get(key),str) or not f[key].strip():
                raise OrchestrationError('Fixture field missing: '+key)
        fid=f['fixture_id']
        if fid in seen or f['home_id']==f['away_id']:
            raise OrchestrationError('Duplicate fixture or invalid team identities')
        seen.add(fid)
        if f.get('verified') is not True: raise OrchestrationError('Fixture unverified')
        kick=utc(f['kickoff_utc'])
        if kick<=now: raise OrchestrationError('Started match')
        ev=evidence_by_id.get(fid)
        if not isinstance(ev,dict) or ev.get('verified') is not True:
            raise OrchestrationError('Feature evidence unverified')
        for k in ('fixture_id','source_fixture_id','home_id','away_id'):
            if ev.get(k)!=f[k]: raise OrchestrationError('Evidence identity mismatch')
        if not isinstance(ev.get('data_source'),str) or not ev['data_source'].strip():
            raise OrchestrationError('Missing evidence source')
        cutoff=utc(ev.get('feature_cutoff_utc'))
        if cutoff>now or cutoff>=kick:
            raise OrchestrationError('Feature time leakage')
    return True

class SpecialistWhiteboard:
    """Append-only records: one fixture/market/specialist, with provenance."""
    def __init__(self, fixture_ids):
        self.fixture_ids=frozenset(fixture_ids)
        self._items={}
    def publish(self, *, fixture_id, specialist, market, value, evidence_id, artifact_sha256):
        if fixture_id not in self.fixture_ids: raise OrchestrationError('Unknown fixture')
        if specialist not in SPECIALISTS or MARKET_BY_SPECIALIST[specialist]!=market:
            raise OrchestrationError('Wrong specialist/market')
        if not isinstance(evidence_id,str) or not evidence_id.strip():
            raise OrchestrationError('Evidence provenance absent')
        if not isinstance(artifact_sha256,str) or len(artifact_sha256)!=64 or any(c not in '0123456789abcdef' for c in artifact_sha256.lower()):
            raise OrchestrationError('Missing model hash')
        if market in CLASSES:
            if not isinstance(value,dict) or set(value)!=set(CLASSES[market]):
                raise OrchestrationError('Bad class labels')
            if any(type(p) not in (float,int) or not math.isfinite(p) or not 0<=p<=1 for p in value.values()):
                raise OrchestrationError('Invalid probabilities')
            if not math.isclose(sum(value.values()),1.0,abs_tol=1e-6,rel_tol=1e-6):
                raise OrchestrationError('Not normalized')
        else:
            if type(value) not in (float,int) or not math.isfinite(value) or value<0:
                raise OrchestrationError('Invalid expected goals')
        key=(fixture_id,market,specialist)
        if key in self._items: raise OrchestrationError('Duplicate specialist publication')
        self._items[key]={'fixture_id':fixture_id,'market':market,'specialist':specialist,
                          'value':copy.deepcopy(value),'evidence_id':evidence_id,'artifact_sha256':artifact_sha256.lower()}
    def snapshot(self):
        return copy.deepcopy(list(self._items.values()))
    def coverage(self,fixture_id,markets):
        return {m: 'AVAILABLE' if any(k[0]==fixture_id and k[1]==m for k in self._items) else 'UNAVAILABLE' for m in markets}

class MasterOrchestratorV2:
    """Caller-controlled, fail-closed dispatch. Nothing executes without explicit adapters."""
    def __init__(self, model_registry, *, adapters, evidence_builder=None):
        if not isinstance(model_registry,dict) or len(model_registry.get('original_models',{}))!=10 or len(model_registry.get('selected_worldwide_models',{}))!=3 or model_registry.get('live_routing_enabled') is not False:
            raise OrchestrationError('Registry not a staging 10+3 registry')
        if not isinstance(adapters,dict): raise OrchestrationError('Adapters mapping required')
        if any(k not in SPECIALISTS or not callable(v) for k,v in adapters.items()):
            raise OrchestrationError('Unknown specialist adapter')
        self.registry=model_registry
        self.adapters=adapters
        self.evidence_builder=evidence_builder
    def execute(self, fixtures, verified_evidence, *, now_utc, requested_markets=('match_result','over_2_5','btts')):
        """Test/staging only; never call from public callback without existing auth/quota."""
        if not isinstance(requested_markets,(list,tuple)) or not requested_markets or len(set(requested_markets))!=len(requested_markets):
            raise OrchestrationError('Invalid requested markets')
        if any(m not in set(MARKET_BY_SPECIALIST.values()) for m in requested_markets):
            raise OrchestrationError('Unregistered market')
        verify_request(fixtures,verified_evidence,now_utc)
        board=SpecialistWhiteboard([f['fixture_id'] for f in fixtures])
        failures=[]
        for fixture in fixtures:
            fid=fixture['fixture_id']
            ev=verified_evidence[fid]
            # Independent contributions; an unavailable model cannot prevent other models reporting.
            for specialist in SPECIALISTS:
                market=MARKET_BY_SPECIALIST[specialist]
                if market not in requested_markets: continue
                adapter=self.adapters.get(specialist)
                if adapter is None:
                    failures.append({'fixture_id':fid,'specialist':specialist,'reason':'NO_VERIFIED_ADAPTER'})
                    continue
                # No synthetic feature creation: adapter must validate its own exact feature schema.
                inputs=ev.get('specialist_features',{}).get(specialist)
                if not isinstance(inputs,dict):
                    failures.append({'fixture_id':fid,'specialist':specialist,'reason':'NO_VERIFIED_FEATURES'})
                    continue
                try:
                    result=adapter(fixture=copy.deepcopy(fixture), features=copy.deepcopy(inputs),evidence=copy.deepcopy(ev))
                    if not isinstance(result,dict) or result.get('market')!=market or result.get('specialist')!=specialist:
                        raise OrchestrationError('Adapter output identity mismatch')
                    board.publish(fixture_id=fid,specialist=specialist,market=market,
                                  value=result.get('value'),evidence_id=result.get('evidence_id'),
                                  artifact_sha256=result.get('artifact_sha256'))
                except Exception as exc:
                    # Do not include source exception text, which may contain sensitive identifiers.
                    failures.append({'fixture_id':fid,'specialist':specialist,'reason':'ADAPTER_OR_OUTPUT_REJECTED','error_type':type(exc).__name__})
        return {'status':'STAGING_ONLY_NOT_RELEASED', 'whiteboard':board.snapshot(),
                'coverage':{f['fixture_id']:board.coverage(f['fixture_id'],requested_markets) for f in fixtures},
                'failures':failures,'consensus_applied':False,'credits_settled':False,
                'live_callback_connected':False}
