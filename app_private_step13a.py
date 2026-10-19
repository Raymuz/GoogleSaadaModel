
"""Soccer AI — source-verified PRIVATE Gradio research prototype (Step 13A).

No inference / GPT / provider access / approvals / betting picks. A snapshot must
have a source-matched Step12C.1/12C.2/12D.1/12D.2 SHA256 evidence chain.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
import hashlib
import importlib.util
import json
import os

ROOT = Path(__file__).resolve().parent
REPORTS = Path(os.environ.get('SOCCER_AI_REPORTS_DIR', '/content/soccer_ai_live_staging/reports')).resolve()
POLICY = ROOT / 'backend/research_policy_gate.py'
ORCH = ROOT / 'backend/research_agent_orchestrator.py'
EXPLAIN = ROOT / 'backend/research_explanation_bridge.py'

HEADERS = ['Fixture', 'Kickoff UTC', 'Research market', 'Raw model probability', 'Evidence']
NOTICE = ('PRIVATE RESEARCH ONLY: historical results are stale, identities are unapproved, '
          'market-specific chronological calibration is unverified. Not a betting recommendation.')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require_report(path, digest):
    p = Path(path).resolve()
    if not p.is_file() or REPORTS not in p.parents or not isinstance(digest, str) or len(digest)!=64 or sha(p)!=digest:
        raise RuntimeError('Research evidence file missing, outside approved folder, or checksum mismatch')
    return p


def match_digest(pattern, digest):
    if not isinstance(digest, str) or len(digest)!=64:
        raise RuntimeError('Invalid source evidence digest')
    matches = [p for p in REPORTS.glob(pattern) if p.is_file() and sha(p)==digest]
    if len(matches)!=1:
        raise RuntimeError('Source evidence chain missing or ambiguous: '+pattern)
    return matches[0]


def load_module(name, path):
    spec=importlib.util.spec_from_file_location(name,str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load approved local backend module')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResearchUIService:
    """Load one audited research snapshot, pin every source hash and recheck each request."""
    def __init__(self):
        manifests=sorted(REPORTS.glob('step12d2_manifest_*.json'))
        if not manifests:
            raise FileNotFoundError('No Step 12D.2 audited research snapshot in the configured reports folder')
        self.d2path=manifests[-1]
        d2=read_json(self.d2path)
        if (d2.get('step')!='12D.2' or
            d2.get('status')!='PASS_OFFLINE_EXPLANATION_AND_PROMPT_INVENTORY' or
            d2.get('production_predictions_enabled') is not False or
            d2.get('original_prompts_approved') is not False or
            d2.get('gpt_calls')!=0 or
            d2.get('market_calibration_verified') is not False or
            d2.get('identity_approvals') is not False):
            raise RuntimeError('Step 12D.2 manifest does not preserve private research-only safeguards')
        d1path=match_digest('step12d1_manifest_*.json',d2['source_step12d1_manifest_sha256'])
        d1=read_json(d1path)
        if (d1.get('step')!='12D.1' or d1.get('status')!='PASS_RESEARCH_ONLY_AGENT_ROUTER'
            or d1.get('gpt_calls')!=0 or d1.get('production_predictions_enabled') is not False):
            raise RuntimeError('Step 12D.1 source rejected')
        d1_result=require_report(d1['result_json'],d1['result_json_sha256'])
        if sha(d1_result)!=d2['source_step12d1_result_sha256']:
            raise RuntimeError('Step12D.2 and original agent result lineage mismatch')
        c1path=match_digest('step12c1_manifest_*.json',d1['source_step12c1_manifest_sha256'])
        c2path=match_digest('step12c2_manifest_*.json',d1['source_step12c2_manifest_sha256'])
        c1=read_json(c1path)
        c2=read_json(c2path)
        if (c1.get('step')!='12C.1' or
            c1.get('status')!='RESEARCH_RISK_ROUTER_COMPLETE_NOT_PRODUCTION_APPROVED' or
            c1.get('production_predictions_enabled') is not False or
            c2.get('step')!='12C.2' or
            c2.get('status')!='PASS_RESEARCH_POLICY_INTEGRITY_NOT_PRODUCTION_APPROVED' or
            c2.get('production_predictions_enabled') is not False or
            c2.get('source_step12c1_manifest_sha256')!=sha(c1path)):
            raise RuntimeError('Risk-source manifest lineage or permissions invalid')
        self.routerpath=require_report(c1['router_json'],c1['router_json_sha256'])
        self.router=read_json(self.routerpath)
        self.policy=load_module('soccer_step13a_policy',POLICY)
        self.orch=load_module('soccer_step13a_orchestrator',ORCH)
        self.bridge=load_module('soccer_step13a_explanation',EXPLAIN)
        pins={self.d2path:sha(self.d2path),d1path:sha(d1path),d1_result:sha(d1_result),
              c1path:sha(c1path),c2path:sha(c2path),self.routerpath:sha(self.routerpath),
              POLICY:sha(POLICY),ORCH:sha(ORCH),EXPLAIN:sha(EXPLAIN)}
        expected={POLICY:d1['research_policy_module_sha256'],
                  ORCH:d2['source_orchestrator_sha256'],
                  EXPLAIN:d2['offline_bridge_sha256']}
        if any(pins[path]!=digest for path,digest in expected.items()):
            raise RuntimeError('A deployed backend module checksum differs from the research audit')
        self.pins=pins
        self._verify()

    def _verify(self):
        for path,digest in self.pins.items():
            if not path.is_file() or sha(path)!=digest:
                raise RuntimeError('Source report/module changed; reload an audited research snapshot')
        policy=self.policy.verify_and_summarize(self.router,datetime.now(timezone.utc),max_matches=5)
        if (policy.get('status')!='PASS_PRIVATE_RESEARCH_ROUTING_WITH_PRODUCTION_BLOCKS' or
            policy.get('production_prediction_approved') is not False or
            policy.get('market_specific_calibration_verified') is not False or
            policy.get('identity_approvals') is not False or
            policy.get('historical_results_refreshed') is not False or
            policy.get('public_recommendations')!=[] or policy.get('betting_picks')!=[]):
            raise RuntimeError('Private research policy gate refused this snapshot')
        return policy

    def choices(self):
        self._verify()
        now=datetime.now(timezone.utc)
        result=[]
        for fid,row in sorted(self.router['research_market_catalog_by_fixture'].items(),
                              key=lambda kv:kv[1].get('kickoff_utc','')):
            if row.get('fixture_status')!='PREMATCH_UNAPPROVED_FROZEN_HISTORY_RESEARCH':
                continue
            kickoff=self.orch._utc(row['kickoff_utc'])
            if kickoff<=now:
                continue
            label=f"{row.get('provider_home','Unknown')} vs {row.get('provider_away','Unknown')} | {kickoff:%Y-%m-%d %H:%M} UTC | {fid}"
            result.append((label,fid))
        return result

    def request(self,request):
        self._verify()
        result=self.orch.orchestrate(request,self.router,self.policy,datetime.now(timezone.utc))
        if (result.get('research_only') is not True or
            result.get('production_predictions_enabled') is not False or
            result.get('public_recommendations')!=[] or
            result.get('published_betting_picks')!=[]):
            raise RuntimeError('Untrusted agent response blocked')
        explanation=self.bridge.build_offline_explanation(result,self.orch)
        if (explanation.get('production_predictions_enabled') is not False or
            explanation.get('gpt_calls')!=0 or explanation.get('published_betting_picks')!=[]):
            raise RuntimeError('Offline explanation safety contract refused')
        return result,explanation

    def status(self):
        policy=self._verify()
        now=datetime.now(timezone.utc)
        active=sum(1 for row in self.router['research_market_catalog_by_fixture'].values()
                   if row.get('fixture_status')=='PREMATCH_UNAPPROVED_FROZEN_HISTORY_RESEARCH'
                   and self.orch._utc(row['kickoff_utc'])>now)
        return {'research_fixture_records':len(self.router['research_market_catalog_by_fixture']),
                'currently_upcoming_research_fixtures':active,
                'frozen_history_age_days':round(policy['history_age_days'],2),
                'live_match_refresh_enabled':False,
                'market_calibration_verified':False,'club_identities_approved':False,
                'gpt_enabled':False,'public_picks':0,
                'snapshot_sha256':self.pins[self.routerpath]}


SERVICE=None
SERVICE_ERROR=''

def start_service():
    global SERVICE,SERVICE_ERROR
    try:
        SERVICE=ResearchUIService()
        SERVICE_ERROR=''
    except (OSError,ValueError,KeyError,RuntimeError,TypeError) as exc:
        SERVICE=None
        SERVICE_ERROR=f'{type(exc).__name__}: {exc}'
    return SERVICE


def _output(mode, request):
    if SERVICE is None:
        return ('**Research snapshot unavailable.** '+SERVICE_ERROR,[],NOTICE)
    try:
        res,ex=SERVICE.request(request)
        rows=[]
        for row in res.get('results',[]):
            fixture=f"{row['home']} vs {row['away']}"
            if 'markets' in row:
                for code,obj in row['markets'].items():
                    rows.append([fixture,row['kickoff_utc'],obj['outcome'],
                                 f"{float(obj['raw_uncalibrated_probability']):.1%}",obj['evidence_method']])
            else:
                rows.append([fixture,row['kickoff_utc'],row['research_hypothesis'],
                             f"{float(row['raw_uncalibrated_probability']):.1%}",'Saved verified model ensemble / Poisson research'])
        held=res.get('held_requests',[])
        description=(f"**{len(res.get('results',[]))} private research fixture(s)** | "
                     f"Status: `{res.get('status','UNKNOWN')}` | "
                     f"{len(held)} held request(s). "
                     "Raw probabilities are **not calibrated** and cannot be published as betting picks.")
        details='\n\n'.join(x['explanation'] for x in ex.get('results',[]))
        if held:
            details+='\n\nHeld inputs:\n'+ '\n'.join(str(x) for x in held)
        if not details.strip():
            details='No qualifying source-backed research results for this request. No results were invented.'
        return description,rows,details+'\n\n'+NOTICE
    except (ValueError,KeyError,TypeError,RuntimeError) as exc:
        return (f'**Request held:** {type(exc).__name__}: {exc}',[],NOTICE)


def recommend(profile='Balanced',max_matches=5,date_utc=''):
    return _output('recommend',{'mode':'recommend','profile':profile,
                    'max_matches':int(max_matches),'date_utc':date_utc or None})


def my_matches(ids=None,text='',profile='Balanced',max_matches=5,date_utc=''):
    return _output('my_matches',{'mode':'my_matches','profile':profile,
                   'fixture_ids':ids or [],'matches_text':text or '',
                   'max_matches':int(max_matches),'date_utc':date_utc or None})


def ask_assistant(prompt='',profile='Balanced',max_matches=5,date_utc=''):
    return _output('assistant',{'mode':'assistant','profile':profile,
                   'prompt':prompt or '', 'max_matches':int(max_matches),
                   'date_utc':date_utc or None})


def list_fixtures():
    if SERVICE is None:
        return []
    try:
        result=[]
        for label,fid in SERVICE.choices():
            result.append([fid,label])
        return result
    except (KeyError,ValueError,RuntimeError):
        return []


def refresh_ui():
    start_service()
    choices=SERVICE.choices() if SERVICE else []
    status=SERVICE.status() if SERVICE else {'status':'RESEARCH_SNAPSHOT_UNAVAILABLE','reason':SERVICE_ERROR}
    return {'choices':choices,'value':[]},status,list_fixtures()


def build_app():
    import gradio as gr
    start_service()
    fixture_choices=SERVICE.choices() if SERVICE else []
    initial_status=SERVICE.status() if SERVICE else {'status':'RESEARCH_SNAPSHOT_UNAVAILABLE','reason':SERVICE_ERROR}
    with gr.Blocks(title='Soccer AI — Private Research Center') as demo:
        gr.HTML('<section style="background:linear-gradient(120deg,#103948,#12664e);'
                'color:#ffffff;padding:24px 28px;border-radius:18px;max-width:1430px;">'
                '<h1 style="color:#ffffff;font-size:30px;margin:0;">⚽ Soccer AI Research Center</h1>'
                '<p style="color:#e0ffea;margin:10px 0 0;">Original ten-model results · Real saved fixtures · Four risk profiles · Offline explanations</p></section>')
        gr.Markdown('**PRIVATE DEVELOPMENT PREVIEW · No live refresh or betting picks.** '
                    'History is stale and club mappings/market calibration are not approved. '
                    'Only source-backed frozen-history research is displayed.')
        with gr.Row():
            with gr.Column(scale=1,min_width=280):
                gr.Markdown('### Settings')
                profile=gr.Radio(['Low','Medium','High','Balanced'],value='Balanced',label='Risk profile')
                def threshold_text(p):
                    if SERVICE is None: return 'Research risk profile configuration unavailable'
                    return f"Research threshold for {p}: **{SERVICE.router['risk_profiles'][p]:.0%}** (not a validated success rate)."
                profile_hint=gr.Markdown(threshold_text('Balanced'))
                profile.change(threshold_text,inputs=profile,outputs=profile_hint)
                count=gr.Slider(minimum=1,maximum=5,step=1,value=5,label='Maximum fixtures')
                date=gr.Textbox(label='Optional UTC date',placeholder='YYYY-MM-DD (leave blank for all future dates)')
                gr.Markdown('**League filtering:** on hold until verified provider league IDs are included in the router. '
                            'No unverified league selection is silently applied.')
                gr.Markdown('**GPT:** disabled until the original prompt version is reviewed. '
                            'Deterministic offline explanations are enabled.')
                reload_btn=gr.Button('Recheck saved snapshot',variant='secondary')
                system_status=gr.JSON(value=initial_status,label='Research backend readiness')
            with gr.Column(scale=2,min_width=440):
                with gr.Tabs():
                    with gr.Tab('Recommend Matches'):
                        gr.Markdown('Apply your risk threshold to **verified source research candidates**. '
                                    'Zero results is a valid outcome.')
                        go_rec=gr.Button('Find private research candidates',variant='primary')
                        rec_msg=gr.Markdown()
                        rec_table=gr.Dataframe(headers=HEADERS,datatype=['str']*5,value=[],interactive=False,label='Research hypothesis list')
                        rec_details=gr.Textbox(label='Offline explanation / restrictions',lines=8,interactive=False)
                    with gr.Tab('Predict My Matches'):
                        gr.Markdown('Select source-audited fixture IDs or type source-matching `Home vs Away` lines. '
                                    'Unknown fixtures remain on hold; no new inference is triggered.')
                        ids=gr.Dropdown(choices=fixture_choices,value=[],multiselect=True,filterable=True,label='Currently upcoming verified fixture IDs')
                        manual=gr.Textbox(lines=3,label='Source-exact home vs away entries (optional)',placeholder='Arsenal vs Chelsea')
                        go_mine=gr.Button('Show seven markets for my fixtures',variant='primary')
                        my_msg=gr.Markdown()
                        my_table=gr.Dataframe(headers=HEADERS,datatype=['str']*5,value=[],interactive=False,label='Seven research markets per matched fixture')
                        my_details=gr.Textbox(label='Offline explanation / held matches',lines=8,interactive=False)
                    with gr.Tab('Research Assistant'):
                        gr.Markdown('Offline, rule-bounded request routing. GPT is **not** called.')
                        ask=gr.Textbox(lines=3,label='Request',placeholder='Recommend matches tomorrow')
                        ask_btn=gr.Button('Review my request',variant='primary')
                        ask_msg=gr.Markdown()
                        ask_table=gr.Dataframe(headers=HEADERS,datatype=['str']*5,value=[],interactive=False,label='Source-backed research results')
                        ask_details=gr.Textbox(label='Offline explanation / restrictions',lines=8,interactive=False)
                    with gr.Tab('Saved Fixtures & System'):
                        gr.Markdown('Fixtures are from the prior audited snapshot, **not** a live provider feed.')
                        fixture_table=gr.Dataframe(headers=['Fixture ID','Saved source matchup and UTC kickoff'],
                                                   datatype=['str','str'],value=list_fixtures(),interactive=False)
        go_rec.click(recommend,inputs=[profile,count,date],outputs=[rec_msg,rec_table,rec_details])
        go_mine.click(my_matches,inputs=[ids,manual,profile,count,date],outputs=[my_msg,my_table,my_details])
        ask_btn.click(ask_assistant,inputs=[ask,profile,count,date],outputs=[ask_msg,ask_table,ask_details])
        ask.submit(ask_assistant,inputs=[ask,profile,count,date],outputs=[ask_msg,ask_table,ask_details])
        def on_reload():
            choice_update,status,table=refresh_ui()
            return gr.update(**choice_update),status,table
        reload_btn.click(on_reload,inputs=[],outputs=[ids,system_status,fixture_table])
    return demo


if __name__=='__main__':
    app=build_app()
    # NO public tunnel. An authenticated deployment requires a separate release step.
    app.launch(server_name='127.0.0.1',share=False,show_error=False)

