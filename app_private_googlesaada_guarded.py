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
    if mod.load_snapshot() is None:
        raise RuntimeError('Original research evidence is unavailable or invalid')
    return mod


old = load_base()
BRIDGE_FILE = ROOT / 'backend/dynamic_ui_bridge_v1.py'
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

def _googlesaada_agent_text(result):
    if result.get('status')=='PASS_PRIVATE_GPT_EXPLANATION':
        return result['text'] + '\n\n*Experimental research only; not a verified betting recommendation.*'
    if result.get('status')=='HOLD_NO_VERIFIED_MODEL_ROWS':
        return 'GoogleSaada Agent can only explain matches with available model estimates.'
    return 'GoogleSaada Agent is temporarily unavailable. The match estimates remain unchanged.'


def explain_matches_googlesaada(keys, raw, risk, day, league, zone):
    msg,rows,note=bridge.predictions_or_recommendations(
        keys,raw,risk,day,league,zone,parse_recommendation_prompt)
    if not rows:
        return 'No eligible upcoming model estimates are available to explain.'
    return _googlesaada_agent_text(_gs_guarded_explanation_call(gpt_module.explain_verified_rows, rows,GOOGLESAADA_EXPLANATION_MODEL))


def explain_recommendations_googlesaada(risk,day,league,zone):
    msg,rows,note=bridge.recommendations(risk,day,league,zone)
    if not rows:
        return 'No qualifying match analyses are available for GoogleSaada Agent to explain.'
    return _googlesaada_agent_text(_gs_guarded_explanation_call(gpt_module.explain_verified_rows, rows,GOOGLESAADA_EXPLANATION_MODEL))


def ask_googlesaada_agent(prompt,risk,day,league,zone):
    # Reuse the existing deterministic bounded intent router. If it cannot
    # verify a fixture and compute original model rows, do not call GPT.
    msg,rows,note=bridge.assistant_request(prompt,risk,day,league,zone)
    if not rows:
        return msg,rows,note
    result=_gs_guarded_explanation_call(gpt_module.explain_verified_rows, rows,GOOGLESAADA_EXPLANATION_MODEL,question=prompt)
    return msg,rows,_googlesaada_agent_text(result) + '\n\n' + note
ALL_LEAGUES = old.ALL_LEAGUES
TZ_CHOICES = old.TZ_CHOICES
PROFILES = old.PROFILES
LEAGUES = old.LEAGUES
DISCLAIMER = str(old.DISCLAIMER).replace('Soccer AI', 'GoogleSaada Soccer Predictor')


def typed_count(text):
    lines = [line.strip() for line in str(text or '').splitlines() if line.strip()]
    count = len(lines)
    return (f'**{count} match request' + ('s' if count != 1 else '') +
            '** entered. You can add more lines; there is no match-count limit.')



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


def predict_many(keys, free_text, profile, day, league, tz):
    # The latest ten-model on-demand backend, not the original five saved rows.
    return bridge.predictions_or_recommendations(keys, free_text, profile,
                                                  day, league, tz,
                                                  parse_recommendation_prompt)


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

    with gr.Blocks(title='GoogleSaada Soccer Predictor — Matches & Live Scores') as demo:
        gr.HTML(heading)

        with gr.Tabs():
            # --------------------------------------------------------------
            # 1. MOST IMPORTANT USER ACTION — ENTER / SELECT MATCHES
            # --------------------------------------------------------------
            with gr.Tab('⚽ Predict Matches'):
                gr.Markdown('### What matches would you like to predict?')
                gr.Markdown('**Enter matches one per line, or ask for recommendations in everyday language.** For example: *Recommend 5 matches with probabilities above 75%*.')
                with gr.Row(equal_height=False):
                    with gr.Column(scale=5):
                        manual = gr.Textbox(
                            label='Enter matches or ask GoogleSaada Agent for recommendations', lines=7,
                            placeholder='Arsenal vs Chelsea\nMan City vs Newcastle\n\nOR ask: Recommend up to 5 matches with probabilities above 75%',
                            info='Match list: one Home vs Away per line. Recommendation question: one sentence. These are separate request types.')
                        count = gr.Markdown('**0 match requests** entered. You can add more lines; there is no match-count limit.')
                    with gr.Column(scale=5):
                        with gr.Row():
                            p_day = gr.Textbox(label='Game date (YYYY-MM-DD)',value=day_value,scale=2)
                            p_league = gr.Dropdown([ALL_LEAGUES,*LEAGUES],value=ALL_LEAGUES,
                                                   label='League',scale=2)
                        with gr.Row():
                            p_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Your time zone',scale=2)
                            p_today = gr.Button('Today',size='sm',scale=1)
                        p_search = gr.Textbox(label='Search available games',
                                              placeholder='Search for a team name')
                        p_select = gr.Dropdown(choices=[], multiselect=True, filterable=True,
                                               label='Or choose upcoming games',
                                               info='Only matches that have not started are listed.')
                        with gr.Row():
                            p_load = gr.Button('Refresh scheduled games',size='sm')
                            p_all = gr.Button('Select all upcoming',size='sm')
                            p_clear = gr.Button('Clear selection',size='sm')
                with gr.Row():
                    p_profile = gr.Radio(PROFILES,value='Balanced',label='Analysis preference')
                p_go = gr.Button('Analyze My Matches',variant='primary',size='lg')
                p_status = gr.Markdown('Ready to load upcoming games.')
                with gr.Accordion('View today’s upcoming fixtures',open=False):
                    p_schedule = gr.Dataframe(headers=['League','Kickoff','Home','Away','Status'],
                                              datatype=['str']*5,interactive=False,value=[])
                p_msg = gr.Markdown()
                p_result = gr.Dataframe(headers=['Match','Outcome','Estimated probability'],
                                         datatype=['str']*3,interactive=False,value=[],label='Match analysis')
                p_notes = gr.Textbox(label='Match analysis notes',lines=3,interactive=False)
                p_gpt = gr.Button('Explain with GoogleSaada Agent',size='sm')
                p_gpt_out = gr.Markdown('Optional explanation by GoogleSaada Agent. The match estimates come from GoogleSaada Soccer Predictor.')
                p_rows = gr.State([])

                manual.change(input_status,inputs=manual,outputs=count)
                p_load.click(load_prediction_day,
                             inputs=[p_day,p_league,p_tz,p_search],
                             outputs=[p_select,p_status,p_schedule,p_rows])
                p_day.submit(load_prediction_day,
                             inputs=[p_day,p_league,p_tz,p_search],
                             outputs=[p_select,p_status,p_schedule,p_rows])
                p_day.change(load_prediction_day,
                             inputs=[p_day,p_league,p_tz,p_search],
                             outputs=[p_select,p_status,p_schedule,p_rows])
                p_league.change(load_prediction_day,
                                inputs=[p_day,p_league,p_tz,p_search],
                                outputs=[p_select,p_status,p_schedule,p_rows])
                p_tz.change(load_prediction_day,
                            inputs=[p_day,p_league,p_tz,p_search],
                            outputs=[p_select,p_status,p_schedule,p_rows])
                p_search.change(filter_prediction_day,inputs=[p_rows,p_tz,p_search],
                                outputs=[p_select,p_schedule])
                p_today.click(old.today_for,inputs=p_tz,outputs=p_day)
                p_all.click(select_all_upcoming,inputs=[p_rows,p_tz,p_search],outputs=p_select)
                p_clear.click(clear_match_choices,outputs=p_select)
                p_go.click(predict_many,
                           inputs=[p_select,manual,p_profile,p_day,p_league,p_tz],
                           outputs=[p_msg,p_result,p_notes])
                p_gpt.click(explain_matches_googlesaada,
                            inputs=[p_select,manual,p_profile,p_day,p_league,p_tz],
                            outputs=p_gpt_out)

            # --------------------------------------------------------------
            # 2. RECOMMENDATIONS — INDEPENDENT INPUTS
            # --------------------------------------------------------------
            with gr.Tab('🎯 Recommendations'):
                gr.Markdown('### Explore matches by your preferred risk level')
                with gr.Row():
                    r_day = gr.Textbox(value=day_value,label='Match date (YYYY-MM-DD)')
                    r_league = gr.Dropdown([ALL_LEAGUES,*LEAGUES],value=ALL_LEAGUES,label='League')
                    r_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Time zone')
                r_profile = gr.Radio(PROFILES,value='Balanced',label='Risk preference')
                r_button = gr.Button('Find Available Match Analyses',variant='primary')
                r_msg = gr.Markdown()
                r_result = gr.Dataframe(headers=['Match','Market','Estimated probability'],
                                        datatype=['str']*3,value=[],interactive=False)
                r_notes = gr.Textbox(label='Important notes',lines=2,interactive=False)
                r_gpt = gr.Button('Explain with GoogleSaada Agent',size='sm')
                r_gpt_out = gr.Markdown('GoogleSaada Agent can explain eligible match analyses.')
                r_gpt.click(explain_recommendations_googlesaada,
                            inputs=[r_profile,r_day,r_league,r_tz],
                            outputs=r_gpt_out)
                r_button.click(bridge.recommendations,
                               inputs=[r_profile,r_day,r_league,r_tz],
                               outputs=[r_msg,r_result,r_notes])

            # --------------------------------------------------------------
            # 3. ASK SOCCER AI — EXISTING BOUNDED OFFLINE REQUEST PARSER
            # --------------------------------------------------------------
            with gr.Tab('💬 GoogleSaada Agent'):
                gr.Markdown('### Ask about today’s matches')
                gr.Markdown('Ask GoogleSaada Agent about upcoming matches and available predictions.')
                q_prompt = gr.Textbox(label='Your match question',lines=5,
                                      placeholder='Show me matches available today')
                with gr.Row():
                    q_day = gr.Textbox(value=day_value,label='Match date (YYYY-MM-DD)')
                    q_league = gr.Dropdown([ALL_LEAGUES,*LEAGUES],value=ALL_LEAGUES,label='League')
                    q_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Time zone')
                q_profile = gr.Radio(PROFILES,value='Balanced',label='Analysis preference')
                q_send = gr.Button('Ask GoogleSaada Agent',variant='primary')
                q_msg = gr.Markdown()
                q_result = gr.Dataframe(headers=['Match','Market','Estimated probability'],
                                        datatype=['str']*3,interactive=False,value=[])
                q_notes = gr.Textbox(label='GoogleSaada Agent response and notes',lines=4,interactive=False)
                q_send.click(ask_googlesaada_agent,
                             inputs=[q_prompt,q_profile,q_day,q_league,q_tz],
                             outputs=[q_msg,q_result,q_notes])
                q_prompt.submit(ask_googlesaada_agent,
                                inputs=[q_prompt,q_profile,q_day,q_league,q_tz],
                                outputs=[q_msg,q_result,q_notes])

            # --------------------------------------------------------------
            # 4. UPCOMING DAILY MATCH SCHEDULE — ONLY BEFORE KICKOFF
            # --------------------------------------------------------------
            with gr.Tab('📅 Daily Fixtures'):
                gr.Markdown('### Browse upcoming games on a chosen day')
                with gr.Row():
                    f_day = gr.Textbox(value=day_value,label='Date (YYYY-MM-DD)')
                    f_league = gr.Dropdown([ALL_LEAGUES,*LEAGUES],value=ALL_LEAGUES,label='League')
                    f_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Time zone')
                f_search = gr.Textbox(label='Find a team',placeholder='Optional team name')
                f_button = gr.Button('Show Upcoming Fixtures',variant='primary')
                f_status = gr.Markdown()
                f_table = gr.Dataframe(headers=['League','Kickoff','Home','Away','Status'],
                                       datatype=['str']*5,interactive=False,value=[])
                f_rows = gr.State([])
                f_button.click(load_fixture_schedule,
                               inputs=[f_day,f_league,f_tz,f_search],
                               outputs=[f_table,f_status,f_rows])
                f_search.change(search_fixture_schedule,inputs=[f_rows,f_tz,f_search],outputs=f_table)

            # --------------------------------------------------------------
            # 5. ADDITIONAL SERVICE — SCOREBOARD LAST
            # --------------------------------------------------------------
            with gr.Tab('🏆 Live Scores & Results'):
                gr.Markdown('### Live scores and final match results')
                gr.Markdown('Follow scores reported by the match provider. Final matches appear here, not in prediction choices.')
                with gr.Row():
                    s_day = gr.Textbox(value=day_value,label='Score date (YYYY-MM-DD)')
                    s_league = gr.Dropdown([ALL_LEAGUES,*LEAGUES],value=ALL_LEAGUES,label='League')
                    s_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Time zone')
                s_filter = gr.Radio(['All matches','Live only','Finished only','Live & finished'],
                                    value='All matches',label='Which scores to show')
                s_button = gr.Button('Refresh Scores',variant='primary')
                s_status = gr.Markdown('Choose a date and press Refresh Scores.')
                s_html = gr.HTML('Match scores will appear here.')
                s_loaded = gr.State(False)
                s_button.click(load_scores,inputs=[s_day,s_league,s_tz,s_filter],
                               outputs=[s_html,s_status,s_loaded])
                s_filter.change(refresh_loaded_scores,
                                inputs=[s_day,s_league,s_tz,s_filter,s_loaded],
                                outputs=[s_html,s_status,s_loaded])
                s_tick = gr.Timer(value=180)
                s_tick.tick(refresh_loaded_scores,
                            inputs=[s_day,s_league,s_tz,s_filter,s_loaded],
                            outputs=[s_html,s_status,s_loaded])

        gr.Markdown('**Please note:** ' + DISCLAIMER)
        # Only the prediction tab loads on initial page view. The other tabs
        # request their own dates/fixtures when a user opens their workflow.
        demo.load(load_prediction_day,
                  inputs=[p_day,p_league,p_tz,p_search],
                  outputs=[p_select,p_status,p_schedule,p_rows])
    return demo


if __name__ == '__main__':
    app = build_app()
    # Localhost-only Colab preview. Keep ALL existing preview files intact.
    app.launch(server_name='127.0.0.1', server_port=7867,
               share=False, show_error=False)
