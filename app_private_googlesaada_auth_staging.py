"""Soccer AI — task-focused private interface reusing the verified Step 13B.1 backend.

Front page: enter unlimited match lines or select upcoming games. Other tasks
have independent tabs, with the live scoreboard LAST. No technical JSON panel.
On-demand research results come from verified original models for eligible
new ESPN fixtures; unresolved clubs remain unavailable.
No GPT, training, published picks, or shared links.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import re

ROOT = Path(__file__).resolve().parent
BASE = ROOT / 'app_private_step13b1.py'


def load_base():
    if not BASE.is_file():
        raise FileNotFoundError('Step 13B.1 backend is missing')
    spec = importlib.util.spec_from_file_location('soccer_step13b1_backend_for_tabs', BASE)
    if not spec or not spec.loader:
        raise RuntimeError('Cannot load the verified fixture backend')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for required in ('load_snapshot', 'load_day', 'ui_tables', 'predictions',
                     'recommendations', 'assistant_request', 'score_cards',
                     'today_for', 'filter_items', 'DAILY', 'PROFILES', 'LEAGUES'):
        if not hasattr(mod, required):
            raise RuntimeError('Required original Step 13B.1 backend operation is missing: ' + required)
    # GOOGLESAADA_STAGE_LEGACY_SNAPSHOT_OPTIONAL_V1
    # Do not manufacture or approve missing research evidence.
    # The original backend keeps SNAPSHOT=None and must fail closed
    # for all operations requiring verified historical research.
    mod.load_snapshot()
    mod.GOOGLESAADA_LEGACY_RESEARCH_READY = (
        getattr(mod, 'SNAPSHOT', None) is not None
    )
    return mod


old = load_base()
BRIDGE_FILE = ROOT / 'backend/dynamic_ui_bridge_v3_recent_markets.py'
bridge_spec = importlib.util.spec_from_file_location('soccer_private_dynamic_ui_bridge', BRIDGE_FILE)
if bridge_spec is None or bridge_spec.loader is None:
    raise RuntimeError('Verified research bridge cannot be loaded')
bridge_module = importlib.util.module_from_spec(bridge_spec)
bridge_spec.loader.exec_module(bridge_module)
bridge = bridge_module.DynamicResearchUIBridge(old)
GPT_UI_FILE = ROOT / 'backend/google_saada_explanation_bridge.py'
gpt_spec = importlib.util.spec_from_file_location('soccer_private_gpt_ui_bridge_v1', GPT_UI_FILE)
if gpt_spec is None or gpt_spec.loader is None:
    raise RuntimeError('Audited private GPT bridge unavailable')
gpt_module = importlib.util.module_from_spec(gpt_spec)
gpt_spec.loader.exec_module(gpt_module)

# The original Python research router is always authoritative; the GPT layer
# receives only server-recomputed model rows after source verification.
GOOGLESAADA_EXPLANATION_MODEL = 'gpt-5-mini'



# GoogleSaada protected explanation boundary
import importlib.util as _gs_guard_importlib

_gs_guard_path = (
    Path(__file__).resolve().parent
    / "backend"
    / "google_saada_agent_guard.py"
)

_gs_guard_spec = _gs_guard_importlib.spec_from_file_location(
    "google_saada_agent_guard",
    str(_gs_guard_path)
)

_gs_guard_module = _gs_guard_importlib.module_from_spec(
    _gs_guard_spec
)

_gs_guard_spec.loader.exec_module(_gs_guard_module)

_gs_guarded_explanation_call = (
    _gs_guard_module.guarded_explanation_call
)


# GoogleSaada authenticated staging gate
import gradio as gr
import importlib.util as _gs_auth_importlib

_gs_auth_path = (
    Path(__file__).resolve().parent
    / "backend"
    / "google_saada_authenticated_gate.py"
)

_gs_auth_spec = _gs_auth_importlib.spec_from_file_location(
    "google_saada_authenticated_gate",
    str(_gs_auth_path)
)

_gs_auth_module = _gs_auth_importlib.module_from_spec(
    _gs_auth_spec
)

_gs_auth_spec.loader.exec_module(_gs_auth_module)

_gs_authenticated_explanation_call = (
    _gs_auth_module.authenticated_explanation_call
)

def _googlesaada_agent_text(result):
    if result.get('status')=='PASS_PRIVATE_GPT_EXPLANATION':
        return result['text'] + '\n\n*Experimental research only; not a verified betting recommendation.*'
    if result.get('status')=='HOLD_NO_VERIFIED_MODEL_ROWS':
        return 'GoogleSaada Agent can only explain matches with available model estimates.'
    return 'GoogleSaada Agent is temporarily unavailable. The match estimates remain unchanged.'


def explain_matches_googlesaada(keys, raw, risk, day, league, zone, request: gr.Request = None):
    msg,rows,note=bridge.predictions_or_recommendations(
        keys,raw,risk,day,league,zone,parse_recommendation_prompt)
    if not rows:
        return 'No eligible upcoming model estimates are available to explain.'
    return _googlesaada_agent_text(_gs_authenticated_explanation_call(gpt_module.explain_verified_rows, rows,GOOGLESAADA_EXPLANATION_MODEL, request=request))


def explain_recommendations_googlesaada(risk,day,league,zone, request: gr.Request = None):
    msg,rows,note=bridge.recommendations(risk,day,league,zone)
    if not rows:
        return 'No qualifying match analyses are available for GoogleSaada Agent to explain.'
    return _googlesaada_agent_text(_gs_authenticated_explanation_call(gpt_module.explain_verified_rows, rows,GOOGLESAADA_EXPLANATION_MODEL, request=request))


def ask_googlesaada_agent(prompt,risk,day,league,zone, request: gr.Request = None):
    # Reuse the existing deterministic bounded intent router. If it cannot
    # verify a fixture and compute original model rows, do not call GPT.
    msg,rows,note=bridge.assistant_request(prompt,risk,day,league,zone)
    if not rows:
        return msg,rows,note
    result=_gs_authenticated_explanation_call(gpt_module.explain_verified_rows, rows,GOOGLESAADA_EXPLANATION_MODEL,question=prompt, request=request)
    return msg,rows,_googlesaada_agent_text(result) + '\n\n' + note
ALL_LEAGUES = old.ALL_LEAGUES
TZ_CHOICES = old.TZ_CHOICES
PROFILES = old.PROFILES
LEAGUES = old.LEAGUES
DISCLAIMER = 'All predictions are for entertainment purposes only. GoogleSaada does not guarantee wins and is not responsible for any decisions made based on these predictions.'


def typed_count(text):
    lines = [line.strip() for line in str(text or '').splitlines() if line.strip()]
    count = len(lines)
    return (f'**{count} match request' + ('s' if count != 1 else '') +
            '** entered. Maximum 16 fixtures per request.')



def parse_recommendation_prompt(text):
    """Pure parser for an explicit recommendation query; no LLM or hidden inference."""
    raw = str(text or '').strip()
    if not raw or len(raw) > 1000 or '\n' in raw:
        return None
    if re.fullmatch(r'.{2,120}?\s+(?:vs\.?|versus|v\.?)\s+.{2,120}', raw, re.I):
        return None
    if not re.search(r'\b(recommend|suggest|find|show|list|pick|select|top|give me)\b', raw, re.I):
        return None
    if not re.search(r'\b(match(?:es)?|game(?:s)?|fixture(?:s)?|prediction(?:s)?|probabilit(?:y|ies))\b', raw, re.I):
        return None
    number = re.search(r'\b(?:top|up to|best|recommend|suggest|find|show|list|give me)\s+(\d{1,5})\b', raw, re.I)
    if not number:
        number = re.search(r'\b(\d{1,5})\s+(?:matches|games|fixtures|predictions)\b', raw, re.I)
    requested = int(number.group(1)) if number else 5
    if requested < 1:
        return None
    match_percent = re.search(r'(?<!\d)(\d{1,3}(?:\.\d+)?)\s*%', raw)
    if match_percent:
        numeric = float(match_percent.group(1))
        if not 0 <= numeric <= 100:
            return None
        minimum = numeric / 100
    else:
        # "High probability" is NOT the "High" risk profile (60% threshold).
        minimum = 0.75
    risk = re.search(r'\b(low|medium|high|balanced)\s+risk\b', raw, re.I)
    return {'count': requested, 'min_probability': minimum,
            'explicit_risk_profile': risk.group(1).capitalize() if risk else None}


def input_status(text):
    parsed = parse_recommendation_prompt(text)
    if parsed:
        return ('**Recommendation question detected.** Find up to '
                + str(parsed['count']) + ' currently eligible matches at or above '
                + f"{parsed['min_probability']:.0%}" + '. Select **Analyze My Matches** to search.')
    return typed_count(text)


def prompt_or_matches(keys, free_text, profile, day, league, tz):
    """Routes source-verified recommendations OR Home vs Away lines; no GPT calls."""
    parsed = parse_recommendation_prompt(free_text)
    if parsed is None:
        return old.predictions(keys, free_text, profile, day, league, tz)
    if keys:
        return ('Choose either a recommendation question or selected fixtures for this request.',
                [], 'Clear the selected games before searching by recommendation question.')
    selected_profile = parsed['explicit_risk_profile'] or profile
    # Reuse original source evidence, freshness checks, research policy and UI hold.
    text, rows, note = old.recommendations(selected_profile, day, league, tz)
    if not isinstance(rows, list):
        return 'Recommendations are temporarily unavailable.', [], old.DISCLAIMER
    acceptable = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            continue
        match_name, market_name, pct = row
        try:
            p = float(str(pct).strip().rstrip('%')) / 100
        except (ValueError, TypeError):
            continue
        if not (0 <= p <= 1):
            continue
        if p + 0.00001 >= parsed['min_probability']:
            acceptable.append((p, [str(match_name), str(market_name), str(pct)]))
    acceptable.sort(key=lambda item: item[0], reverse=True)
    picks = [row for _, row in acceptable[:parsed['count']]]
    found = len(picks)
    msg = (f'**{found} of up to {parsed["count"]} requested matches** currently meet '
           f'the {parsed["min_probability"]:.0%} estimated-probability threshold.')
    if not picks:
        msg += ' No qualifying research estimates are available; none were fabricated.'
    # The saved risk router currently returns at most five recommendations per call.
    # Do not claim this is a limitless recommendation pipeline.
    if parsed['count'] > 5:
        msg += (' This app provides research estimates only '
                'per recommendation search; more require the Step 13B.2 refreshable inference service.')
    msg += ' These are experimental estimates, not reliable win rates.'
    return msg, picks, note

def load_prediction_day(day, league, tz, search=''):
    # No scoreboard content on the primary prediction tab.
    table, dropdown, _, status, rows = old.load_day(day, league, tz, search, force=True)
    return dropdown, status, table, rows


def filter_prediction_day(rows, tz, search):
    import gradio as gr
    table, choices, _ = old.ui_tables(rows or [], tz, search)
    return gr.update(choices=choices, value=[]), table


def select_all_upcoming(rows, tz, search):
    import gradio as gr
    _, choices, _ = old.ui_tables(rows or [], tz, search)
    return gr.update(value=[key for _display, key in choices])


def clear_match_choices():
    import gradio as gr
    return gr.update(value=[])


GS15F_INPUT_ADAPTER = ROOT / "backend/google_saada_prediction_input_v1.py"
gs15f_spec = importlib.util.spec_from_file_location(
    "googlesaada_prediction_input_v1", GS15F_INPUT_ADAPTER
)
if gs15f_spec is None or gs15f_spec.loader is None:
    raise RuntimeError("Verified prediction input adapter missing")
gs15f_adapter = importlib.util.module_from_spec(gs15f_spec)
gs15f_spec.loader.exec_module(gs15f_adapter)

def predict_many(keys, free_text, profile, day, league, tz):
    # Existing, verified ten-model bridge remains authoritative.
    return gs15f_adapter.route(
        bridge, old, keys, free_text, profile,
        day, league, tz, parse_recommendation_prompt
    )


def load_fixture_schedule(day, league, tz, search=''):
    table, _, _, status, rows = old.load_day(day, league, tz, search, force=True)
    return table, status, rows


def search_fixture_schedule(rows, tz, search):
    table, _, _ = old.ui_tables(rows or [], tz, search)
    return table


def load_scores(day, league, tz, show='All matches'):
    # Scores use provider reporting; no trained-model inference is involved.
    try:
        rows = old.DAILY.day(day, league, tz, force=True)
        options = {'All matches', 'Live only', 'Finished only', 'Live & finished'}
        if show not in options:
            raise ValueError('Unknown score filter')
        if show == 'Live only':
            filtered = [r for r in rows if r['phase'] == 'LIVE']
        elif show == 'Finished only':
            filtered = [r for r in rows if r['phase'] == 'FINAL']
        elif show == 'Live & finished':
            filtered = [r for r in rows if r['phase'] in ('LIVE', 'FINAL')]
        else:
            filtered = rows
        cards = old.score_cards(filtered, tz)
        status = (f'**{len(filtered)} games shown** · {len(rows)} total games for '
                  f'{day} · {league} · {tz}')
        return cards, status, True
    except (ValueError, RuntimeError):
        return 'Unable to refresh scores right now. Please try again.', \
               'Scores temporarily unavailable.', False


def refresh_loaded_scores(day, league, tz, show, has_opened):
    import gradio as gr
    if not has_opened:
        return gr.update(), gr.update(), False
    return load_scores(day, league, tz, show)


# GOOGLESAADA STAGE 15I.6D.5 — COMPLETE SOURCE-VERIFIED APP ROUTES
import importlib.util as _gsi_util
import os as _gsi_os
from pathlib import Path as _gsi_Path
from datetime import datetime as _gsi_dt, timedelta as _gsi_td
from zoneinfo import ZoneInfo as _gsi_Zone
import re as _gsi_re

def _gsi_load(name,filename):
    p=_gsi_Path(__file__).resolve().parent/'backend'/filename
    spec=_gsi_util.spec_from_file_location(name,p)
    if spec is None or spec.loader is None:raise RuntimeError('GoogleSaada module unavailable: '+filename)
    module=_gsi_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

_gsi_world=_gsi_load('gsi_world_v2','google_saada_global_scores_v2.py')
_gsi_ledger=_gsi_load('gsi_quota_v2','google_saada_free_quota_v2.py')
_gsi_display=_gsi_load('gsi_display_v1','google_saada_match_display_v1.py')
_gsi_derived=_gsi_load('gsi_derived_v1','google_saada_derived_markets_v1.py')
_gsi_intent=_gsi_load('gsi_router_v1','google_saada_intent_fixture_router_v1.py')
_GSI_MARKETS=('Full-time result','Exact score','Half-time / Full-time')
_GSI_HEADERS=['Match','Home win','Draw','Away win','Over 2.5','Under 2.5','BTTS Yes','BTTS No','Leading 1X2 outcome','Exact score (derived)','Exact score probability','HT/FT (derived)','HT/FT probability']
_GSI_DISCLAIMER='All predictions are for entertainment purposes only. GoogleSaada does not guarantee wins and is not responsible for any decisions made based on these predictions.'
# Switch on verified recent-result feature refresh ONLY for this isolated app.
_gsi_os.environ.setdefault('GS_RECENT_FEATURES_ENABLED','1')
_GSI_DAY_MAX=20
_GSI_BATCH_MAX=16

def _gsi_quota(uid):
    return '**Free allowance: '+str(_gsi_ledger.remaining(uid))+'/20 match analyses remaining today (UTC).** '+('Persistent usage database available.' if _gsi_ledger.persistent() else 'Temporary usage database; limits reset on Space restart.')

def _gsi_uid(token):
    return _gsi_ledger.identity(token)

def _gsi_count(raw):
    return len({str(r[0]) for r in raw or [] if isinstance(r,(list,tuple)) and len(r)>=3})

def _gsi_output(msg,rows,note,selected,day,league,zone,context='prediction'):
    # Never display individual identity failure names in customer notes.
    try:
        wide,decisions=_gsi_display.compact(rows)
    except (ValueError,TypeError):
        return ('Model output could not be verified for display.',[],
            'Original probability validation did not pass.\n\nMANDATORY DISCLAIMER: '+_GSI_DISCLAIMER)
    markets=set(selected or _GSI_MARKETS)
    if not markets or not markets.issubset(set(_GSI_MARKETS)):
        markets={'Full-time result'}
    eligible={}
    if wide and any(m in markets for m in ('Exact score','Half-time / Full-time')):
        try:
            current,_=bridge._evaluations(day,league,zone)
            eligible={str(v['home'])+' vs '+str(v['away']):v for v in current.values()
                      if v.get('status')=='UNAPPROVED_FROZEN_HISTORY_RESEARCH_ESTIMATE'}
        except (RuntimeError,ValueError,OSError,KeyError):pass
    detailed=[];table=[]
    for record in wide:
        r=list(record)
        if 'Full-time result' not in markets:r[1:9]=['—']*8
        extras=['—']*4
        reference=eligible.get(str(record[0]))
        if reference:
            extra=reference.get('gs_recent_feature_context')
            detailed.append(_gsi_intent.freshness_note(extra))
            if any(m in markets for m in ('Exact score','Half-time / Full-time')):
                try:
                    derived=_gsi_derived.derive(reference)
                    if 'Exact score' in markets:
                        extras[0]=derived['exact_score']
                        extras[1]=f"{derived['exact_probability']:.1%}"
                        detailed.append('Derived exact-score distribution (top five): '+', '.join(f'{a} {p:.1%}' for a,p in derived['exact_top5']))
                    if 'Half-time / Full-time' in markets:
                        extras[2]=derived['htft'];extras[3]=f"{derived['htft_probability']:.1%}"
                        detailed.append('Derived HT/FT combinations (top five): '+', '.join(f'{a} {p:.1%}' for a,p in sorted(derived['htft_nine_probabilities'].items(),key=lambda x:-x[1])[:5]))
                except (ValueError,TypeError,KeyError):
                    detailed.append('Derived market unavailable: goal-regressor evidence incomplete.')
        elif any(m in markets for m in ('Exact score','Half-time / Full-time')):
            detailed.append('Derived estimate withheld until the original model and source evidence can be verified.')
        table.append(r+extras)
    notes=_gsi_display.public_notes('',wide,decisions,context)
    if detailed:notes+='\n\n'+'\n'.join('• '+s for s in dict.fromkeys(detailed))
    if not wide:
        if 'limit' in str(msg).casefold() or 'allowance' in str(msg).casefold() or 'remain' in str(msg).casefold():
            user_msg=str(msg)
        else:
            safe=str(msg or '').strip()
            if safe.startswith('No eligible upcoming matches were selected.'):
                user_msg=('No verified upcoming fixtures matched this request. '
                          'Choose a FUTURE match from the Premier League, La Liga or Bundesliga selector, '
                          'or enter one match per line using Home vs Away.')
            elif safe.startswith('Match analysis is unavailable right now.'):
                user_msg='The original inference/fixture service could not complete this request. Try refreshing the date.'
            elif _gsi_re.match(r'^\*\*0 of \d+ selected matches analyzed\.\*\*',safe):
                user_msg=('The selected games did not pass the original source-identity, '
                          'historical feature and pre-kickoff eligibility gates. '
                          'No model probabilities were invented.')
            else:
                user_msg='No eligible original-model probabilities were returned; try another future supported-league fixture.'
    else:user_msg='**'+str(len(wide))+' fixture(s) analyzed.**'
    return user_msg,table,notes

def _gsi_execute_original(keys,text,profile,day,league,zone,token,markets):
    uid,signed=_gsi_uid(token)
    remaining=_gsi_ledger.remaining(uid)
    requested_keys=list(dict.fromkeys(keys or []))
    raw=str(text or '').strip()
    # Separate clearly delimited manual fixtures; never synthesize team identities.
    if raw.casefold().count(' vs ')>1 and (',' in raw or ';' in raw):
        raw=_gsi_re.sub(r'\s*[,;]\s*', '\n',raw)
    matches=[line.strip() for line in raw.splitlines() if ' vs ' in line.casefold()]
    match_count=_gsi_re.search(r'(?i)\b(?:predict|forecast|analy[sz]e|recommend)\s+(\d{1,2})\s+(?:matches|games|fixtures)\b',raw)
    num=int(match_count.group(1)) if match_count and not requested_keys and not matches else max(1,len(requested_keys)+len(matches))
    if not 1<=num<=_GSI_BATCH_MAX:
        return 'Choose 1 to 16 matches per request.',[], 'Use a supported prediction type.\n\nMANDATORY DISCLAIMER: '+_GSI_DISCLAIMER,signed,_gsi_quota(uid)
    if num>remaining:return 'Only '+str(remaining)+' match analyses remain today.',[], 'Browse fixtures and scores without spending credits.',signed,_gsi_quota(uid)
    reservation=_gsi_ledger.reserve(uid,num)
    if not reservation:return 'Daily prediction allowance exhausted.',[], '',signed,_gsi_quota(uid)
    counted=0
    try:
        # Current fixture matching does not grant historical identity approval.
        if not requested_keys and len(matches)==1:
            fixtures=list(bridge._current(day,league,zone).values())
            intent=_gsi_intent.route_request(raw,markets,source_fixtures=fixtures)
            if intent['action']=='predict':requested_keys=[intent['fixture_key']];raw=''
            elif intent['action']=='clarify':
                return intent['message'],[], 'No unverified team identity was used.\n\nMANDATORY DISCLAIMER: '+_GSI_DISCLAIMER,signed,_gsi_quota(uid)
        if match_count and not requested_keys and not matches:
            selected,current=bridge._evaluations(day,league,zone)
            requested_keys=[k for k,v in selected.items() if v.get('market_probabilities') and v.get('status')=='UNAPPROVED_FROZEN_HISTORY_RESEARCH_ESTIMATE'][:num]
            raw=''
        msg,raw_rows,note=bridge.predictions(requested_keys,raw,profile,day,league,zone)
        counted=_gsi_count(raw_rows)
        if counted>num:raise RuntimeError('Model returned more matches than reserved')
        out=_gsi_output(msg,raw_rows,note,markets,day,league,zone)
        counted=min(counted,len(out[1]))  # refund rejected or undisplayed results
    except (RuntimeError,ValueError,OSError,LookupError,KeyError,TypeError):
        out=('Model analysis temporarily unavailable.',[],'No estimate was released.\n\nMANDATORY DISCLAIMER: '+_GSI_DISCLAIMER)
        counted=0
    finally:
        _gsi_ledger.settle(reservation,counted)
    return (*out,signed,_gsi_quota(uid))

def gs_predict_complete(keys,text,profile,day,league,zone,token,markets,request:gr.Request=None):
    return _gsi_execute(keys,text,profile,day,league,zone,token,markets)

def gs_recommend_complete(profile,day,league,zone,token,request:gr.Request=None):
    uid,signed=_gsi_uid(token)
    remaining=_gsi_ledger.remaining(uid)
    if not remaining:return 'Daily prediction allowance exhausted.',[],'Browse scores and fixtures for free.',signed,_gsi_quota(uid)
    reservation=_gsi_ledger.reserve(uid,min(remaining,16))
    if not reservation:return 'Daily prediction allowance exhausted.',[],'',signed,_gsi_quota(uid)
    counted=0
    try:
        msg,raw,note=bridge._research_recommendations(profile,day,league,zone,requested_count=min(remaining,16))
        counted=_gsi_count(raw)
        if counted>min(remaining,16):raise RuntimeError('Recommendation exceeded reservation')
        out=_gsi_output(msg,raw,note,['Full-time result'],day,league,zone,context='recommendation')
        counted=min(counted,len(out[1]))  # never debit undisplayed recommendations
    except (RuntimeError,ValueError,OSError,LookupError,KeyError,TypeError):
        out=('Recommendations unavailable from the verified source.',[],'MANDATORY DISCLAIMER: '+_GSI_DISCLAIMER)
        counted=0
    finally:_gsi_ledger.settle(reservation,counted)
    return (*out,signed,_gsi_quota(uid))

def gs_fixtures_complete(day,league,zone,search=''):
    try:
        rows,failures,_=_gsi_world.collect(day,league,zone,search)
        table=_gsi_world.fixture_table(rows,zone)
        return table,'**'+str(len(table))+' upcoming matches across configured source competitions.**'+(' Partial coverage: '+str(len(failures))+' requests failed.' if failures else ''),rows
    except (RuntimeError,ValueError,OSError,TimeoutError):return [],'Source fixture feed temporarily unavailable.',[]

def gs_fixture_search_complete(rows,zone,search):
    try:return _gsi_world.fixture_table([r for r in (rows or []) if str(search or '').casefold() in (r['home']+' '+r['away']+' '+r['league']).casefold()],zone)
    except (RuntimeError,ValueError,KeyError,TypeError):return []

def gs_scores_complete(day,league,zone,mode='All matches',search=''):
    try:
        rows,failures,_=_gsi_world.collect(day,league,zone,search,live_now=(mode=='Live only'))
        if mode=='Finished only':rows=[r for r in rows if r['phase']=='FINAL']
        elif mode=='Live & finished':rows=[r for r in rows if r['phase'] in ('LIVE','FINAL')]
        note='**'+str(len(rows))+' source-reported matches.** '+('Some competitions are currently unavailable.' if failures else '')
        return _gsi_world.cards(rows,zone),note,True
    except (RuntimeError,ValueError,OSError,TimeoutError):return 'Live score provider unavailable.','Please retry later.',False

def gs_score_refresh_complete(day,league,zone,mode,loaded,search):
    if not loaded:return gr.update(),gr.update(),False
    return gs_scores_complete(day,league,zone,mode,search)

def gs_agent_complete(prompt,risk,day,league,zone,token,markets,request:gr.Request=None):
    uid,signed=_gsi_uid(token)
    raw=str(prompt or '').strip()
    if not raw or len(raw)>500:
        return 'Please ask a shorter question.',[],'',signed,_gsi_quota(uid)
    scope_date=day
    local=_gsi_dt.now(_gsi_Zone(zone)).date()
    if 'tomorrow' in raw.casefold():scope_date=(local+_gsi_td(days=1)).isoformat()
    elif 'today' in raw.casefold():scope_date=local.isoformat()
    fixture_rows=[]
    if _gsi_re.search(r'(?i)\b(?:vs|versus)\b',raw):
        try:fixture_rows=list(bridge._current(scope_date,league,zone).values())
        except (RuntimeError,ValueError,OSError):pass
    intent=_gsi_intent.route_request(raw,markets,fixture_rows)
    action=intent['action']
    if action=='unsupported_player_market':return intent['message'],[],'MANDATORY DISCLAIMER: '+_GSI_DISCLAIMER,signed,_gsi_quota(uid)
    if action=='clarify' or action=='help':return intent['message'],[],('Fixture selection is verified against the sports-data provider.\n\nMANDATORY DISCLAIMER: '+_GSI_DISCLAIMER),signed,_gsi_quota(uid)
    if action=='fixtures':
        rows,fail,_=_gsi_world.collect(scope_date,_gsi_world.ALL,zone)
        shown=_gsi_world.fixture_table(rows,zone)
        text=str(len(shown))+' upcoming source-reported fixtures.\n'+'\n'.join('• '+x[2]+' vs '+x[3]+' ('+x[0]+')' for x in shown[:40])
        return text,[], 'Browsing fixtures costs no prediction credits.',signed,_gsi_quota(uid)
    if action=='scores':
        rows,fail,_=_gsi_world.collect(scope_date,_gsi_world.ALL,zone,live_now=('live' in raw.casefold()))
        text=str(len(rows))+' matches: '+'; '.join(x['home']+' '+str(x['home_score'] if x['home_score'] is not None else '—')+'–'+str(x['away_score'] if x['away_score'] is not None else '—')+' '+x['away'] for x in rows[:35])
        return text,[], 'Provider results are not model predictions.',signed,_gsi_quota(uid)
    if action=='recommendations':
        return gs_recommend_complete(risk,scope_date,league,zone,signed,request)
    if action=='predict':
        return _gsi_execute([intent['fixture_key']],'',risk,scope_date,league,zone,signed,intent['markets'])
    if action=='predict_many':
        return _gsi_execute([], 'Predict '+str(intent['requested_count'])+' matches',risk,scope_date,league,zone,signed,intent['markets'])
    return ('This Agent supports verified fixture browsing, scores and original-model predictions only. '
             'Open-web research and full GPT conversation are not enabled in this release.',[],
             'MANDATORY DISCLAIMER: '+_GSI_DISCLAIMER,signed,_gsi_quota(uid))

def gs_explanation_notes_only(*args,**kwargs):
    return 'Detailed model reasoning is in the Match analysis notes. MANDATORY DISCLAIMER: '+_GSI_DISCLAIMER


# GOOGLESAADA_UI_SIX_TABS_V1

def gs_ui_limit(n):
    try: n = int(n)
    except (ValueError,TypeError): raise gr.Error('Choose 1 to 16 games.')
    if not 1 <= n <= 16: raise gr.Error('Choose 1 to 16 games.')
    return n

def gs_ui_select(rows, tz, search, n):
    import gradio as gr
    table, choices, _ = old.ui_tables(rows or [],tz,search)
    choices=list(choices or [])
    return gr.update(choices=choices,value=[key for _,key in choices[:gs_ui_limit(n)]]),table

def gs_ui_reload(day, league, tz, search, n):
    import gradio as gr
    _, status, table, rows = load_prediction_day(day,league,tz,search)
    _,choices,_ = old.ui_tables(rows or [],tz,search)
    choices=list(choices or [])
    selected=[key for _,key in choices[:gs_ui_limit(n)]]
    return gr.update(choices=choices,value=selected),str(status)+f' | {len(selected)} of {n} requested fixtures selected.',table,rows

def gs_ui_cap(keys, n):
    import gradio as gr
    keys=list(dict.fromkeys(keys or []))
    return gr.update(value=keys[:gs_ui_limit(n)]) if len(keys)>gs_ui_limit(n) else gr.update()

GS_GUIDE = (ROOT / 'app_ui/guide_v2.md').read_text(encoding='utf-8')

GS_V2H_CSS = '\n/* GOOGLESAADA_V2H_ACCESSIBLE_UI_V1 */\n.gradio-container {max-width:1240px!important;margin:auto!important;}\n.gradio-container .prose {line-height:1.65;}\n.gradio-container button.primary {font-weight:750!important;min-height:45px;}\n.gradio-container [role="tablist"] {overflow-x:auto;scrollbar-width:thin;}\n.gradio-container .tabitem {min-width:0;}\n.gs-steps {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:12px 0 20px;}\n.gs-step {padding:16px;border-radius:14px;border:1px solid var(--border-color-primary,#d7e0e8);background:var(--background-fill-secondary,#f7f9fb);}\n.gs-step strong {display:block;font-size:15px;margin-bottom:4px;}\n.gs-step span {font-size:13px;line-height:1.5;opacity:.9;}\n.gradio-container .wrap.svelte-1hfxrpf {overflow-wrap:anywhere;}\n@media(max-width:760px) {.gs-steps{grid-template-columns:1fr;gap:8px;} .gs-step{padding:12px;}\n .gradio-container {padding:6px!important;} .gradio-container [role="tab"]{white-space:nowrap;}}\n'
GS_V2H_STEPS = '<div class="gs-steps" aria-label="How to use GoogleSaada">\n<div class="gs-step"><strong>1 · Choose fixtures</strong><span>Enter a match or select up to 16 scheduled games.</span></div>\n<div class="gs-step"><strong>2 · Analyze</strong><span>Select the prediction markets and run verified analysis.</span></div>\n<div class="gs-step"><strong>3 · Review</strong><span>Compare match probabilities, source notes and unavailable markets.</span></div></div>'


# GS_V8_GPT_ONLY_AGENT_UPGRADE_V1: chat text exclusively via OpenAI Responses API.
# The 13 trained models, authenticated prediction routing and credit settlement are unchanged.
def gs_agent_complete(prompt,risk,day,league,zone,token,markets,request:gr.Request=None):
    from backend.google_saada_gpt_only_agent import respond as _gs_gpt_respond
    uid,signed=_gsi_uid(token)
    raw=str(prompt or '').strip()
    if not raw:
        return 'Ask GoogleSaada a question.', [], '', signed, _gsi_quota(uid)
    if len(raw)>4000:
        return 'Please shorten your message to 4,000 characters.', [], '', signed, _gsi_quota(uid)
    # Structured scores are data only; GPT writes the response. No open model alternatives.
    score_context=[]
    ask_scores=any(x in raw.casefold() for x in ('score','result','live','last match','latest match'))
    if ask_scores:
        try:
            rows,fail,_=_gsi_world.collect(day,_gsi_world.ALL,zone,live_now=('live' in raw.casefold()))
            for v in rows[:24]:
                score_context.append({k:v.get(k) for k in ('home','away','home_score','away_score','status','date_utc','fixture_id')})
        except Exception:
            pass  # a provider outage must never create fictitious scores
    try:
        answer=_gs_gpt_respond(raw,score_context=score_context)
    except Exception as e:
        # Do not leak API keys, cookies or provider payloads.
        answer='GPT service temporarily unavailable. Check backend configuration and try again.'
    return answer, [], 'GPT Agent: live scores are source-dependent; model predictions require separately verified inference. '+_GSI_DISCLAIMER, signed, _gsi_quota(uid)

def build_app():
    import gradio as gr
    day_value = old.today_for('Africa/Douala')
    # Explicit inline high-contrast styles survive Gradio theme h1/p overrides.
    heading = """<div role="banner" style="border-radius:18px; padding:22px 26px;
        background:linear-gradient(110deg,#102a46,#166366);color:#ffffff !important;">
        <div style="font-size:30px; font-weight:800; line-height:1.35;
            color:#ffffff !important; -webkit-text-fill-color:#ffffff !important;
            opacity:1 !important; text-shadow:0 1px 2px rgba(0,0,0,.25);">⚽ GoogleSaada Soccer Predictor</div>
        <div style="font-size:15px; line-height:1.5; margin-top:8px;
            color:#f0feff !important; -webkit-text-fill-color:#f0feff !important;
            opacity:1 !important;">Predict upcoming matches · Compare match probabilities · Follow live football scores</div>
        </div>"""

    with gr.Blocks(title='GoogleSaada Soccer Predictor — Matches & Live Scores', css=GS_V2H_CSS) as demo:
        gsi_token = gr.BrowserState(default_value="", storage_key="googlesaada_quota_v2", secret=_gsi_os.environ.get("GS_BROWSER_STATE_SECRET","googlesaada-anon-trial-v2"))
        gsi_quota = gr.Markdown("Free match allowance: up to 20 analyses per browser per day (UTC).")
        gr.HTML(heading)
        gr.HTML(GS_V2H_STEPS)

        with gr.Tabs():
            # --------------------------------------------------------------
            # 1. MOST IMPORTANT USER ACTION — ENTER / SELECT MATCHES
            # --------------------------------------------------------------
            with gr.Tab('⚽ Predict Matches'):
                gr.Markdown('### Choose matches to analyze')
                gr.Markdown('Enter matches or request up to **16 upcoming games** in everyday language.')
                manual = gr.Textbox(label='Enter matches (optional)',lines=4,placeholder='Arsenal vs Chelsea\n\nOR: Find 10 upcoming matches')
                count = gr.Markdown('**Maximum 16 per request · 20 free analyses per UTC day.** Predictions depend on verified history; unavailable fixtures will not be guessed.')
                p_profile = gr.Radio(PROFILES,value='Balanced',label='Analysis preference')
                gsi_p_markets = gr.CheckboxGroup(choices=list(_GSI_MARKETS),value=list(_GSI_MARKETS),label='Choose prediction types')
                gr.Markdown('### Upcoming scheduled matches')
                with gr.Row():
                    p_quantity = gr.Dropdown(choices=list(range(1,17)),value=10,label='Number of matches (1–16)',scale=2)
                    p_search = gr.Textbox(label='Search available games',placeholder='Search team or league',scale=4)
                with gr.Accordion('Advanced Fixture Search',open=False):
                    with gr.Row():
                        p_day = gr.DateTime(label='Game date',value=day_value,include_time=False,type='string')
                        p_league = gr.Dropdown([ALL_LEAGUES,*LEAGUES],value=ALL_LEAGUES,label='League')
                        p_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Your time zone')
                    p_today = gr.Button('Today',size='sm')
                p_select = gr.Dropdown(choices=[],multiselect=True,filterable=True,label='Select upcoming games',info='Selected from source-reported upcoming fixtures; maximum 16.')
                with gr.Row():
                    p_load = gr.Button('Refresh scheduled games',size='sm')
                    p_all = gr.Button('Select requested games',size='sm')
                    p_clear = gr.Button('Clear selection',size='sm')
                p_status = gr.Markdown('Loading provider schedule.')
                with gr.Accordion('View scheduled fixtures',open=False):
                    p_schedule = gr.Dataframe(headers=['League','Kickoff','Home','Away','Status'],datatype=['str']*5,interactive=False,value=[])
                p_go = gr.Button('Analyze Selected Matches',variant='primary',size='lg')
                p_msg = gr.Markdown()
                p_result = gr.Dataframe(headers=_GSI_HEADERS,datatype=['str']*13,interactive=False,value=[],label='Match-by-match probabilities (where verified)',wrap=True)
                p_notes = gr.Textbox(label='Match analysis notes',lines=3,interactive=False)
                p_gpt = gr.Button('Explain with GoogleSaada Agent',size='sm')
                p_gpt_out = gr.Markdown('Optional explanation. Predictions come from actual model outputs.')
                p_rows = gr.State([])
                manual.change(input_status,inputs=manual,outputs=count)
                p_load.click(gs_ui_reload,inputs=[p_day,p_league,p_tz,p_search,p_quantity],outputs=[p_select,p_status,p_schedule,p_rows])
                for control in (p_day,p_league,p_tz):
                    control.change(gs_ui_reload,inputs=[p_day,p_league,p_tz,p_search,p_quantity],outputs=[p_select,p_status,p_schedule,p_rows])
                p_search.change(gs_ui_select,inputs=[p_rows,p_tz,p_search,p_quantity],outputs=[p_select,p_schedule])
                p_quantity.change(gs_ui_select,inputs=[p_rows,p_tz,p_search,p_quantity],outputs=[p_select,p_schedule])
                p_today.click(old.today_for,inputs=p_tz,outputs=p_day)
                p_all.click(gs_ui_select,inputs=[p_rows,p_tz,p_search,p_quantity],outputs=[p_select,p_schedule])
                p_clear.click(clear_match_choices,outputs=p_select)
                p_select.change(gs_ui_cap,inputs=[p_select,p_quantity],outputs=p_select)
                p_go.click(gs_predict_complete,inputs=[p_select,manual,p_profile,p_day,p_league,p_tz,gsi_token,gsi_p_markets],outputs=[p_msg,p_result,p_notes,gsi_token,gsi_quota])
                manual.submit(gs_predict_complete,inputs=[p_select,manual,p_profile,p_day,p_league,p_tz,gsi_token,gsi_p_markets],outputs=[p_msg,p_result,p_notes,gsi_token,gsi_quota])
                p_gpt.click(gs_explanation_notes_only,inputs=[p_select,manual,p_profile,p_day,p_league,p_tz],outputs=p_gpt_out)

            with gr.Tab('🎯 Recommendations'):
                gr.Markdown('### Explore matches by your preferred risk level')
                with gr.Row():
                    r_day = gr.Textbox(value=day_value,label='Match date (YYYY-MM-DD)')
                    r_league = gr.Dropdown([ALL_LEAGUES,*LEAGUES],value=ALL_LEAGUES,label='League')
                    r_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Time zone')
                r_profile = gr.Radio(PROFILES,value='Balanced',label='Risk preference')
                r_button = gr.Button('Find Available Match Analyses',variant='primary')
                r_msg = gr.Markdown()
                r_result = gr.Dataframe(headers=_GSI_HEADERS,
                                        datatype=['str']*13,value=[],interactive=False)
                r_notes = gr.Textbox(label='Important notes',lines=2,interactive=False)
                r_gpt = gr.Button('Explain with GoogleSaada Agent',size='sm')
                r_gpt_out = gr.Markdown('GoogleSaada Agent can explain eligible match analyses.')
                r_gpt.click(gs_explanation_notes_only,
                            inputs=[r_profile,r_day,r_league,r_tz],
                            outputs=r_gpt_out)
                r_button.click(gs_recommend_complete, inputs=[r_profile, r_day, r_league, r_tz, gsi_token], outputs=[r_msg, r_result, r_notes, gsi_token, gsi_quota])

            # --------------------------------------------------------------
            # 3. ASK SOCCER AI — EXISTING BOUNDED OFFLINE REQUEST PARSER
            # --------------------------------------------------------------
            with gr.Tab('💬 GoogleSaada Agent'):
                gr.Markdown('### Ask about today’s matches')
                gr.Markdown('GPT-exclusive chat for football questions, recent scores, and research. Live match data is source-dependent; verified model predictions remain in Predict Matches.')
                q_prompt = gr.Textbox(label='Your match question',lines=5,
                                      placeholder='Show me matches available today')
                with gr.Row():
                    q_day = gr.Textbox(value=day_value,label='Match date (YYYY-MM-DD)')
                    q_league = gr.Dropdown([ALL_LEAGUES,*LEAGUES],value=ALL_LEAGUES,label='League')
                    q_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Time zone')
                q_profile = gr.Radio(PROFILES,value='Balanced',label='Analysis preference')
                gsi_q_markets = gr.CheckboxGroup(choices=list(_GSI_MARKETS), value=list(_GSI_MARKETS), label="Choose prediction types")
                q_send = gr.Button('Ask GoogleSaada Agent',variant='primary')
                q_msg = gr.Markdown()
                q_result = gr.Dataframe(headers=_GSI_HEADERS,
                                        datatype=['str']*13,interactive=False,value=[])
                q_notes = gr.Textbox(label='GoogleSaada Agent response and notes',lines=4,interactive=False)
                q_send.click(gs_agent_complete, inputs=[q_prompt, q_profile, q_day, q_league, q_tz, gsi_token, gsi_q_markets], outputs=[q_msg, q_result, q_notes, gsi_token, gsi_quota])
                q_prompt.submit(gs_agent_complete, inputs=[q_prompt, q_profile, q_day, q_league, q_tz, gsi_token, gsi_q_markets], outputs=[q_msg, q_result, q_notes, gsi_token, gsi_quota])

            # --------------------------------------------------------------
            # 4. UPCOMING DAILY MATCH SCHEDULE — ONLY BEFORE KICKOFF
            # --------------------------------------------------------------
            with gr.Tab('📅 Daily Fixtures'):
                gr.Markdown('### Browse upcoming games on a chosen day')
                with gr.Row():
                    f_day = gr.Textbox(value=day_value,label='Date (YYYY-MM-DD)')
                    f_league = gr.Dropdown([_gsi_world.ALL, *_gsi_world.COMPETITIONS], value='All available competitions', label='League')
                    f_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Time zone')
                f_search = gr.Textbox(label='Find a team',placeholder='Optional team name')
                f_button = gr.Button('Show Upcoming Fixtures',variant='primary')
                f_status = gr.Markdown()
                f_table = gr.Dataframe(headers=['League','Kickoff','Home','Away','Status'],
                                       datatype=['str']*5,interactive=False,value=[])
                f_rows = gr.State([])
                f_button.click(gs_fixtures_complete, inputs=[f_day, f_league, f_tz, f_search], outputs=[f_table, f_status, f_rows])
                f_search.change(gs_fixture_search_complete, inputs=[f_rows, f_tz, f_search], outputs=[f_table])

            # --------------------------------------------------------------
            # 5. ADDITIONAL SERVICE — SCOREBOARD LAST
            # --------------------------------------------------------------
            with gr.Tab('🏆 Live Scores & Results') as gsi_scores_tab:
                gr.Markdown('### Live scores and final match results')
                gr.Markdown('Follow scores reported by the match provider. Final matches appear here, not in prediction choices.')
                with gr.Row():
                    s_day = gr.Textbox(value=day_value,label='Score date (YYYY-MM-DD)')
                    s_league = gr.Dropdown([_gsi_world.ALL, *_gsi_world.COMPETITIONS], value='All available competitions', label='League')
                    s_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Time zone')
                s_filter = gr.Radio(['All matches', 'Live only', 'Finished only', 'Live & finished'], value='Live only', label='Which scores to show')
                gsi_score_search = gr.Textbox(label="Search live scores by team or competition")
                s_button = gr.Button('Refresh Scores',variant='primary')
                s_status = gr.Markdown('Choose a date and press Refresh Scores.')
                s_html = gr.HTML('Match scores will appear here.')
                s_loaded = gr.State(False)
                s_button.click(gs_scores_complete, inputs=[s_day, s_league, s_tz, s_filter, gsi_score_search], outputs=[s_html, s_status, s_loaded])
                s_filter.change(gs_score_refresh_complete, inputs=[s_day, s_league, s_tz, s_filter, s_loaded, gsi_score_search], outputs=[s_html, s_status, s_loaded])
                s_tick = gr.Timer(value=180)
                s_tick.tick(gs_score_refresh_complete, inputs=[s_day, s_league, s_tz, s_filter, s_loaded, gsi_score_search], outputs=[s_html, s_status, s_loaded])
                gsi_score_search.change(gs_score_refresh_complete, inputs=[s_day,s_league,s_tz,s_filter,s_loaded,gsi_score_search], outputs=[s_html,s_status,s_loaded])
                gsi_scores_tab.select(gs_scores_complete, inputs=[s_day,s_league,s_tz,s_filter,gsi_score_search], outputs=[s_html,s_status,s_loaded])

            with gr.Tab('📖 How to Use GoogleSaada'):
                gr.Markdown(GS_GUIDE)

        gr.Markdown('**MANDATORY DISCLAIMER:** ' + DISCLAIMER)
        # Only the prediction tab loads on initial page view. The other tabs
        # request their own dates/fixtures when a user opens their workflow.
        demo.load(gs_ui_reload,
                  inputs=[p_day,p_league,p_tz,p_search,p_quantity],
                  outputs=[p_select,p_status,p_schedule,p_rows])
    return demo



# GOOGLESAADA_STEP25_VALIDATED_BOUNDARY_V2

def _gsi_execute(keys, text, profile, day, league, zone, token, markets):

    import re as _gs25_re

    from googlesaada_request_router_v2 import (
        parse_request as _gs25_parse
    )

    raw = str(text or "").strip()
    selected_keys = list(dict.fromkeys(keys or []))

    def reject(message):
        uid, signed = _gsi_uid(token)
        return (
            message,
            [],
            "No prediction generated or charged. "
            "MANDATORY DISCLAIMER: " + _GSI_DISCLAIMER,
            signed,
            _gsi_quota(uid)
        )

    MAXIMUM = 16

    if len(selected_keys) > MAXIMUM:
        return reject("Maximum 16 matches per request.")

    # Count explicit manual fixture separators.
    manual_count = len(
        _gs25_re.findall(
            r"\bvs?\.?(?=\s)",
            raw,
            flags=_gs25_re.I
        )
    )

    if manual_count + len(selected_keys) > MAXIMUM:
        return reject("Maximum 16 matches per request.")

    parsed = None

    if raw:
        parsed = _gs25_parse(raw, timezone=zone)
        status = parsed.get("status")

        if status == "LIMIT_EXCEEDED":
            return reject("Maximum 16 matches per request.")

        if status == "INVALID_COUNT":
            return reject("Request between 1 and 16 matches.")

        if status == "INVALID_TIMEZONE":
            return reject("Invalid timezone.")

    # Parse count directly at the request boundary.
    # Allows "16 upcoming matches", "10 games tomorrow",
    # "five matches", and similar phrasing.
    count_expression = _gs25_re.search(
        r"\b("
        r"\d{1,3}|one|two|three|four|five|six|seven|"
        r"eight|nine|ten|eleven|twelve|thirteen|"
        r"fourteen|fifteen|sixteen"
        r")\s+"
        r"(?:(?:upcoming|scheduled|future|next)\s+)*"
        r"(?:matches?|games?|fixtures?|predictions?|picks?)\b",
        raw,
        flags=_gs25_re.I
    )

    words = {
        "one":1, "two":2, "three":3, "four":4,
        "five":5, "six":6, "seven":7, "eight":8,
        "nine":9, "ten":10, "eleven":11,
        "twelve":12, "thirteen":13,
        "fourteen":14, "fifteen":15, "sixteen":16
    }

    requested = None

    if count_expression:
        word = count_expression.group(1).casefold()

        requested = (
            int(word) if word.isdigit() else words[word]
        )

        if not 1 <= requested <= MAXIMUM:
            return reject("Request between 1 and 16 matches.")

    if requested is not None and manual_count == 0:
        if len(selected_keys) + requested > MAXIMUM:
            return reject("Combined request exceeds 16 matches.")

    # Do not allow the old bridge to silently mishandle a
    # mixture of selected keys and manual fixtures.
    if selected_keys and manual_count:
        return reject(
            "Please submit selected and manual fixtures separately."
        )

    # Preserve manual input exactly. The existing backend
    # remains responsible for authentic fixture matching.
    if manual_count:
        return _gsi_execute_original(
            selected_keys, raw, profile, day,
            league, zone, token, markets
        )

    # Route counted discovery requests into the format accepted
    # by the legacy executor.
    #
    # IMPORTANT: the existing engine does not yet guarantee
    # that ranking/date/market intent survives this translation.
    # These requests remain staging-only until end-to-end tested.
    if requested is not None and not selected_keys:
        raw = f"predict {requested} matches"

    return _gsi_execute_original(
        selected_keys, raw, profile, day,
        league, zone, token, markets
    )

if __name__ == '__main__':
    app = build_app()
    # Localhost-only Colab preview. Keep ALL existing preview files intact.
    app.launch(server_name='127.0.0.1', server_port=7867,
               share=False, show_error=False)

# ============================================================
# GOOGLESAADA STEP 25 — SAFE REQUEST BOUNDARY
# Original prediction, authentication, and quota logic retained.
# ============================================================

