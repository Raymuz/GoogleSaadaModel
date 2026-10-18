"""Soccer AI — private UI refresh: live daily fixtures and results.

Scoreboard and fixture discovery use an ESPN public endpoint on demand.
Predictions remain available ONLY for the previously audited, saved research
fixtures. Unmapped/new fixtures never receive synthesized model probabilities.
No GPT, authorization change, historical writes, or training.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from threading import RLock
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import csv
import hashlib
import importlib.util
import json
import os
import re
import unicodedata

ROOT = Path(__file__).resolve().parent
REPORTS = Path(os.environ.get('SOCCER_AI_REPORTS_DIR', '/content/soccer_ai_live_staging/reports')).resolve()
ORIGINAL_APP = ROOT / 'app_private_step13a.py'
TZ_CHOICES = ['Africa/Douala', 'America/Chicago', 'UTC', 'Europe/London',
              'America/New_York', 'Europe/Paris', 'Africa/Lagos']
LEAGUES = {'Premier League':'eng.1','La Liga':'esp.1','Bundesliga':'ger.1'}
ALL_LEAGUES = 'All supported leagues'
PROFILES = ['Low','Medium','High','Balanced']
MARKET_LABELS = {
    'FT_1X2_HOME':'Home win', 'FT_1X2_DRAW':'Draw', 'FT_1X2_AWAY':'Away win',
    'FT_OVER_2_5':'Over 2.5 goals', 'FT_UNDER_2_5':'Under 2.5 goals',
    'FT_BTTS_YES':'Both teams score — Yes', 'FT_BTTS_NO':'Both teams score — No',
}
DISCLAIMER = ('These model estimates are experimental and based on older historical data. '
              'They are not verified betting recommendations.')


def norm(s):
    s=unicodedata.normalize('NFKD',str(s))
    s=''.join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r'[^a-z0-9]+',' ',s.casefold()).strip()


def utc(text):
    dt=datetime.fromisoformat(str(text).replace('Z','+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Time zone missing from source timestamp')
    return dt.astimezone(timezone.utc)


def score_of(value):
    if isinstance(value,dict): value=value.get('value')
    if isinstance(value,bool) or value is None: return None
    try:
        n=float(value)
        if n>=0 and n.is_integer():return int(n)
    except (ValueError,TypeError,OverflowError): pass
    return None


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
    return h.hexdigest()


def import_module(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path))
    if spec is None or spec.loader is None: raise RuntimeError('Private backend is not available')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_event(event,league_code):
    """Parse one provider fixture; no local identity guesses or fake scores."""
    if not isinstance(event,dict): return None
    mid=str(event.get('id','')).strip()
    if not re.fullmatch(r'\d{4,20}',mid): return None
    comp=event.get('competitions') or []
    if not isinstance(comp,list) or not comp or not isinstance(comp[0],dict):return None
    teams=comp[0].get('competitors') or []
    if not isinstance(teams,list):return None
    sides={}
    for item in teams:
        if not isinstance(item,dict):continue
        side=item.get('homeAway')
        if side in ('home','away') and side not in sides:
            name=(item.get('team') or {}).get('displayName','')
            if not isinstance(name,str) or not name.strip():return None
            sides[side]=(name.strip(),score_of(item.get('score')))
    if set(sides)!={'home','away'}:return None
    dt=utc(event['date'])
    state=((event.get('status') or {}).get('type') or {})
    short_state=str(state.get('state','')).lower()
    status_name=str(state.get('name','')).upper()
    completed=state.get('completed') is True
    description=str(state.get('shortDetail') or state.get('description') or '').strip()
    if any(flag in (status_name+' '+description).upper() for flag in ('POSTPON','CANCEL','ABANDON','SUSPEND')):
        phase='OTHER'
    elif completed and short_state=='post': phase='FINAL'
    elif short_state=='in': phase='LIVE'
    elif short_state=='pre' and status_name in ('STATUS_SCHEDULED','STATUS_PRE_GAME','STATUS_CREATED',''):
        phase='UPCOMING'
    else: phase='OTHER'
    hs,as_=sides['home'][1],sides['away'][1]
    outcome=''
    if phase=='FINAL' and hs is not None and as_ is not None:
        outcome='Home win' if hs>as_ else ('Away win' if as_>hs else 'Draw')
    return {'key':league_code+'|'+mid,'source':'ESPN','league_code':league_code,
            'league':next((n for n,c in LEAGUES.items() if c==league_code),league_code),
            'source_match_id':mid,'home':sides['home'][0],'away':sides['away'][0],
            'home_score':hs,'away_score':as_,'kickoff_utc':dt.isoformat(),
            'phase':phase,'description':description,'outcome':outcome}


class DailyFixtures:
    """Provider date discovery, query-local timezones, short server cache."""
    def __init__(self,transport=None):
        self.transport=transport or self._download
        self.cache={}
        self.lock=RLock()

    @staticmethod
    def _download(code,day):
        # Fixed HTTPS host/league allowlist, not a URL derived from user text.
        if code not in LEAGUES.values() or not re.fullmatch(r'\d{8}',day):
            raise ValueError('Unsupported league or provider date')
        url=('https://site.api.espn.com/apis/site/v2/sports/soccer/'+code+
             '/scoreboard?'+urlencode({'dates':day,'limit':500}))
        req=Request(url,headers={'User-Agent':'Soccer-AI-Private-Research/1.0',
                                 'Accept':'application/json'})
        with urlopen(req,timeout=9) as response:
            if response.status!=200:raise RuntimeError('Fixture provider unavailable')
            blob=response.read(5*1024*1024+1)
            if len(blob)>5*1024*1024:raise ValueError('Oversized provider response')
            payload=json.loads(blob.decode('utf-8'))
        if not isinstance(payload,dict) or not isinstance(payload.get('events'),list):
            raise RuntimeError('Fixture provider returned an incompatible response')
        return payload

    def _one(self,code,ymd,force=False):
        import time
        key=(code,ymd)
        with self.lock:
            cached=self.cache.get(key)
            if not force and cached and time.monotonic()-cached[0]<90:
                return cached[1]
        payload=self.transport(code,ymd)
        if not isinstance(payload,dict) or not isinstance(payload.get('events'),list):
            raise RuntimeError('Fixture source returned invalid data')
        with self.lock:self.cache[key]=(time.monotonic(),payload)
        return payload

    def day(self,day_text,league_text,time_zone,force=False):
        if time_zone not in TZ_CHOICES:raise ValueError('Select a supported time zone')
        if league_text!=ALL_LEAGUES and league_text not in LEAGUES:
            raise ValueError('Choose a supported league')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(day_text)):
            raise ValueError('Enter a date as YYYY-MM-DD')
        target=date.fromisoformat(day_text)
        zone=ZoneInfo(time_zone)
        codes=[LEAGUES[league_text]] if league_text in LEAGUES else list(LEAGUES.values())
        # ±1 UTC day to correctly cover non-UTC local calendar days.
        dates=[(target+timedelta(days=n)).strftime('%Y%m%d') for n in (-1,0,1)]
        calls=[(c,d) for c in codes for d in dates]
        collection=[];errors=[]
        with ThreadPoolExecutor(max_workers=min(6,len(calls))) as pool:
            futures={pool.submit(self._one,c,d,force):(c,d) for c,d in calls}
            for f in as_completed(futures):
                code,ymd=futures[f]
                try:
                    payload=f.result()
                    for event in payload['events']:
                        try:
                            match=parse_event(event,code)
                            if match and utc(match['kickoff_utc']).astimezone(zone).date()==target:
                                collection.append(match)
                        except (KeyError,ValueError,TypeError):continue
                except Exception as exc:
                    errors.append(f'{code} {ymd}: {type(exc).__name__}')
        # On any partial provider failure do not pretend the requested day's
        # fixture schedule is complete. Fail closed for current predictions.
        if errors:raise RuntimeError('Some fixtures could not be refreshed. Please retry later.')
        by_key={}
        for row in collection:
            if row['key'] in by_key and row!=by_key[row['key']]:
                raise RuntimeError('Conflicting live fixture records; please refresh')
            by_key[row['key']]=row
        return sorted(by_key.values(),key=lambda x:(x['kickoff_utc'],x['league'],x['key']))


class SafeSnapshot:
    """Keep original Step 13A safety chain and exact ESPN source linkage."""
    def __init__(self):
        old=import_module('soccer_original_step13a_readonly',ORIGINAL_APP)
        self.service=old.ResearchUIService()
        self.lookup={}
        latest=sorted(REPORTS.glob('step12b2a_manifest_*.json'))
        if not latest:raise RuntimeError('Original fixture identity evidence unavailable')
        manifest=json.loads(latest[-1].read_text(encoding='utf-8'))
        path=Path(manifest['upcoming_fixture_csv']).resolve()
        if REPORTS not in path.parents or not path.is_file() or sha(path)!=manifest['upcoming_fixture_csv_sha256']:
            raise RuntimeError('Original match source identity file failed validation')
        self.file=path;self.digest=sha(path)
        with path.open(newline='',encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                if r.get('Research_Gate')!='EXACT_PAIR_RESEARCH_FEATURES_ONLY':continue
                source=str(r.get('Source','')).upper()
                comp=str(r.get('Source_Competition_ID',''))
                pid=str(r.get('Source_Match_ID',''))
                rid=str(r.get('Research_Feature_Row_ID',''))
                if source!='ESPN' or comp not in LEAGUES.values() or not pid or not rid:
                    continue
                k=(source,comp,pid)
                if k in self.lookup:raise RuntimeError('Duplicate provider fixture identity')
                expected='research:12b2a:'+hashlib.sha256((r['Source']+'|'+comp+'|'+pid).encode()).hexdigest()[:20]
                if rid!=expected:raise RuntimeError('Original synthetic fixture ID does not match source')
                self.lookup[k]=r

    def verified_research_id(self,item):
        if item['phase']!='UPCOMING' or utc(item['kickoff_utc'])<=datetime.now(timezone.utc):return None
        if sha(self.file)!=self.digest:return None
        row=self.lookup.get(('ESPN',item['league_code'],item['source_match_id']))
        if not row:return None
        if norm(row['Home_Team'])!=norm(item['home']) or norm(row['Away_Team'])!=norm(item['away']):
            return None
        if abs((utc(row['Kickoff_UTC'])-utc(item['kickoff_utc'])).total_seconds())>120:
            return None
        rid=row['Research_Feature_Row_ID']
        if rid not in self.service.router['research_market_catalog_by_fixture']:return None
        return rid


DAILY=DailyFixtures()
SNAPSHOT=None
SNAPSHOT_ERROR=''

def load_snapshot():
    global SNAPSHOT,SNAPSHOT_ERROR
    try:
        SNAPSHOT=SafeSnapshot();SNAPSHOT_ERROR=''
    except (OSError,KeyError,ValueError,RuntimeError,TypeError) as exc:
        SNAPSHOT=None;SNAPSHOT_ERROR=type(exc).__name__
    return SNAPSHOT


def local_time(item,tz):
    return utc(item['kickoff_utc']).astimezone(ZoneInfo(tz)).strftime('%H:%M')


def filter_items(rows,search):
    needle=norm(search or '')
    if not needle:return list(rows)
    return [r for r in rows if needle in norm(r['home']+' '+r['away'])]


def ui_tables(rows,tz,search=''):
    now=datetime.now(timezone.utc)
    filtered=filter_items(rows,search)
    upcoming=[r for r in filtered if r['phase']=='UPCOMING' and utc(r['kickoff_utc'])>now]
    table=[[r['league'],local_time(r,tz),r['home'],r['away'],'Scheduled'] for r in upcoming]
    dropdown=[(f"{local_time(r,tz)} · {r['home']} vs {r['away']} ({r['league']})",r['key'])
              for r in upcoming]
    return table,dropdown,score_cards(filtered,tz)


def score_cards(rows,tz):
    style='<style>.sai-scores{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.sai-card{padding:15px;border:1px solid #d5dde3;border-radius:12px;background:var(--block-background-fill,#fff);color:var(--body-text-color,#252525)}.sai-head{font-size:12px;opacity:.72;display:flex;justify-content:space-between;gap:5px}.sai-line{display:flex;justify-content:space-between;font-size:16px;font-weight:600;padding-top:10px}.sai-result{margin-top:12px;font-size:12px;opacity:.8}</style>'
    cards=[]
    for row in rows:
        home=escape(row['home']);away=escape(row['away'])
        hs='—' if row['home_score'] is None or row['phase']=='UPCOMING' else str(row['home_score'])
        ass='—' if row['away_score'] is None or row['phase']=='UPCOMING' else str(row['away_score'])
        status=row['phase'].title() if row['phase']!='OTHER' else (row['description'] or 'Unavailable')
        status=escape(status)
        result=escape(row['outcome'] or (row['description'] if row['phase']=='LIVE' else ''))
        cards.append(f'<div class="sai-card"><div class="sai-head"><span>{escape(row["league"])}</span><span>{local_time(row,tz)} · {status}</span></div><div class="sai-line"><span>{home}</span><span>{hs}</span></div><div class="sai-line"><span>{away}</span><span>{ass}</span></div><div class="sai-result">{result}</div></div>')
    return style+'<div class="sai-scores">'+''.join(cards)+'</div>' if cards else 'No scheduled, live, or completed matches found for this date.'


def load_day(day,league,tz,search='',force=False):
    import gradio as gr
    try:
        rows=DAILY.day(day,league,tz,force=force)
        table,choices,cards=ui_tables(rows,tz,search)
        status=f'**{len(choices)} upcoming matches** · {len(rows)} total scheduled/live/completed · {day} ({tz})'
        return table,gr.update(choices=choices,value=[]),cards,status,rows
    except (ValueError,RuntimeError) as exc:
        # Clear any previously cached UI state when provider refresh fails.
        return ([],gr.update(choices=[],value=[]),'Scores are temporarily unavailable.',
                '**Unable to refresh fixtures.** Check your date or try again.',[])


def filter_day(rows,tz,search):
    import gradio as gr
    table,choices,cards=ui_tables(rows or [],tz,search)
    return table,gr.update(choices=choices,value=[]),cards


def today_for(tz):
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def periodic_refresh(day,league,tz,search,follow_today):
    # Follow the current local date automatically unless browsing another date.
    chosen_day=today_for(tz) if follow_today else day
    values=load_day(chosen_day,league,tz,search,force=False)
    return (chosen_day,*values)


def fresh_matching(rows,keys,manual_text):
    # Return selected provider records without a UI-imposed match maximum.
    upcoming={r['key']:r for r in rows if r['phase']=='UPCOMING' and utc(r['kickoff_utc'])>datetime.now(timezone.utc)}
    chosen=set(keys or [])
    unknown=[]
    for line in (manual_text or '').splitlines():
        line=line.strip()
        if not line:continue
        m=re.fullmatch(r'(.{2,120}?)\s+(?:vs\.?|versus|v\.?)\s+(.{2,120}?)',line,flags=re.I)
        if not m:
            unknown.append(line+' — use Home vs Away');continue
        hm,aw=norm(m.group(1)),norm(m.group(2))
        matches=[k for k,r in upcoming.items() if norm(r['home'])==hm and norm(r['away'])==aw]
        if len(matches)==1:chosen.add(matches[0])
        else:unknown.append(line+' — not found or ambiguous for the selected date')
    for k in (keys or []):
        if k not in upcoming:unknown.append(str(k)+' — unavailable or already started')
    return [upcoming[k] for k in sorted(chosen) if k in upcoming],unknown


def predictions(keys,manual_text,profile,day,league,tz):
    # Refresh provider status again immediately before prediction selection.
    try:
        rows=DAILY.day(day,league,tz,force=True)
        selected,held=fresh_matching(rows,keys,manual_text)
        if not selected and not held:return 'Choose one or more upcoming matches.',[],DISCLAIMER
        result=[];candidates=[]
        for row in selected:
            rid=SNAPSHOT.verified_research_id(row) if SNAPSHOT else None
            if rid:candidates.append((rid,row))
            else:held.append(f"{row['home']} vs {row['away']} — prediction not available yet")
        if SNAPSHOT and candidates:
            # The original audited orchestrator has a five-per-call safety bound.
            # Respect it via batching; do not silently truncate user selections.
            for start in range(0,len(candidates),5):
                subset=candidates[start:start+5]
                output,_=SNAPSHOT.service.request({'mode':'my_matches','profile':profile,
                           'fixture_ids':[rid for rid,_ in subset],'max_matches':5})
                if (output.get('production_predictions_enabled') is not False or
                        output.get('public_recommendations')!=[] or
                        output.get('published_betting_picks')!=[]):
                    raise RuntimeError('Unsafe research output')
                found={item['fixture_id']:item for item in output.get('results',[])}
                for rid,fixture in subset:
                    observed=found.get(rid)
                    if not observed or set(observed.get('markets',{}))!=set(MARKET_LABELS):
                        held.append(f"{fixture['home']} vs {fixture['away']} — prediction unavailable")
                        continue
                    for code,label in MARKET_LABELS.items():
                        p=observed['markets'][code]['raw_uncalibrated_probability']
                        if isinstance(p,bool) or not isinstance(p,(float,int)) or not 0<=p<=1:
                            raise RuntimeError('Invalid model probability')
                        result.append([fixture['home']+' vs '+fixture['away'],label,f'{p:.1%}'])
        held_text='\n'.join('• '+h for h in held)
        msg=f'**{len(result)//7} match predictions available** from {len(selected)} selected upcoming matches.'
        if held:msg+=f' {len(held)} match requests could not be predicted.'
        return msg,result,(held_text+'\n\n' if held_text else '')+DISCLAIMER
    except Exception:
        # Do not leak internal exceptions, stack traces, JSON, or secrets into UI.
        return 'Predictions are temporarily unavailable. Please refresh and try again.',[],DISCLAIMER


def recommendations(profile,day,league,tz):
    if SNAPSHOT is None:
        return 'Recommendations are temporarily unavailable.',[],DISCLAIMER
    try:
        rows=DAILY.day(day,league,tz,force=True)
        # Only permit saved research observations for matching LIVE provider
        # fixtures; never interpret new fixture data as precomputed predictions.
        eligible={SNAPSHOT.verified_research_id(r):r for r in rows
                  if SNAPSHOT.verified_research_id(r)}
        out,_=SNAPSHOT.service.request({'mode':'recommend','profile':profile,'max_matches':5})
        if out.get('production_predictions_enabled') is not False:return 'Results unavailable.',[],DISCLAIMER
        result=[]
        for o in out.get('results',[]):
            rid=o['fixture_id'];fixture=eligible.get(rid)
            if not fixture:continue
            p=o['raw_uncalibrated_probability']
            if isinstance(p,bool) or not isinstance(p,(int,float)) or not 0<=p<=1:continue
            result.append([f"{fixture['home']} vs {fixture['away']}",
                           MARKET_LABELS.get(o.get('market_code'),'Research market'),f'{p:.1%}'])
        return (f'**{len(result)} research candidates available for this date**' if result else
                'No eligible research recommendations for this date and risk preference.'),result,DISCLAIMER
    except Exception:
        return 'Recommendations are temporarily unavailable.',[],DISCLAIMER


def assistant_request(prompt,profile,day,league,tz):
    if not isinstance(prompt,str) or not prompt.strip():
        return 'Enter your question.',[],DISCLAIMER
    if SNAPSHOT is None:return 'Research assistant is temporarily unavailable.',[],DISCLAIMER
    try:
        # Bounded existing assistant, no GPT calls, no invented predictions.
        if len(prompt)>1000: return 'Please shorten your question.',[],DISCLAIMER
        rows=DAILY.day(day,league,tz,force=True)
        eligible={SNAPSHOT.verified_research_id(r):r for r in rows
                  if SNAPSHOT.verified_research_id(r)}
        out,_=SNAPSHOT.service.request({'mode':'assistant','profile':profile,
                                        'prompt':prompt,'date_utc':day,'max_matches':5})
        listed=[]
        for item in out.get('results',[]):
            fixture=eligible.get(item.get('fixture_id'))
            if fixture and 'raw_uncalibrated_probability' in item:
                listed.append([fixture['home']+' vs '+fixture['away'],
                               MARKET_LABELS.get(item.get('market_code'),'Research market'),
                               f"{float(item['raw_uncalibrated_probability']):.1%}"])
        return (f'{len(listed)} matching research results.' if listed else
                'No matching predictions are available. Try selecting a fixture on the Predict Matches tab.'),listed,DISCLAIMER
    except Exception:
        return 'This request could not be matched to available fixtures. Try a date and a team name.',[],DISCLAIMER


def build_app():
    import gradio as gr
    today=today_for('Africa/Douala')
    with gr.Blocks(title='Soccer AI — Fixtures, Predictions & Live Scores') as demo:
        gr.HTML('<div style="padding:18px 20px;background:linear-gradient(110deg,#103448,#17745b);color:white;border-radius:14px">'
                '<h1 style="color:white;margin:0;font-size:28px">⚽ Soccer AI</h1>'
                '<p style="margin:5px 0 0;color:#e6ffef">Today’s matches · Match analysis · Live scores & results</p></div>')
        gr.Markdown('Select a day and league to explore scheduled matches. Scores update when you refresh.')
        with gr.Row():
            day=gr.Textbox(label='Match date',value=today,placeholder='YYYY-MM-DD',scale=2)
            tz=gr.Dropdown(TZ_CHOICES,value='Africa/Douala',label='Your time zone',scale=2)
            league=gr.Dropdown([ALL_LEAGUES,*LEAGUES.keys()],value=ALL_LEAGUES,label='League',scale=2)
            refresh=gr.Button('Refresh games & scores',variant='primary',scale=1)
        with gr.Row():
            today_btn=gr.Button('Today',size='sm')
            search=gr.Textbox(label='Find a team',placeholder='Type team name to filter this day’s games',scale=4)
            follow=gr.Checkbox(value=True,label='Follow today automatically',scale=1)
        status=gr.Markdown('Choose a date and refresh to see current fixtures.')
        all_rows=gr.State([])
        with gr.Tabs():
            with gr.Tab('Predict Matches'):
                gr.Markdown('Select as many upcoming games as you like. Matches already started or completed are excluded.')
                fixtures=gr.Dataframe(headers=['League','Kickoff (selected time zone)','Home','Away','Status'],
                                       datatype=['str']*5,value=[],interactive=False,label='Upcoming games for this date')
                choose=gr.Dropdown(choices=[],multiselect=True,filterable=True,label='Select matches to analyze')
                manual=gr.Textbox(lines=3,label='Or type matches (one per line)',placeholder='Home Team vs Away Team')
                profile=gr.Radio(PROFILES,value='Balanced',label='Analysis preference')
                predict=gr.Button('Analyze my selected matches',variant='primary')
                pred_notice=gr.Markdown()
                pred_table=gr.Dataframe(headers=['Match','Outcome','Estimated probability'],
                                        datatype=['str']*3,value=[],interactive=False,label='Available match analysis')
                pred_expl=gr.Textbox(label='Notes about selected matches',interactive=False,lines=3)
            with gr.Tab('Scores & Results'):
                gr.Markdown('Final scores include the match result. Live games show the score reported by the provider. Refresh for updates.')
                score_html=gr.HTML('Select a date to see scores and match results.')
            with gr.Tab('Match Recommendations'):
                gr.Markdown('Explore model research for the date and analysis preference you selected. Some days have no available estimates.')
                go_rec=gr.Button('Explore available matches',variant='primary')
                rec_msg=gr.Markdown()
                rec_table=gr.Dataframe(headers=['Match','Research market','Estimated probability'],
                                       datatype=['str']*3,value=[],interactive=False)
                rec_notes=gr.Textbox(label='Important information',interactive=False,lines=2)
            with gr.Tab('Ask Soccer AI'):
                prompt=gr.Textbox(lines=3,label='What matches do you want to explore?',
                                  placeholder='Show me available matches today')
                ask_btn=gr.Button('Search my matches',variant='primary')
                ask_msg=gr.Markdown()
                ask_table=gr.Dataframe(headers=['Match','Outcome','Estimated probability'],
                                       datatype=['str']*3,value=[],interactive=False)
                ask_notes=gr.Textbox(label='Important information',interactive=False,lines=2)
        gr.Markdown('**About these estimates:** '+DISCLAIMER)

        outputs_refresh=[fixtures,choose,score_html,status,all_rows]
        refresh.click(lambda d,l,t,s:load_day(d,l,t,s,force=True),
                      inputs=[day,league,tz,search],outputs=outputs_refresh)
        day.submit(load_day,inputs=[day,league,tz,search],outputs=outputs_refresh)
        day.input(lambda _:False,inputs=day,outputs=follow)
        day.change(load_day,inputs=[day,league,tz,search],outputs=outputs_refresh)
        tz.change(load_day,inputs=[day,league,tz,search],outputs=outputs_refresh)
        league.change(load_day,inputs=[day,league,tz,search],outputs=outputs_refresh)
        search.change(filter_day,inputs=[all_rows,tz,search],outputs=[fixtures,choose,score_html])
        today_btn.click(today_for,inputs=tz,outputs=day).then(
            lambda:True,inputs=[],outputs=follow).then(
            load_day,inputs=[day,league,tz,search],outputs=outputs_refresh)
        predict.click(predictions,inputs=[choose,manual,profile,day,league,tz],
                      outputs=[pred_notice,pred_table,pred_expl])
        go_rec.click(recommendations,inputs=[profile,day,league,tz],
                     outputs=[rec_msg,rec_table,rec_notes])
        ask_btn.click(assistant_request,inputs=[prompt,profile,day,league,tz],
                      outputs=[ask_msg,ask_table,ask_notes])
        prompt.submit(assistant_request,inputs=[prompt,profile,day,league,tz],
                      outputs=[ask_msg,ask_table,ask_notes])
        demo.load(load_day,inputs=[day,league,tz,search],outputs=outputs_refresh)
        tick=gr.Timer(value=180)
        tick.tick(periodic_refresh,inputs=[day,league,tz,search,follow],
                  outputs=[day,*outputs_refresh])
    return demo


if __name__=='__main__':
    app=build_app()
    # Private localhost notebook access only. No public share URL or server secrets.
    app.launch(server_name='127.0.0.1',server_port=7861,share=False,show_error=False)
