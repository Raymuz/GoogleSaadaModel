"""Soccer AI — task-focused private interface reusing the verified Step 13B.1 backend.

Front page: enter unlimited match lines or select upcoming games. Other tasks
have independent tabs, with the live scoreboard LAST. No technical JSON panel.
Only existing audited research fixtures can produce saved probability estimates.
Newly discovered fixtures remain unavailable for prediction until Step 13B.2.
No GPT, training, published picks, or shared links.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import importlib.util

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
ALL_LEAGUES = old.ALL_LEAGUES
TZ_CHOICES = old.TZ_CHOICES
PROFILES = old.PROFILES
LEAGUES = old.LEAGUES
DISCLAIMER = old.DISCLAIMER


def typed_count(text):
    lines = [line.strip() for line in str(text or '').splitlines() if line.strip()]
    count = len(lines)
    return (f'**{count} match request' + ('s' if count != 1 else '') +
            '** entered. You can add more lines; there is no match-count limit.')


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
    # ORIGINAL prediction & identity integrity gate: no new model guesses.
    return old.predictions(keys, free_text, profile, day, league, tz)


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
    heading = """<style>
    .sai-header{border-radius:18px;background:linear-gradient(110deg,#0b223b,#185d60);padding:22px 26px;color:#fff}
    .sai-header h1{font-size:29px;margin:0;color:#fff;font-weight:750}
    .sai-header p{font-size:14px;margin:7px 0 0;color:#e5f3f5}
    .sai-section{font-size:14px;color:#557086}
    </style><div class="sai-header"><h1>⚽ Soccer AI</h1>
    <p>Predict upcoming matches · Compare research probabilities · Follow live football scores</p></div>"""

    with gr.Blocks(title='Soccer AI — Predict Matches & Live Scores') as demo:
        gr.HTML(heading)

        with gr.Tabs():
            # --------------------------------------------------------------
            # 1. MOST IMPORTANT USER ACTION — ENTER / SELECT MATCHES
            # --------------------------------------------------------------
            with gr.Tab('⚽ Predict Matches'):
                gr.Markdown('### What matches would you like to predict?')
                gr.Markdown('**Enter multiple games below — one match on each line — or choose games from the daily schedule.**')
                with gr.Row(equal_height=False):
                    with gr.Column(scale=5):
                        manual = gr.Textbox(
                            label='Enter your matches', lines=7,
                            placeholder='Home team vs Away team\nAnother home team vs Another away team\nThird home team vs Third away team',
                            info='Paste as many games as you want; one match per line, in Home vs Away format.')
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
                p_rows = gr.State([])

                manual.change(typed_count,inputs=manual,outputs=count)
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
                r_button.click(old.recommendations,
                               inputs=[r_profile,r_day,r_league,r_tz],
                               outputs=[r_msg,r_result,r_notes])

            # --------------------------------------------------------------
            # 3. ASK SOCCER AI — EXISTING BOUNDED OFFLINE REQUEST PARSER
            # --------------------------------------------------------------
            with gr.Tab('💬 Ask Soccer AI'):
                gr.Markdown('### Ask about today’s matches')
                gr.Markdown('Search supported fixtures and existing match analyses using everyday language.')
                q_prompt = gr.Textbox(label='Your match question',lines=5,
                                      placeholder='Show me matches available today')
                with gr.Row():
                    q_day = gr.Textbox(value=day_value,label='Match date (YYYY-MM-DD)')
                    q_league = gr.Dropdown([ALL_LEAGUES,*LEAGUES],value=ALL_LEAGUES,label='League')
                    q_tz = gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Time zone')
                q_profile = gr.Radio(PROFILES,value='Balanced',label='Analysis preference')
                q_send = gr.Button('Ask Soccer AI',variant='primary')
                q_msg = gr.Markdown()
                q_result = gr.Dataframe(headers=['Match','Market','Estimated probability'],
                                        datatype=['str']*3,interactive=False,value=[])
                q_notes = gr.Textbox(label='Additional information',lines=2,interactive=False)
                q_send.click(old.assistant_request,
                             inputs=[q_prompt,q_profile,q_day,q_league,q_tz],
                             outputs=[q_msg,q_result,q_notes])
                q_prompt.submit(old.assistant_request,
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
    app.launch(server_name='127.0.0.1', server_port=7862,
               share=False, show_error=False)
