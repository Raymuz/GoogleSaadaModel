"""Soccer AI — source-backed, research-only on-demand fixture analysis.

Provider schedule and IDs -> read-only unapproved identity proposals -> original
causal feature builder -> original SHA256-pinned ten models -> mapped 1X2 and
Poisson research markets. Never publish results; no GPT or model fitting.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import hashlib
import importlib.util
import json
import math
import re
import threading

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REPORTS = Path('/content/soccer_ai_live_staging/reports').resolve()
REGISTRIES = Path('/content/soccer_ai_live_staging/identity_registry').resolve()
HISTORY = ROOT / 'deployment_files/feature_state/matches.parquet'
EXPORT_MANIFEST = ROOT / 'deployment_files/manifest.json'
MODEL_DIR = ROOT / 'deployment_files/models'
ORIGINAL_FEATURES = ROOT / 'backend/original_safe_features.py'
RESOLVER_MODULE = ROOT / 'backend/club_identity_resolver.py'
LEAGUES = {'Premier League': 'eng.1', 'La Liga': 'esp.1', 'Bundesliga': 'ger.1'}
TIMEZONES = {'Africa/Douala','America/Chicago','UTC','Europe/London',
             'America/New_York','Europe/Paris','Africa/Lagos'}
ROLES = {
    'full_time_xgboost':36,'full_time_elo':1,'full_time_logistic':36,
    'full_time_random_forest':36,'full_time_home_goals':17,
    'full_time_away_goals':17,'first_half_home_goals':17,
    'first_half_away_goals':17,'second_half_home_goals':17,
    'second_half_away_goals':17
}
CLASSIFIERS = ('full_time_xgboost','full_time_elo','full_time_logistic','full_time_random_forest')
CATEGORIES = ('league_name','home_team_name','away_team_name')
MARKET_LABELS = {
    'FT_1X2_HOME':'Home win', 'FT_1X2_DRAW':'Draw', 'FT_1X2_AWAY':'Away win',
    'FT_OVER_2_5':'Over 2.5 goals', 'FT_UNDER_2_5':'Under 2.5 goals',
    'FT_BTTS_YES':'Both teams score — Yes', 'FT_BTTS_NO':'Both teams score — No',
}


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def imported(path,label):
    spec=importlib.util.spec_from_file_location(label,path)
    if spec is None or spec.loader is None:raise RuntimeError('Original portable module missing')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stamp_utc(text):
    t=datetime.fromisoformat(str(text).replace('Z','+00:00'))
    if t.tzinfo is None:raise ValueError('Fixture timestamp has no time zone')
    return t.astimezone(timezone.utc)


def stable_id(value):
    if value is None or pd.isna(value):return ''
    if isinstance(value,(float,np.floating)) and float(value).is_integer():
        return str(int(value))
    result=str(value).strip()
    return result[:-2] if result.endswith('.0') and result[:-2].isdigit() else result


def text_ok(value):
    return value is not None and not pd.isna(value) and bool(str(value).strip())


def latest_prior_unique(values,cutoff_day):
    past=[(day,str(name).strip()) for day,name in values
          if day<cutoff_day and text_ok(name)]
    if not past:return None
    latest=max(x[0] for x in past)
    names={name for day,name in past if day==latest}
    return next(iter(names)) if len(names)==1 else None


def parse_event(event,code):
    """Only source-provided IDs; never infer fixture status or final scores."""
    if not isinstance(event,dict):return None
    mid=str(event.get('id',''))
    if not re.fullmatch(r'\d{3,20}',mid):return None
    competitions=event.get('competitions') or []
    if not isinstance(competitions,list) or not competitions:return None
    competitors=competitions[0].get('competitors',[])
    if not isinstance(competitors,list):return None
    sides={}
    for c in competitors:
        if not isinstance(c,dict):continue
        side=c.get('homeAway')
        team=c.get('team') or {}
        if side in ('home','away') and side not in sides and isinstance(team,dict):
            name=team.get('displayName')
            team_id=str(team.get('id','')).strip()
            if not isinstance(name,str) or not name.strip() or not re.fullmatch(r'\d{1,20}',team_id):return None
            sides[side]={'name':name.strip(),'team_id':team_id}
    if set(sides)!={'home','away'}:return None
    kickoff=stamp_utc(event.get('date'))
    status=((event.get('status') or {}).get('type') or {})
    state=str(status.get('state','')).lower()
    label=str(status.get('name','')).upper()
    completed=status.get('completed') is True
    if completed or state!='pre' or label not in {'','STATUS_SCHEDULED','STATUS_PRE_GAME','STATUS_CREATED'}:
        phase='NOT_UPCOMING'
    else:phase='UPCOMING'
    return {'key':code+'|'+mid,'source':'ESPN','source_competition_id':code,
            'source_match_id':mid,'league':next(n for n,v in LEAGUES.items() if v==code),
            'home':sides['home']['name'],'away':sides['away']['name'],
            'home_team_id':sides['home']['team_id'],'away_team_id':sides['away']['team_id'],
            'kickoff_utc':kickoff.isoformat(),'phase':phase}


def download_day(code,utc_day):
    if code not in LEAGUES.values() or not re.fullmatch(r'\d{8}',utc_day):
        raise ValueError('Unsupported provider request')
    url=('https://site.api.espn.com/apis/site/v2/sports/soccer/'+code+
         '/scoreboard?'+urlencode({'dates':utc_day,'limit':500}))
    req=Request(url,headers={'User-Agent':'Soccer-AI-Research/1.0','Accept':'application/json'})
    with urlopen(req,timeout=12) as resp:
        if resp.status!=200:raise RuntimeError('Source provider unavailable')
        data=resp.read(5*1024*1024+1)
    if len(data)>5*1024*1024:raise RuntimeError('Oversized provider fixture payload')
    answer=json.loads(data.decode('utf-8'))
    if not isinstance(answer,dict) or not isinstance(answer.get('events'),list):
        raise RuntimeError('Provider fixture response schema mismatch')
    return answer['events']


def discover(day,league='All supported leagues',time_zone='Africa/Douala',transport=None):
    """Refresh requested local calendar day, fail-closed on partial API errors."""
    if league!='All supported leagues' and league not in LEAGUES:raise ValueError('Unsupported league')
    if time_zone not in TIMEZONES:raise ValueError('Unsupported time zone')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(day)):raise ValueError('Invalid date')
    local_day=date.fromisoformat(day)
    tz=ZoneInfo(time_zone)
    codes=[LEAGUES[league]] if league in LEAGUES else list(LEAGUES.values())
    days=[(local_day+timedelta(days=i)).strftime('%Y%m%d') for i in (-1,0,1)]
    jobs=[(code,ymd) for code in codes for ymd in days]
    fetch=transport or download_day
    results={};errors=[]
    with ThreadPoolExecutor(max_workers=min(6,len(jobs))) as pool:
        futures={pool.submit(fetch,c,d):(c,d) for c,d in jobs}
        for future in as_completed(futures):
            code,day_str=futures[future]
            try:
                for raw in future.result():
                    item=parse_event(raw,code)
                    if item is None:continue
                    if stamp_utc(item['kickoff_utc']).astimezone(tz).date()!=local_day:continue
                    key=item['key']
                    # Provider can repeat the same fixture in ±UTC day results.
                    if key in results and results[key]!=item:
                        raise RuntimeError('Conflicting source fixture')
                    results[key]=item
            except Exception as exc:
                errors.append(f'{code}/{day_str}: {type(exc).__name__}')
    if errors:raise RuntimeError('Provider schedule incomplete; retry after availability is restored: '+', '.join(errors))
    return sorted(results.values(),key=lambda r:(r['kickoff_utc'],r['key']))


def pmf(lam):
    if isinstance(lam,bool) or not np.isfinite(lam) or not 0<=float(lam)<=25:
        raise ValueError('Invalid Poisson expected goals')
    lam=float(lam)
    p=math.exp(-lam)
    out=[p]
    for goal in range(1,121):
        p *= lam/goal
        out.append(p)
    if sum(out)<1-1e-9:raise ValueError('Poisson probability grid truncated')
    return out


def poisson_markets(home,away):
    h=pmf(home);a=pmf(away)
    w=d=l=0.
    for i,ph in enumerate(h):
        for j,pa in enumerate(a):
            joint=ph*pa
            if i>j:w+=joint
            elif i==j:d+=joint
            else:l+=joint
    if abs(w+d+l-1)>1e-8:raise ValueError('Poisson total probability invalid')
    total=float(home)+float(away)
    under=math.exp(-total)*(1+total+total*total/2)
    btts=(1-math.exp(-home))*(1-math.exp(-away))
    return {'home_win':w,'draw':d,'away_win':l,'under_2_5':under,
            'over_2_5':1-under,'btts_yes':btts,'btts_no':1-btts}


def manifest_ensemble_weights(manifest):
    sections=[]
    initial=manifest.get('ensemble')
    if isinstance(initial,dict):
        sections.append(initial)
        sections.extend(v for v in initial.values() if isinstance(v,dict))
    choices=set()
    for section in sections:
        for a,b in (('xgboost_weight','poisson_weight'),('xgb_weight','poisson_weight'),
                    ('xgboost','poisson'),('xgb','poisson')):
            x,y=section.get(a),section.get(b)
            if isinstance(x,(int,float)) and not isinstance(x,bool) and isinstance(y,(int,float)) and not isinstance(y,bool):
                if math.isfinite(x) and math.isfinite(y) and x>=0 and y>=0 and abs(x+y-1)<1e-8:
                    choices.add((round(float(x),12),round(float(y),12)))
    if len(choices)!=1:raise RuntimeError('Ensemble weights unproven or conflicting')
    return next(iter(choices))


class DynamicResearchInference:
    """Use saved model artifacts, original features, and proposed IDs in research only."""
    def __init__(self):
        self.root=ROOT
        self.history_path=HISTORY
        self.models_meta=read_json(EXPORT_MANIFEST)
        if self.models_meta.get('production_predictions_enabled') is not False:
            raise RuntimeError('Unexpected production prediction flag')
        if set(self.models_meta.get('models',{}))!=set(ROLES):
            raise RuntimeError('Ten original model roles are not preserved')
        if int(self.models_meta.get('exported_model_count',-1))!=10:
            raise RuntimeError('Ten original serialized models not present')
        self.models={}
        for role,count in ROLES.items():
            spec=self.models_meta['models'][role]
            p=(ROOT / str(spec.get('deployment_path',''))).resolve()
            if (p.parent!=MODEL_DIR.resolve() or p.name!=spec.get('filename')
                    or spec.get('status')!='EXPORTED_AND_VERIFIED'
                    or sha(p)!=spec.get('source_sha256')
                    or len(spec.get('feature_names_in') or [])!=count
                    or int(spec.get('n_features_in',-1))!=count):
                raise RuntimeError('Model checksum or input contract invalid: '+role)
            self.models[role]=p
        self.verified_model_hashes={role:sha(p) for role,p in self.models.items()}
        self.history_hash=sha(HISTORY)
        self.feature_module=imported(ORIGINAL_FEATURES,'soccer_original_safe_features_13b2')
        if not callable(getattr(self.feature_module,'build_safe_soccer_features',None)):
            raise RuntimeError('Original feature builder unavailable')
        self.resolver_module=imported(RESOLVER_MODULE,'soccer_resolver_13b2')
        registry_files=sorted(REPORTS.glob('step12b1a_manifest_*.json'))
        if not registry_files:raise RuntimeError('Step 12B.1A identity registry manifest unavailable')
        original=read_json(registry_files[-1])
        registry=Path(original['sqlite_file']).resolve()
        if (registry.parent!=REGISTRIES or sha(registry)!=original['sqlite_sha256']
                or self.history_hash!=original['historical_parquet_sha256']
                or original.get('production_predictions_enabled') is not False
                or original.get('provider_id_mappings_approved')!=0
                or original.get('league_mappings_approved')!=0):
            raise RuntimeError('Frozen-history identity evidence unexpectedly changed')
        self.registry_path=registry
        self.registry_hash=sha(registry)
        proofs=sorted(REPORTS.glob('step12b3b1_manifest_*.json'))
        if not proofs:raise RuntimeError('Original fitted training class evidence is unavailable')
        proof=read_json(proofs[-1])
        pinned=Path(proof['results_json']).resolve()
        if (pinned.parent!=REPORTS or sha(pinned)!=proof['results_sha256']
                or proof.get('original_training_label_mapping_proven') is not True
                or proof.get('production_predictions_enabled') is not False):
            raise RuntimeError('Original training-label mapping proof is not valid')
        source_proof=read_json(pinned)
        if (source_proof.get('training_label_evidence_status')
                !='PASS_OBSERVED_TRAINING_TARGET_AND_DESIGN_ALIGNED'
                or source_proof.get('accepted_training_to_outcome_mapping_if_proven')
                !={'0':'AWAY','1':'DRAW','2':'HOME'}):
            raise RuntimeError('Fitted classifier semantics not proven')
        self.class_mapping={'0':'AWAY','1':'DRAW','2':'HOME'}
        self.xweight,self.pweight=manifest_ensemble_weights(self.models_meta)
        self._lock=threading.Lock()
        self.history=pd.read_parquet(HISTORY)
        for col in ('match_id','date_utc','league_id','home_team_key','away_team_key',
                    'is_played','home_goals','away_goals',*CATEGORIES):
            if col not in self.history.columns:raise RuntimeError('Original history missing: '+col)
        self.history['match_id']=self.history['match_id'].astype(str)
        if self.history['match_id'].duplicated().any():raise RuntimeError('Historical match IDs repeated')
        self.history['date_utc']=pd.to_datetime(self.history['date_utc'],utc=True,errors='raise')
        self.history['league_id']=self.history['league_id'].map(stable_id)
        self.history['home_team_key']=self.history['home_team_key'].map(stable_id)
        self.history['away_team_key']=self.history['away_team_key'].map(stable_id)
        self.played=self.history.loc[self.history['is_played'].fillna(False).eq(True)].copy()
        if self.played.empty:raise RuntimeError('Original historical outcomes unavailable')
        self.latest_history=self.played['date_utc'].max().isoformat()
        self._labels_league=defaultdict(list)
        self._labels_sides=defaultdict(list)
        for row in self.played[['date_utc','league_id','home_team_key','away_team_key',
                                'league_name','home_team_name','away_team_name']].itertuples(index=False):
            day=row.date_utc.floor('D')
            if text_ok(row.league_name):self._labels_league[row.league_id].append((day,row.league_name))
            if text_ok(row.home_team_name):
                self._labels_sides[(row.league_id,row.home_team_key,'home')].append((day,row.home_team_name))
            if text_ok(row.away_team_name):
                self._labels_sides[(row.league_id,row.away_team_key,'away')].append((day,row.away_team_name))

    def _eligible(self,fixture,resolver):
        if fixture['phase']!='UPCOMING' or stamp_utc(fixture['kickoff_utc'])<=datetime.now(timezone.utc):
            return None,'HOLD_NOT_UPCOMING'
        # ESPN team IDs are required; no fuzzy auto-approval or guesswork.
        pair=resolver.resolve_fixture('ESPN',fixture['source_competition_id'],
            fixture['home_team_id'],fixture['home'],fixture['away_team_id'],fixture['away'])
        home,away=pair['home'],pair['away']
        if pair['provider_id_collision'] or pair['top_candidate_collision']:
            return None,'HOLD_IDENTITY_COLLISION'
        for side in (home,away):
            if (side.get('known_provider_id') is not True
                or side['status']!='EXACT_HISTORICAL_CANDIDATE_NOT_APPROVED'
                or side['candidate_count_total']!=1):
                return None,'HOLD_TEAM_IDENTITY_UNRESOLVED'
        lc=home.get('proposed_historical_league_id')
        hc,ac=home['candidates'][0],away['candidates'][0]
        if not lc or stable_id(lc)!=stable_id(away.get('proposed_historical_league_id')):
            return None,'HOLD_LEAGUE_IDENTITY_UNRESOLVED'
        if any(stable_id(v['historical_league_id'])!=stable_id(lc) for v in (hc,ac)):
            return None,'HOLD_LEAGUE_TEAM_MISMATCH'
        if hc['canonical_id']==ac['canonical_id']:
            return None,'HOLD_IDENTICAL_HISTORICAL_TEAMS'
        return {'league_id':stable_id(lc),
                'home_team_key':stable_id(hc['historical_team_key']),
                'away_team_key':stable_id(ac['historical_team_key'])},'RESEARCH_EXACT_CANDIDATE_UNAPPROVED'

    def analyze(self,fixtures):
        if not isinstance(fixtures,list):raise ValueError('Provide source-discovered fixtures as a list')
        if sha(HISTORY)!=self.history_hash or sha(self.registry_path)!=self.registry_hash:
            raise RuntimeError('Research history or identity registry has changed')
        checks={}
        for role,path in self.models.items():
            if sha(path)!=self.verified_model_hashes[role]:
                raise RuntimeError('Original trained model hash changed')
        if len({f.get('key') for f in fixtures})!=len(fixtures):
            raise ValueError('Duplicate provider fixture keys')
        eligible=[]
        with self.resolver_module.ClubIdentityResolver(self.registry_path) as resolver:
            for item in fixtures:
                required={'key','source','source_competition_id','source_match_id','home','away',
                          'home_team_id','away_team_id','kickoff_utc','phase'}
                if not isinstance(item,dict) or not required.issubset(item):
                    raise ValueError('Incomplete source fixture evidence')
                if (item['source']!='ESPN' or item['source_competition_id'] not in LEAGUES.values()
                        or item['key']!=item['source_competition_id']+'|'+item['source_match_id']):
                    raise ValueError('Invalid provider fixture identity')
                proposal,status=self._eligible(item,resolver)
                checks[item['key']]={'key':item['key'],'home':item['home'],'away':item['away'],
                    'kickoff_utc':item['kickoff_utc'],'status':status,
                    'market_probabilities':None,'identity_approved':False,
                    'history_up_to_date':False,'calibration_verified':False,
                    'production_prediction_authorized':False}
                if proposal:
                    cutoff=pd.Timestamp(stamp_utc(item['kickoff_utc'])).floor('D')
                    names={
                        'league_name':latest_prior_unique(self._labels_league[proposal['league_id']],cutoff),
                        'home_team_name':latest_prior_unique(self._labels_sides[
                            (proposal['league_id'],proposal['home_team_key'],'home')],cutoff),
                        'away_team_name':latest_prior_unique(self._labels_sides[
                            (proposal['league_id'],proposal['away_team_key'],'away')],cutoff),
                    }
                    if not all(names.values()):
                        checks[item['key']]['status']='HOLD_HISTORICAL_CATEGORY_LABEL'
                    else:
                        eligible.append((item,proposal,names))

        if not eligible:return [checks[f['key']] for f in fixtures]
        # Run original causal builder ONCE across all eligible fixtures.
        aug=[];crosswalk={}
        original_ids=set(self.history['match_id'])
        for fixture,proposal,names in eligible:
            unique='ESPN|'+fixture['source_competition_id']+'|'+fixture['source_match_id']
            rowid='research:13b2:'+hashlib.sha256(unique.encode()).hexdigest()[:22]
            if rowid in original_ids or rowid in crosswalk:raise RuntimeError('Feature ID collision')
            crosswalk[rowid]=(fixture,proposal,names)
            row={col:np.nan for col in self.history.columns}
            row.update({'match_id':rowid,'date_utc':pd.Timestamp(stamp_utc(fixture['kickoff_utc'])),
                'league_id':proposal['league_id'],'home_team_key':proposal['home_team_key'],
                'away_team_key':proposal['away_team_key'],'is_played':False,
                'home_goals':np.nan,'away_goals':np.nan,**names})
            aug.append(row)
        augmented=pd.concat([self.history,pd.DataFrame(aug,columns=self.history.columns)],ignore_index=True)
        with self._lock: # avoid running duplicate ~367k history rebuilds simultaneously
            frame=self.feature_module.build_safe_soccer_features(augmented)
        projected=frame.loc[frame['match_id'].astype(str).isin(crosswalk)].copy()
        if len(projected)!=len(crosswalk) or projected['match_id'].astype(str).duplicated().any():
            raise RuntimeError('Original feature builder lost or duplicated a fixture')
        for col in ('home_history_day','away_history_day','league_history_day'):
            if col in projected:
                mask=projected[col].isna() | (projected[col]<projected['match_day'])
                if not bool(mask.all()):raise RuntimeError('Causal history violation: '+col)
        for role in CATEGORIES:
            if role not in projected:projected[role]=pd.Series(pd.NA,index=projected.index,dtype='string')
        for index,row in projected.iterrows():
            fixture,proposal,names=crosswalk[str(row['match_id'])]
            for cat,label in names.items():projected.at[index,cat]=label
            if stable_id(row['league_id'])!=proposal['league_id'] or (
                abs((pd.Timestamp(row['date_utc'])-pd.Timestamp(fixture['kickoff_utc'])).total_seconds())>1):
                raise RuntimeError('Historical feature row mismatch')
        projected=projected.set_index('match_id',drop=False)
        if set(projected.index)!=set(crosswalk):raise RuntimeError('Fixture-to-feature join mismatch')

        raw={key:{} for key in crosswalk}
        for role in ROLES:
            cols=self.models_meta['models'][role]['feature_names_in']
            if any(c not in projected.columns for c in cols):
                raise RuntimeError('Missing trained model feature: '+role)
            X=projected.loc[:,cols].copy()
            for col in cols:
                if col in CATEGORIES:
                    if X[col].isna().any() or X[col].astype(str).str.strip().eq('').any():
                        raise RuntimeError('Missing historical category')
                else:
                    numeric=pd.to_numeric(X[col],errors='coerce').to_numpy(dtype=float)
                    if np.isinf(numeric).any():raise RuntimeError('Infinite input feature')
            model=joblib.load(self.models[role])  # SHA256-verified project-owned artifact only
            if role in CLASSIFIERS:
                labels=[]
                for x in model.classes_:
                    token=str(x).strip()
                    if token.endswith('.0') and token[:-2].isdigit():token=token[:-2]
                    labels.append(token)
                if set(labels)!=set(self.class_mapping):raise RuntimeError('Fitted class order invalid')
                prediction=np.asarray(model.predict_proba(X),dtype=float)
                if (prediction.shape!=(len(X),3) or not np.isfinite(prediction).all()
                        or np.any(prediction<0) or np.any(prediction>1)
                        or not np.allclose(prediction.sum(axis=1),1,atol=1e-5)):
                    raise RuntimeError('Model probability validation failed: '+role)
                for rowid,vector in zip(projected.index,prediction):
                    raw[rowid][role]={self.class_mapping[c]:float(p) for c,p in zip(labels,vector)}
            else:
                prediction=np.asarray(model.predict(X),dtype=float).reshape(-1)
                if (len(prediction)!=len(X) or not np.isfinite(prediction).all()
                        or np.any(prediction<0) or np.any(prediction>25)):
                    raise RuntimeError('Goal estimate outside safe research range: '+role)
                for rowid,value in zip(projected.index,prediction):raw[rowid][role]=float(value)

        for rowid,(fixture,_,_) in crosswalk.items():
            result=checks[fixture['key']]
            if stamp_utc(fixture['kickoff_utc'])<=datetime.now(timezone.utc):
                result['status']='HOLD_KICKOFF_PASSED_DURING_COMPUTE'
                continue
            r=raw[rowid]
            full=poisson_markets(r['full_time_home_goals'],r['full_time_away_goals'])
            mix={key:self.xweight*r['full_time_xgboost'][cls]+self.pweight*full[key]
                 for key,cls in [('home_win','HOME'),('draw','DRAW'),('away_win','AWAY')]}
            if abs(sum(mix.values())-1)>1e-8:raise RuntimeError('Ensemble 1X2 mass failed')
            markets={'FT_1X2_HOME':mix['home_win'],'FT_1X2_DRAW':mix['draw'],
                'FT_1X2_AWAY':mix['away_win'],'FT_OVER_2_5':full['over_2_5'],
                'FT_UNDER_2_5':full['under_2_5'],'FT_BTTS_YES':full['btts_yes'],
                'FT_BTTS_NO':full['btts_no']}
            if any(not math.isfinite(v) or v<0 or v>1 for v in markets.values()):
                raise RuntimeError('Invalid research market probability')
            for a,b in [('FT_OVER_2_5','FT_UNDER_2_5'),('FT_BTTS_YES','FT_BTTS_NO')]:
                if abs(markets[a]+markets[b]-1)>1e-8:raise RuntimeError('Invalid complementary markets')
            result['status']='UNAPPROVED_FROZEN_HISTORY_RESEARCH_ESTIMATE'
            result['market_probabilities']=markets
            result['frozen_history_last_completed_utc']=self.latest_history
        if sha(HISTORY)!=self.history_hash or sha(self.registry_path)!=self.registry_hash:
            raise RuntimeError('Original historical data or identity evidence changed')
        return [checks[f['key']] for f in fixtures]

    def day(self,day,league='All supported leagues',time_zone='Africa/Douala',transport=None):
        """Only currently scheduled, future fixtures. No completed-game predictions."""
        source=discover(day,league,time_zone,transport)
        upcoming=[r for r in source if r['phase']=='UPCOMING'
                  and stamp_utc(r['kickoff_utc'])>datetime.now(timezone.utc)]
        results=self.analyze(upcoming)
        return {'date':day,'league':league,'time_zone':time_zone,
                'source_upcoming_fixture_count':len(upcoming),'research_results':results,
                'status_counts':dict(Counter(r['status'] for r in results)),
                'research_only':True,'historical_results_refreshed':False,
                'club_identities_approved':False,'market_calibration_verified':False,
                'production_predictions_enabled':False,'public_betting_picks':[],
                'original_history_last_completed_utc':self.latest_history}
