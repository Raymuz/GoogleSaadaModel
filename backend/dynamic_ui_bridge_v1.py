"""Soccer AI private dynamic prediction -> Gradio UI bridge.

The only accepted probabilities are from the original SHA-verified ten fitted
models with causal features and unapproved, request-only exact club evidence.
The current ESPN schedule is cross-checked on EVERY interaction; short cache
only saves expensive repeat feature computation, never allows expired fixtures.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
import copy
import hashlib
import importlib.util
import json
import math
import re
import time

ROOT = Path(__file__).resolve().parent.parent
INFERENCE_PATH = ROOT / 'backend/dynamic_research_provider_name_evidence_v2_mass_safe.py'
RISKS_PATH = ROOT / 'app_ui/config/risk_profiles.json'
RISK_ROUTER_PATH = ROOT / 'backend/research_risk_router.py'
ALLOWED_MARKETS = {
    'FT_1X2_HOME':'Home win', 'FT_1X2_DRAW':'Draw', 'FT_1X2_AWAY':'Away win',
    'FT_OVER_2_5':'Over 2.5 goals', 'FT_UNDER_2_5':'Under 2.5 goals',
    'FT_BTTS_YES':'Both teams score — Yes',
    'FT_BTTS_NO':'Both teams score — No'
}
OK_STATUS = 'UNAPPROVED_FROZEN_HISTORY_RESEARCH_ESTIMATE'
NOTICE = ('Experimental model estimates based on older historical data; '
          'not calibrated or verified betting recommendations.')


def load_module(path, name):
    if not Path(path).is_file():
        raise FileNotFoundError('Required existing backend file is absent')
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to import existing backend')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_saved_risks():
    if not RISKS_PATH.is_file():
        raise RuntimeError('The saved risk profile configuration is unavailable')
    payload = json.loads(RISKS_PATH.read_text(encoding='utf-8'))
    router = load_module(RISK_ROUTER_PATH, 'soccer_risk_saved_parse_dynamic_ui')
    levels, status = router.read_risk_profiles(payload)
    if status != 'SAVED_STEP10D_RISK_PROFILES_PARSED' or set(levels or {}) != {
        'Low','Medium','High','Balanced'}:
        raise RuntimeError('Saved risk profiles have changed or cannot be verified')
    return levels


def valid_market_probabilities(values):
    if not isinstance(values, dict) or set(values) != set(ALLOWED_MARKETS):
        raise RuntimeError('Unrecognized original research market schema')
    numeric = {}
    for key, value in values.items():
        if isinstance(value,bool) or not isinstance(value,(int,float)):
            raise RuntimeError('Non-numeric model probability')
        v = float(value)
        if not math.isfinite(v) or v < 0 or v > 1:
            raise RuntimeError('Invalid model probability')
        numeric[key] = v
    if abs(sum(numeric[k] for k in ('FT_1X2_HOME','FT_1X2_DRAW','FT_1X2_AWAY')) - 1) > 1e-8:
        raise RuntimeError('Invalid model 1X2 total probability')
    for a,b in (('FT_OVER_2_5','FT_UNDER_2_5'),('FT_BTTS_YES','FT_BTTS_NO')):
        if abs(numeric[a]+numeric[b]-1) > 1e-8:
            raise RuntimeError('Invalid complementary market probability')
    return numeric


def user_hold(status):
    # No raw internal status codes, JSON, prompt state, or debug data in UI.
    if status in ('HOLD_EXACT_IDENTITY_EVIDENCE_INCOMPLETE','HOLD_TEAM_IDENTITY_UNRESOLVED'):
        return 'club identity could not be verified from the available history'
    if status == 'HOLD_KICKOFF_PASSED_DURING_COMPUTE':
        return 'the match has already started'
    if status in ('HOLD_HISTORICAL_CATEGORY_LABEL','HOLD_NO_PRIOR_COMPLETED_MATCH_SIDE_LABEL'):
        return 'insufficient historical club data'
    return 'the match is not eligible for analysis with the current data'


class DynamicResearchUIBridge:
    def __init__(self, saved_ui_backend, service_factory=None, risk_levels=None):
        self.base = saved_ui_backend
        self._factory = service_factory
        self._engine = None
        self._lock = RLock()
        self._cache = {}
        self._cache_seconds = 120
        self._levels = risk_levels if risk_levels is not None else read_saved_risks()
        if set(self._levels) != {'Low','Medium','High','Balanced'}:
            raise RuntimeError('Unexpected risk profile names')
        if (getattr(self.base,'ALL_LEAGUES',None) != 'All supported leagues'
                or set(self.base.LEAGUES) != {'Premier League','La Liga','Bundesliga'}):
            raise RuntimeError('Existing fixture UI compatibility changed')

    def _get_engine(self):
        if self._engine is None:
            if self._factory is not None:
                self._engine = self._factory()
            else:
                module = load_module(INFERENCE_PATH,'soccer_dynamic_mass_safe_ui_bridge_v1')
                self._engine = module.ProviderNameEvidenceResearch()
        return self._engine

    def _scope(self, day, league, zone):
        if not isinstance(day,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):
            raise ValueError('Invalid match date')
        datetime.strptime(day,'%Y-%m-%d')
        if league != self.base.ALL_LEAGUES and league not in self.base.LEAGUES:
            raise ValueError('Unsupported competition')
        if zone not in self.base.TZ_CHOICES:
            raise ValueError('Unsupported time zone')
        return (day,league,zone)

    def _current(self, day, league, zone):
        # This ALWAYS re-reads the source, even when expensive model features
        # are cached. It prevents stale/started fixtures from receiving results.
        items = self.base.DAILY.day(day,league,zone,force=True)
        now = datetime.now(timezone.utc)
        return {r['key']:r for r in items if r['phase']=='UPCOMING'
                and self.base.utc(r['kickoff_utc'])>now}

    def _research_day(self, day, league, zone):
        key = self._scope(day,league,zone)
        with self._lock:
            item = self._cache.get(key)
            now = time.monotonic()
            if item and now-item[0] < self._cache_seconds:
                return copy.deepcopy(item[1])
            outcome = self._get_engine().day(day,league,zone)
            if not isinstance(outcome,dict) or any(outcome.get(k) is not False for k in
                    ('production_predictions_enabled','club_identities_approved',
                     'market_calibration_verified','historical_results_refreshed')):
                raise RuntimeError('Research evidence authorization gate failed')
            if outcome.get('public_betting_picks') != []:
                raise RuntimeError('Unsafe research publication field')
            rows = outcome.get('research_results')
            if not isinstance(rows,list) or len({r.get('key') for r in rows}) != len(rows):
                raise RuntimeError('Incomplete or duplicate research fixture keys')
            self._cache[key] = (now, copy.deepcopy(outcome))
            return copy.deepcopy(outcome)

    def _checked(self, research, fresh):
        # The independently fetched current-source status and identity must
        # match the record on which the ten models actually ran.
        if not isinstance(research,dict):
            return False
        key = research.get('key')
        if not isinstance(key,str) or fresh is None or fresh['key'] != key:
            return False
        if self.base.norm(fresh['home']) != self.base.norm(research.get('home','')):
            return False
        if self.base.norm(fresh['away']) != self.base.norm(research.get('away','')):
            return False
        try:
            date_drift = abs((self.base.utc(fresh['kickoff_utc']) -
                              self.base.utc(research['kickoff_utc'])).total_seconds())
        except (ValueError,KeyError,TypeError):
            return False
        if date_drift>120 or self.base.utc(fresh['kickoff_utc'])<=datetime.now(timezone.utc):
            return False
        if any(research.get(flag) is not False for flag in
               ('identity_approved','history_up_to_date','calibration_verified',
                'production_prediction_authorized')):
            raise RuntimeError('Research result has unexpected authorization flags')
        return True

    def _evaluations(self, day, league, zone):
        self._scope(day,league,zone)
        # Compute first, then independently recheck live provider status.
        research = self._research_day(day,league,zone)
        current = self._current(day,league,zone)
        result = {}
        for row in research['research_results']:
            if not isinstance(row,dict):
                raise RuntimeError('Malformed model research result')
            key = row.get('key')
            if key in result:
                raise RuntimeError('Duplicate model result')
            if not self._checked(row,current.get(key)):
                continue
            item = dict(row)
            if item.get('status')==OK_STATUS:
                item['market_probabilities'] = valid_market_probabilities(item.get('market_probabilities'))
            else:
                if item.get('market_probabilities') is not None:
                    raise RuntimeError('Held match unexpectedly contains model estimates')
            result[key] = item
        return result,current

    def predictions(self, keys, free_text, profile, day, league, zone):
        if profile not in self._levels:
            return 'Select a valid analysis preference.',[],NOTICE
        try:
            self._scope(day,league,zone)
            # Resolve human input against current source: no source IDs or clubs
            # invented by user text and no hard match-count limit.
            latest = self._current(day,league,zone)
            selected,manual_holds = self.base.fresh_matching(
                list(latest.values()), keys or [], free_text or '')
            if not selected:
                return ('No eligible upcoming matches were selected.',[],
                        ('\n'.join(manual_holds)+'\n\n' if manual_holds else '')+NOTICE)
            assessed,current = self._evaluations(day,league,zone)
            rows=[]; holds=list(manual_holds); completed=0
            for item in selected:
                source_now = current.get(item['key'])
                evidence = assessed.get(item['key'])
                match = item['home']+' vs '+item['away']
                if source_now is None or evidence is None:
                    holds.append(match+' — unavailable or already started')
                    continue
                if evidence['status']!=OK_STATUS:
                    holds.append(match+' — '+user_hold(evidence['status']))
                    continue
                completed += 1
                for code,label in ALLOWED_MARKETS.items():
                    rows.append([match,label,f"{evidence['market_probabilities'][code]:.1%}"])
            text=f'**{completed} of {len(selected)} selected matches analyzed.**'
            if holds: text+=f' {len(holds)} unavailable requests.'
            notes='\n'.join('• '+note for note in holds)
            return text,rows,(notes+'\n\n' if notes else '')+NOTICE
        except (ValueError,RuntimeError,KeyError,TypeError,OSError):
            return 'Match analysis is unavailable right now. Try refreshing the date and fixtures.',[],NOTICE

    def _research_recommendations(self, profile, day, league, zone,
                                  required_minimum=None, requested_count=None):
        if profile not in self._levels:
            return 'Select a valid risk preference.',[],NOTICE
        try:
            research,current = self._evaluations(day,league,zone)
            threshold=float(self._levels[profile])
            if required_minimum is not None:
                minimum=float(required_minimum)
                if not math.isfinite(minimum) or not 0<=minimum<=1:
                    raise ValueError('Unsupported requested probability threshold')
                threshold=max(threshold,minimum)
            candidates=[]
            for item in research.values():
                if item['status']!=OK_STATUS:
                    continue
                options=item['market_probabilities']
                best_code=max(ALLOWED_MARKETS,key=lambda c:(options[c],c))
                p=options[best_code]
                if p+1e-12>=threshold:
                    candidates.append((p,item['home']+' vs '+item['away'],best_code))
            candidates.sort(key=lambda x:(-x[0],x[1],x[2]))
            if requested_count is not None:
                if isinstance(requested_count,bool) or not isinstance(requested_count,int) or requested_count<1:
                    raise ValueError('Invalid requested count')
                candidates=candidates[:requested_count]
            table=[[name,ALLOWED_MARKETS[code],f'{p:.1%}'] for p,name,code in candidates]
            count=len(table)
            if count:
                text=f'**{count} matches meet the {threshold:.0%} estimated-probability filter.**'
            else:
                text=f'No eligible upcoming matches meet the {threshold:.0%} filter.'
            if requested_count is not None:
                text+=f' Requested up to {requested_count}; fewer results are possible.'
            return text,table,NOTICE
        except (ValueError,RuntimeError,KeyError,TypeError,OSError):
            return 'Match recommendations are temporarily unavailable.',[],NOTICE

    def recommendations(self, profile, day, league, zone):
        # User may receive any number of qualifying games; no five-match cap.
        return self._research_recommendations(profile,day,league,zone)

    def predictions_or_recommendations(self, keys, text, profile, day, league,
                                      zone, parse_prompt):
        parsed = parse_prompt(text)
        if parsed is None:
            return self.predictions(keys,text,profile,day,league,zone)
        if keys:
            return ('Choose either a recommendation request or selected games.',[],
                    'Clear selected games to request recommendations. '+NOTICE)
        selected = parsed.get('explicit_risk_profile') or profile
        return self._research_recommendations(
            selected,day,league,zone,
            required_minimum=parsed.get('min_probability'),
            requested_count=parsed.get('count'))

    def assistant_request(self, prompt, profile, day, league, zone):
        import re as _re
        raw=str(prompt or '').strip()
        if not raw or len(raw)>1000:
            return 'Enter a shorter question about scheduled matches.',[],NOTICE
        # Bounded deterministic offline assistant. No GPT call.
        detected=_re.search(r'\b(low|medium|high|balanced)\s+risk\b',raw,_re.I)
        risk=detected.group(1).capitalize() if detected else profile
        value=_re.search(r'(?<!\d)(\d{1,3}(?:\.\d+)?)\s*%',raw)
        threshold=float(value.group(1))/100 if value else None
        count=_re.search(r'\b(?:top\s+|up to\s+)?(\d{1,5})\s+(?:matches|games|fixtures)\b',raw,_re.I)
        limit=int(count.group(1)) if count else None
        if _re.search(r'\b(recommend|suggest|find|top|high probability|probabilities)\b',raw,_re.I):
            return self._research_recommendations(risk,day,league,zone,threshold,limit)
        if _re.search(r'\b\w.+\s+vs\.?\s+\w.+',raw,_re.I):
            return self.predictions([],raw,risk,day,league,zone)
        return ('Try a question such as “Recommend 5 matches above 75%” '
                'or enter a match as “Home Team vs Away Team”.',[],NOTICE)
