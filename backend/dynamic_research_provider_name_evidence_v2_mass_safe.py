"""Provider-observed display & short names -> unique EXACT historic club alias.

No generated synonym, fuzzy approval, permanent provider ID association, or
unknown current names are used to call the original ten fitted research models.
The current ESPN scoreboard source is fetched afresh for the requested date.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo
from difflib import SequenceMatcher
import importlib.util
import re

HERE = Path(__file__).resolve().parent
BASE_FILE = HERE / 'dynamic_research_inference_v2_mass_safe.py'
PARENT_FILE = HERE / 'dynamic_research_identity_adapter_v2_mass_safe.py'
if not BASE_FILE.is_file() or not PARENT_FILE.is_file():
    raise RuntimeError('Original research inference/identity modules unavailable')

def import_local(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

BASE = import_local(BASE_FILE, 'soccer_on_demand_source_base_13b22')
PARENT = import_local(PARENT_FILE, 'soccer_strict_identity_parent_13b22')


def names_from_same_espn_team(raw_event, fixture):
    """Take only publisher-provided names from the SAME numerical team ID.

    name, shortDisplayName and displayName are ESPN team fields. Never treat
    user-entered names, abbreviations, or generated nicknames as evidence.
    """
    competitions = raw_event.get('competitions') or []
    if not isinstance(competitions, list) or not competitions:
        raise ValueError('ESPN event has no competition evidence')
    raw_teams = competitions[0].get('competitors') or []
    if not isinstance(raw_teams, list):
        raise ValueError('Invalid team list')
    output = {}
    for side in ('home', 'away'):
        candidates = [c for c in raw_teams if isinstance(c, dict) and c.get('homeAway') == side]
        if len(candidates) != 1:
            raise ValueError('Duplicate/missing ESPN team side')
        team = candidates[0].get('team')
        if not isinstance(team, dict) or str(team.get('id', '')).strip() != fixture[side+'_team_id']:
            raise ValueError('ESPN competitor ID does not match parsed fixture')
        source = fixture[side]
        options = {source}
        for name_key in ('displayName','shortDisplayName','name'):
            text = team.get(name_key)
            if isinstance(text, str) and 2 <= len(text.strip()) <= 100:
                options.add(text.strip())
        # Concatenating provider location and nickname is not used as evidence.
        # It would be a synthesized label, not an ESPN-provided club spelling.
        if source not in options or not all(isinstance(x, str) for x in options):
            raise ValueError('Missing original source display name')
        output[side] = sorted(options)
    return output


def discover_with_provider_names(day,league='All supported leagues',
                                 time_zone='Africa/Douala',transport=None):
    """Same validation/day semantics as Step13B.2A, plus source name evidence."""
    if league != 'All supported leagues' and league not in BASE.LEAGUES:
        raise ValueError('Unsupported league')
    if time_zone not in BASE.TIMEZONES:
        raise ValueError('Unsupported time zone')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(day)):
        raise ValueError('Invalid day')
    local_day = date.fromisoformat(day)
    tz = ZoneInfo(time_zone)
    codes = [BASE.LEAGUES[league]] if league in BASE.LEAGUES else list(BASE.LEAGUES.values())
    days = [(local_day+timedelta(days=n)).strftime('%Y%m%d') for n in (-1,0,1)]
    jobs = [(code,d) for code in codes for d in days]
    fetch = transport or BASE.download_day
    observed={};errors=[]
    with ThreadPoolExecutor(max_workers=min(6,len(jobs))) as pool:
        futures={pool.submit(fetch,c,d):(c,d) for c,d in jobs}
        for future in as_completed(futures):
            code,daycode = futures[future]
            try:
                response=future.result()
                if not isinstance(response,list):
                    raise ValueError('Provider response is not a list')
                for raw in response:
                    row = BASE.parse_event(raw,code)
                    if row is None:
                        continue
                    if BASE.stamp_utc(row['kickoff_utc']).astimezone(tz).date() != local_day:
                        continue
                    names = names_from_same_espn_team(raw,row)
                    key=row['key']
                    if key in observed:
                        old=observed[key]
                        if any(old[k]!=row[k] for k in row):
                            raise ValueError('Provider changed same fixture evidence across responses')
                        for side in ('home','away'):
                            old['source_name_options'][side] = sorted(
                                set(old['source_name_options'][side]) | set(names[side]))
                    else:
                        row['source_name_options'] = names
                        observed[key] = row
            except Exception as exc:
                errors.append(f'{code}/{daycode}:{type(exc).__name__}')
    if errors:
        raise RuntimeError('Incomplete ESPN source evidence; no silent partial results: '+', '.join(errors))
    return sorted(observed.values(),key=lambda r:(r['kickoff_utc'],r['key']))


class ProviderNameEvidenceResearch(PARENT.ExactEvidenceResearchInference):
    """Does not trust caller-supplied alternate names.

    Only .day() provides trusted source-name evidence. .analyze() called with
    manually built fixtures falls back to the original exact displayName check.
    """
    def __init__(self):
        super().__init__()
        self._provider_source_lock = RLock()
        self._trusted_provider_names = {}

    def _trusted_names(self, fixture, side):
        allowed={fixture[side]}
        record=self._trusted_provider_names.get(fixture['key'])
        if record is not None:
            # Name options can never be injected from caller-supplied fixture dict.
            if any(record.get(k)!=fixture.get(k) for k in
                   ('key','source_competition_id','source_match_id',
                    'home_team_id','away_team_id','home','away')):
                raise ValueError('Trusted ESPN names do not match fixture record')
            allowed.update(record['source_name_options'][side])
        return sorted(allowed)

    def analyze(self, fixtures):
        # Prevent a concurrent request from reading another request's
        # trusted publisher-name evidence while it is being processed.
        with self._provider_source_lock:
            return super().analyze(fixtures)

    def _unmatched_historical_review(self, resolver, historical_lg, name_options):
        """Unapproved diagnostic alternatives; NEVER feed into model features."""
        history=resolver.connection.execute(
            'SELECT a.spelling, a.normalized FROM historical_aliases a '
            'JOIN historical_clubs c ON c.canonical_id=a.canonical_id '
            'WHERE c.historical_league_id=?', (historical_lg,)).fetchall()
        keyed={x['normalized']:x['spelling'] for x in history}
        suggestions=[]
        for supplied in name_options:
            n=self.resolver_module.normalize_club_name(supplied)
            candidates=sorted(((SequenceMatcher(None,n,h).ratio(),keyed[h])
                               for h in keyed if n and h),reverse=True)
            suggestions.extend([{'source_name':supplied,'historical_name':spelling,
                                 'similarity_review_only':round(value,3)}
                                for value,spelling in candidates[:2] if value>=0.65])
        return sorted(suggestions,key=lambda x:(-x['similarity_review_only'],x['historical_name']))[:4]

    def _exact_candidate(self, resolver, fixture, side, historical_lg, cutoff):
        source_name=str(fixture[side]).strip()
        source_id=str(fixture[side+'_team_id']).strip()
        code=fixture['source_competition_id']
        options=self._trusted_names(fixture,side)
        info={'provider_id':source_id,'provider_name':source_name,
              'names_observed_from_provider':options,
              'candidate_count':0,'identity_approved':False,
              'provider_id_already_in_original_registry':False,
              'evidence_kind':'NONE'}
        if not re.fullmatch(r'\d{1,20}',source_id) or not source_name:
            return None,info,'HOLD_PROVIDER_NAME_OR_ID_INVALID'
        registered=resolver.connection.execute(
            'SELECT 1 FROM provider_teams WHERE source=? AND source_competition_id=? '
            'AND source_team_id=?',('ESPN',code,source_id)).fetchone() is not None
        info['provider_id_already_in_original_registry']=registered
        normalize=self.resolver_module.normalize_club_name
        normalized={normalize(n):n for n in options if normalize(n)}
        if not normalized:
            return None,info,'HOLD_NO_VALID_PROVIDER_NAMES'
        # First, find EVERY exact match to an original completed-match alias.
        rows=resolver.connection.execute(
            'SELECT DISTINCT a.canonical_id, a.normalized, a.spelling AS historical_spelling, '
            'c.historical_league_id, c.historical_team_key '
            'FROM historical_aliases a '
            'JOIN historical_clubs c ON c.canonical_id=a.canonical_id '
            'WHERE c.historical_league_id=? AND a.normalized IN ('+
            ','.join('?' for _ in normalized)+')',
            (historical_lg,*normalized)).fetchall()
        found={row['canonical_id'] for row in rows}
        info['candidate_count']=len(found)
        if len(found)!=1:
            info['unapproved_review_suggestions']=self._unmatched_historical_review(
                resolver,historical_lg,options)
            return None,info,('HOLD_NO_EXACT_PROVIDER_NAME_VARIANT_IN_HISTORY'
                              if not found else 'HOLD_CONFLICTING_EXACT_SOURCE_NAME_VARIANTS')
        selected=next(iter(found))
        originals=resolver.connection.execute(
            'SELECT DISTINCT canonical_id FROM candidate_links '
            'WHERE source=? AND source_competition_id=? AND source_team_id=? '
            'AND evidence_type=?',('ESPN',code,source_id,'EXACT_NORMALIZED_NAME')).fetchall()
        original_ids={r['canonical_id'] for r in originals}
        info['original_registry_exact_candidate_count']=len(original_ids)
        if original_ids and original_ids!={selected}:
            return None,info,'HOLD_PROVIDER_ID_CONFLICTS_WITH_ORIGINAL_EXACT_EVIDENCE'
        matching=[r for r in rows if r['canonical_id']==selected]
        chosen=next((r for r in matching if r['normalized']==normalize(source_name)),matching[0])
        lg=BASE.stable_id(chosen['historical_league_id'])
        team=BASE.stable_id(chosen['historical_team_key'])
        if lg!=historical_lg or not team:
            return None,info,'HOLD_EXACT_NAME_FOUND_IN_WRONG_LEAGUE'
        label=BASE.latest_prior_unique(
            self._labels_sides.get((lg,team,side),[]),cutoff)
        if not label:
            return None,info,'HOLD_NO_PRIOR_COMPLETED_MATCH_SIDE_LABEL'
        info['evidence_kind']=(
            'PROVIDER_ALT_NAME_UNIQUE_HISTORICAL_ALIAS_REQUEST_ONLY'
            if chosen['normalized']!=normalize(source_name)
            else 'PROVIDER_DISPLAY_NAME_UNIQUE_HISTORICAL_ALIAS_REQUEST_ONLY')
        info['source_observed_spelling_matching_history']=normalized[chosen['normalized']]
        info['historical_spelling_matched']=chosen['historical_spelling']
        info['historical_name_prior_to_kickoff']=label
        info['proposed_canonical_id']=selected
        return ({'league_id':lg,'team_key':team,'canonical_id':selected}, info,
                'RESEARCH_UNIQUE_PROVIDER_NAME_AND_HISTORICAL_ALIAS_NOT_APPROVED')

    def day(self, day, league='All supported leagues', time_zone='Africa/Douala', transport=None):
        with self._provider_source_lock:
            fixtures=discover_with_provider_names(day,league,time_zone,transport)
            upcoming=[f for f in fixtures if f['phase']=='UPCOMING' and
                      BASE.stamp_utc(f['kickoff_utc'])>datetime.now(timezone.utc)]
            # Never trust a caller to inject alternate names through analyze().
            self._trusted_provider_names={f['key']:dict(f) for f in upcoming}
            try:
                # Parent .analyze performs history/registry/model SHA checks.
                result=self.analyze(upcoming)
            finally:
                self._trusted_provider_names={}
            return {'date':day,'league':league,'time_zone':time_zone,
                    'source_upcoming_fixture_count':len(upcoming),
                    'source_provider_name_variants_count':sum(
                        len(set(f['source_name_options']['home']))+
                        len(set(f['source_name_options']['away'])) for f in upcoming),
                    'research_results':result,
                    'status_counts':dict(Counter(r['status'] for r in result)),
                    'research_only':True,'historical_results_refreshed':False,
                    'club_identities_approved':False,'market_calibration_verified':False,
                    'production_predictions_enabled':False,'public_betting_picks':[],
                    'original_history_last_completed_utc':self.latest_history}
