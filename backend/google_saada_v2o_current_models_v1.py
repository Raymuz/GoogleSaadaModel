"""GoogleSaada V2O — signed-in, source-verified, score-only model request gateway.

Requires fresh V2M audited history and an actual trusted auth cookie. The
three worldwide specialists may run on unseen teams; original specialists
abstain until their separate current inputs have been independently proven.
Original 13-model orchestrator remains STAGING_ONLY_NOT_RELEASED.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
import importlib.util,json,os,re,sys

APP=Path(__file__).resolve().parent.parent
V3=APP/'global_v3' if (APP/'global_v3').is_dir() else APP.parent/'global_v3'
STAGE=V3/'v2g_staging'
WORLD_ROLES=('worldwide_match_result','worldwide_over_2_5','worldwide_btts')

class EvidenceUnavailable(RuntimeError): pass

def sha(path):
    h=sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def load(path,tag):
    spec=importlib.util.spec_from_file_location(tag,str(path))
    if spec is None or spec.loader is None:raise EvidenceUnavailable('Model source unavailable')
    mod=importlib.util.module_from_spec(spec)
    sys.modules[tag]=mod
    spec.loader.exec_module(mod)
    return mod

def utc(value):
    from datetime import datetime
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if not isinstance(value,datetime) or value.tzinfo is None:raise EvidenceUnavailable('Timezone-aware UTC required')
    return value.astimezone(timezone.utc)

class PredictionEngine:
    def __init__(self, *, now=None):
        import pandas as pd
        self.now=utc(now or datetime.now(timezone.utc))
        self.prod_path=APP/'backend/google_saada_unified_model_registry.json'
        self.staged_path=STAGE/'v2g_staging_registry.json'
        self.prod_before=self.prod_path.read_bytes()
        prod=json.loads(self.prod_before)
        self.registry=json.loads(self.staged_path.read_text())
        if prod.get('live_routing_enabled') is not False or len(prod.get('original_models',{}))!=10:
            raise EvidenceUnavailable('Protected production model registry changed')
        if self.registry.get('live_routing_enabled') is not False or len(self.registry.get('selected_worldwide_models',{}))!=3:
            raise EvidenceUnavailable('Staged 13-model registry invalid')
        reports=sorted(STAGE.glob('v2m_generalized_live_staging_*.json'),key=lambda p:(p.stat().st_mtime,p.name),reverse=True)
        if not reports:raise EvidenceUnavailable('Run verified V2M source refresh first')
        report=json.loads(reports[0].read_text())
        timestamp=utc(report.get('timestamp_utc'))
        age=(self.now-timestamp).total_seconds()
        maxage=int(os.environ.get('GS_V2O_SOURCE_MAX_AGE_MINUTES','120'))*60
        if age<0 or age>maxage:
            raise EvidenceUnavailable('V2M verified data refresh expired; request not charged')
        if report.get('blockers') or not report.get('metrics',{}).get('competitions_with_complete_scored_day_fetches'):
            raise EvidenceUnavailable('V2M source history incomplete')
        metrics=report['metrics']
        self.good_leagues=set(metrics['competitions_with_complete_scored_day_fetches'])
        history_path=STAGE/Path(metrics['extended_history_file']).name
        history_hash=metrics['extended_history_sha256']
        if history_path.parent.resolve()!=STAGE.resolve() or sha(history_path)!=history_hash:
            raise EvidenceUnavailable('Verified extended history absent or changed')
        proof_reports=sorted(STAGE.glob('v2l_crosswalk_recovery_*.json'),key=lambda p:(p.stat().st_mtime,p.name),reverse=True)
        if not proof_reports:raise EvidenceUnavailable('Verified V2L mapping missing')
        reference=json.loads(proof_reports[0].read_text())
        proof_path=STAGE/Path(reference.get('crosswalk_path','')).name
        if proof_path.parent.resolve()!=STAGE.resolve() or not proof_path.is_file():
            raise EvidenceUnavailable('V2L evidence outside trusted staging directory')
        self.proof=json.loads(proof_path.read_text())
        if self.proof.get('schema')!='GS_V2J_PROVEN_CROSSWALK_V1':
            raise EvidenceUnavailable('Unrecognized V2L proof schema')
        self.staging_backend=V3/'v2i_consolidated_candidate/backend'
        self.provider=load(self.staging_backend/'google_saada_global_scores_v2.py','gs_v2o_provider')
        self.builder=load(STAGE/'google_saada_v2g_live56.py','gs_v2o_56_builder')
        self.adapters=load(self.staging_backend/'google_saada_verified_specialist_adapters_v2.py','gs_v2o_adapters')
        self.manager_mod=load(self.staging_backend/'google_saada_manager_agent_v2.py','gs_v2o_manager')
        self.orch_mod=load(self.staging_backend/'google_saada_master_orchestrator_v2.py','gs_v2o_orchestrator')
        self.feature_engine=self.builder.GlobalHistory56(history_path,expected_sha256=history_hash,
                                  feature_names=self.builder.FEATURE_NAMES,min_prior_matches=5)
        self.history_hash=history_hash
        self.models=self.adapters.VerifiedSpecialistAdapterFactory(self.staged_path)
        preflight=self.models.preflight(roles=WORLD_ROLES,load=True,retain_models=False)
        if set(preflight)!=set(WORLD_ROLES):raise EvidenceUnavailable('Worldwide trained weights failed verified preflight')
        self.model_runner=self.orch_mod.MasterOrchestratorV2(self.registry,adapters=self.models.all_adapters())
        self._daily_fetch={}
        self.pd=pd

    def qualified_id(self,comp,tid):
        info=self.proof.get('teams',{}).get(str(tid))
        if comp in self.proof.get('leagues',{}) and isinstance(info,dict):
            if len(set(info.get('provider_events',[])))>=2 and info.get('worldwide_id'):
                return str(info['worldwide_id'])
        return 'ESPN:'+comp+':'+str(tid)

    def refetch(self,row):
        key=row['key'];comp,ident=key.split('|',1)
        matches=[name for name,code in self.provider.COMPETITIONS.items() if code==comp]
        if len(matches)!=1 or matches[0] not in self.good_leagues:
            raise EvidenceUnavailable('Competition has no complete verified recent-score coverage')
        league=matches[0]
        kick=utc(row['kickoff_utc'])
        if not self.now<kick<self.now+timedelta(days=7):
            raise EvidenceUnavailable('Fixture outside strictly future 168-hour window')
        slot=(league,kick.strftime('%Y%m%d'))
        if slot not in self._daily_fetch:
            raw=self.provider._fetch(league,slot[1])
            self._daily_fetch[slot]=[self.provider._parse(v,league) for v in raw]
        hits=[ev for ev in self._daily_fetch[slot] if isinstance(ev,dict) and ev.get('key')==key]
        if len(hits)!=1:raise EvidenceUnavailable('Provider fixture ID missing or ambiguous')
        ev=hits[0]
        for field in ('home','away','kickoff_utc','league'):
            if ev.get(field)!=row.get(field):raise EvidenceUnavailable('Fixture changed since server selection')
        if ev.get('source')!='ESPN' or ev.get('phase')!='UPCOMING':
            raise EvidenceUnavailable('ESPN fixture no longer scheduled')
        for side in ('home','away'):
            tid=str(ev.get(side+'_team_id',''))
            if not re.fullmatch(r'\d{1,20}',tid):raise EvidenceUnavailable('Stable provider team ID not found')
        if str(ev['home_team_id'])==str(ev['away_team_id']):
            raise EvidenceUnavailable('Invalid provider team pairing')
        return ev

    def predict(self,selected, *, requested_markets=None):
        if not isinstance(selected,list) or not 1<=len(selected)<=16:
            raise EvidenceUnavailable('Select 1 to 16 valid matches')
        if self.now!=utc(self.now):raise EvidenceUnavailable('Invalid server UTC clock')
        fixtures=[];evidence={};abstentions=[]
        for row in selected:
            try:
                ev=self.refetch(row)
                comp=ev['source_competition_id']
                home=self.qualified_id(comp,ev['home_team_id'])
                away=self.qualified_id(comp,ev['away_team_id'])
                features=self.feature_engine.build(home_team_id=home,away_team_id=away,
                       kickoff_utc=ev['kickoff_utc'],evidence_cutoff_utc=self.now.isoformat())
                fid='ESPN|'+ev['key']
                fixture={'fixture_id':fid,'source':'ESPN_VERIFIED_SCORE_HISTORY_STAGING',
                  'source_fixture_id':str(ev['source_match_id']), 'home_id':home,'away_id':away,
                  'home':ev['home'],'away':ev['away'],'kickoff_utc':utc(ev['kickoff_utc']).isoformat(),
                  'verified':True}
                packet={'fixture_id':fid,'source_fixture_id':str(ev['source_match_id']),
                  'home_id':home,'away_id':away,'verified':True,
                  'data_source':'ESPN_SCORE_ONLY_PINNED_AND_REFRESHED:'+self.history_hash,
                  'feature_cutoff_utc':self.now.isoformat(),
                  'specialist_features':{role:dict(features) for role in WORLD_ROLES}}
                packet['evidence_id']=self.builder.evidence_digest(fixture,features,self.history_hash)
                fixtures.append(fixture);evidence[fid]=packet
            except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:
                abstentions.append({'fixture':str(row.get('key','')),'reason':type(exc).__name__})
        if not fixtures:return [],abstentions
        manager=self.manager_mod.GoogleSaadaManagerAgent(('match_result','over_2_5','btts'))
        plan=manager.create_plan([fx['fixture_id'] for fx in fixtures],['match_result','over_2_5','btts'])
        outcome=self.model_runner.execute(fixtures,evidence,now_utc=self.now.isoformat(),requested_markets=('match_result','over_2_5','btts'))
        if outcome.get('status')!='STAGING_ONLY_NOT_RELEASED' or outcome.get('credits_settled') or outcome.get('live_callback_connected'):
            raise EvidenceUnavailable('Unexpected orchestrator release or charging behavior')
        world={}
        for r in outcome['whiteboard']:
            if r.get('specialist') in WORLD_ROLES:
                world.setdefault(r['fixture_id'],{})[r['specialist']]=r['value']
        rows=[]
        for fx in fixtures:
            p=world.get(fx['fixture_id'],{})
            if set(p)!=set(WORLD_ROLES):
                abstentions.append({'fixture':fx['fixture_id'],'reason':'INCOMPLETE_MODEL_OUTPUT'})
                continue
            res,over,btts=p['worldwide_match_result'],p['worldwide_over_2_5'],p['worldwide_btts']
            if set(res)!={'H','D','A'} or set(over)!={'0','1'} or set(btts)!={'0','1'}:
                raise EvidenceUnavailable('Model returned unexpected target class mapping')
            vals=list(res.values())+list(over.values())+list(btts.values())
            import math
            if any(not math.isfinite(float(v)) or not 0<=float(v)<=1 for v in vals):
                raise EvidenceUnavailable('Model returned invalid probabilities')
            fmt=lambda n:f'{float(n)*100:.1f}%'
            winning=max(('H','D','A'),key=lambda k:res[k])
            leader={'H':'Home win','D':'Draw','A':'Away win'}[winning]
            rows.append([fx['home']+' vs '+fx['away'],fmt(res['H']),fmt(res['D']),fmt(res['A']),
                 fmt(over['1']),fmt(over['0']),fmt(btts['1']),fmt(btts['0']),
                 leader,'—','—','—','—'])
        # Never release a probability for a fixture that began while scoring.
        output_time=datetime.now(timezone.utc)
        kickoff_by_name={fx['home']+' vs '+fx['away']:utc(fx['kickoff_utc']) for fx in fixtures}
        valid=[]
        for row in rows:
            if output_time<kickoff_by_name[row[0]]:
                valid.append(row)
            else:
                abstentions.append({'fixture':row[0],'reason':'KICKOFF_BEGAN_DURING_ANALYSIS'})
        if self.prod_path.read_bytes()!=self.prod_before:raise EvidenceUnavailable('Production model registry unexpectedly changed')
        return valid,abstentions


def handle(keys,raw,profile,day,league,zone,anon_token,markets,*,request,old,week,auth,oauth_profile=None):
    disclaimer='Research predictions are uncertain and are not guaranteed outcomes.'
    def denied(message):return (message,[],disclaimer,anon_token,'Authenticated prediction allowance unavailable or unchanged.')
    try:
        principal=auth.principal_from_hf_profile(oauth_profile)
    except (auth.AccessDenied,ValueError,TypeError):
        return denied('Please sign in through the verified GoogleSaada login to analyze matches. No credits charged.')
    try:
        if not isinstance(keys,(list,tuple)) or len(keys)>16 or len(str(raw or ''))>2500:
            raise ValueError('Maximum 16 matches and 2,500 characters')
        available,start,end,errors=week.window(league,zone,old,force=True)
        if errors:raise ValueError('Fixture source coverage incomplete; no credits charged')
        chosen=week._resolve(list(keys),str(raw or '').strip(),available)
        clock=datetime.now(timezone.utc)
        if not 1<=len(chosen)<=16 or any(not clock<week._utc(r['kickoff_utc'])<clock+timedelta(days=7) for r in chosen):
            raise ValueError('Started or out-of-range match selected')
        ledger_path=os.environ.get('GS_PREDICT_LEDGER_PATH','')
        if (not ledger_path or os.environ.get('GS_PREDICT_LEDGER_PRODUCTION_READY')!='1'
            or not Path(ledger_path).is_absolute()
            or (not Path(ledger_path).as_posix().startswith('/data/')
                and os.environ.get('GS_PREDICT_SANDBOX_ONLY')!='1')):
            raise auth.AccessDenied('Authenticated persistent credit ledger has not been approved')
        ledger=auth.CreditLedger(ledger_path)
        market_selection=list(markets) if isinstance(markets,(list,tuple)) else []
        allowed={'Full-time result','Over/Under 2.5','Both Teams to Score'}
        if not market_selection or not set(market_selection).issubset(allowed):
            raise ValueError('Choose supported match-result, Over/Under, or Both Teams to Score markets; no credits charged')
        kind,rid,prior=ledger.reserve(principal,[r['key'] for r in chosen],market_selection,profile)
        if kind=='REPLAY':
            return (*prior,anon_token,f'Authenticated allowance: {ledger.remaining(principal)}/20 remaining (UTC). Previous result reused, no new charge.')
        # A reservation is never left unaccounted for after a verified failure.
        counted=0;answer=None
        try:
            engine=PredictionEngine()
            values,abstentions=engine.predict(chosen,requested_markets=market_selection)
            # Preserve only user-requested markets in the displayed output.
            for v in values:
                if 'Full-time result' not in market_selection:
                    for col in (1,2,3,8):v[col]='—'
                if 'Over/Under 2.5' not in market_selection:
                    for col in (4,5):v[col]='—'
                if 'Both Teams to Score' not in market_selection:
                    for col in (6,7):v[col]='—'
            counted=len(values)
            message=f'{counted} of {len(chosen)} requested fixtures analyzed with three actual worldwide models.'
            note=(f'{len(abstentions)} fixture(s) abstained; only successful displayed predictions are charged. '
                  'Other ten specialists were not used without independently verified current inputs. '
                  'Exact score and HT/FT were not inferred. '+disclaimer)
            answer=[message,values,note]
        except Exception:
            answer=['Verified prediction evidence is temporarily unavailable. No credits charged.',[],disclaimer]
            counted=0
        ledger.settle(rid,principal,counted,answer)
        return (*answer,anon_token,f'Authenticated allowance: {ledger.remaining(principal)}/20 remaining (UTC).')
    except (auth.AccessDenied,auth.QuotaDenied,auth.InFlight,ValueError,TypeError) as exc:
        return denied(str(exc))
    except Exception:
        return denied('Prediction settlement state requires verification. Please do not retry a different request until resolved.')
