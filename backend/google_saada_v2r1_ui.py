"""V2R.1: presentation adaptation + independent free daily model-backed Quick Picks.
No changes to trained weights, source identity, public user quotas or original model probability code.
"""
from __future__ import annotations
from datetime import datetime,timedelta,timezone,date
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo
import json,logging,os,re,unicodedata
import gradio as gr

LOG=logging.getLogger('googlesaada.v2r1')
V2R=None
ENGINE=None
_LOCK=RLock()
_MEM={}
FIELDS=('Match','Home win','Draw','Away win','Over 2.5','Under 2.5','BTTS Yes','BTTS No','Leading 1X2 outcome')
SUMMARY_FIELDS=('Match','Recommended market','Model probability','Status')

def configure(*,v2r,engine_factory):
    global V2R,ENGINE
    V2R=v2r; ENGINE=engine_factory

def _projection(rows):
    if not isinstance(rows,(tuple,list)):return []
    return [list(row[:9]) for row in rows if isinstance(row,(tuple,list)) and len(row)==13]

def hidden():
    return gr.update(visible=False),gr.update(value=[]),gr.update(value=''),gr.update(value='')

def _request(text,league,period,day,zone,page):
    """Bounded natural-language shortcuts. Always source-verify; never generate fixture IDs."""
    raw=str(text or '').strip()
    if not raw or len(raw)>2500:
        return [], 'Enter a match name or a short request (maximum 2,500 characters).'
    if zone not in V2R.TZ_CHOICES:
        return [], 'Choose a supported time zone.'
    query=raw
    explicit_date=None
    pieces=[p.strip() for p in raw.split(',')]
    if len(pieces)==3 and re.search(r'\bvs?\.?\b',pieces[-1],re.I):
        options=[name for name in V2R.COMPETITIONS if V2R.normalize(name)==V2R.normalize(pieces[0])]
        if len(options)!=1:
            return [],'The pasted competition is not uniquely recognized. Choose it from the dropdown.'
        league=options[0]
        query=pieces[-1]
        try:
            explicit_date=date.fromisoformat(pieces[1][:10])
        except (ValueError,TypeError):
            return [],'Use YYYY-MM-DD in the pasted fixture date.'
    # League hints are exact normalized aliases, not an LLM guess.
    # An explicit chosen league takes priority over generic wording.
    if league==V2R.ALL:
        detected=[name for name in V2R.COMPETITIONS if len(name)>3 and V2R.normalize(name) in V2R.normalize(raw)]
        if len(detected)==1:
            league=detected[0]
    reference=datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).date()
    lowered=raw.casefold()
    if explicit_date is not None:
        period='Specific date';day=explicit_date.isoformat()
    elif re.search(r'\btomorrow\b',lowered):
        period='Specific date';day=(reference+timedelta(days=1)).isoformat()
    elif re.search(r'\btoday\b',lowered):
        period='Today'
    # Prompt forms: Show 5 matches, Show me 5 matches, or
    # Show matches involving Arsenal. Limit all answers to verified fixtures.
    requested=re.search(r'\b(?:show|find|list|give me|search)(?:\s+me)?\s+(?:the\s+)?(?:next\s+)?(?:upcoming\s+)?(\d{1,2})\s+(?:upcoming\s+)?(?:matches|match|games|fixtures)\b',lowered)
    count=int(requested.group(1)) if requested else None
    if count is not None and not 1<=count<=16:
        return [],'Request between 1 and 16 matches.'
    involving=re.search(r'\b(?:involving|featuring)\s+([\w -]{2,70})',raw,re.I)
    simple_show=bool(re.search(r'\b(?:show|find|list|give me|search)\b',lowered)) and not re.search(r'\b(?:vs?\.?|versus)\b',lowered)
    if requested or involving or simple_show:
        try:
            query_team=involving.group(1).strip() if involving else ''
            # No Quick Predict league/page controls: search all bounded pages
            # until the request is fulfilled. Every result is provider-verified.
            page_ids=(range(1,len(V2R.LEAGUE_PAGES)+1) if league==V2R.ALL else (1,))
            found={};errors=[]
            for current_page in page_ids:
                batch,issues,_=V2R.discover(league,period,day,zone,query_team,current_page)
                errors.extend(issues)
                for fixture in batch:
                    found[fixture['key']]=fixture
                if len(found)>=(count or 10):
                    break
            matches=sorted(found.values(),key=lambda r:(V2R.utc(r['kickoff_utc']),r['key']))
            selected=matches[:count or 10]
            if not selected:
                return [],'No source-verified upcoming matches match this prompt.'+(' Some source queries failed.' if errors else '')
            return selected,('Partial provider coverage: only source-verified matches were accepted.' if errors else '')
        except Exception as exc:
            return [],'Fixture lookup unavailable: '+type(exc).__name__+': '+str(exc)[:100]
    rows,_,status,_=V2R.direct_lookup(query,league,period,day,zone,page)
    if not rows and league==V2R.ALL:
        # A directly entered fixture can be in any of the nineteen leagues.
        for other_page in range(1,len(V2R.LEAGUE_PAGES)+1):
            if other_page==page:continue
            found,_,message,_=V2R.direct_lookup(query,league,period,day,zone,other_page)
            if found:
                rows,status=found,message
                break
    if explicit_date is not None and rows:
        correct=[r for r in rows if V2R.utc(r['kickoff_utc']).astimezone(ZoneInfo(zone)).date()==explicit_date]
        if len(correct)!=len(rows):
            return [],'Pasted fixture date differs from verified local kickoff.'
    if not rows:
        return [],status
    return rows,''

def find_direct(text,league,period,day,zone,page):
    rows,error=_request(text,league,period,day,zone,page)
    compact=('**%d upcoming source-verified matches found:** '%len(rows)
             + '; '.join(r['home']+' vs '+r['away'] for r in rows[:16])) if rows else error
    # Matches appear as a compact status, without a fixture table or selection UI.
    return rows,compact,str(text or '').strip()

def do_lookup(text,league,period,day,zone,page):
    rows,status,signature=find_direct(text,league,period,day,zone,page)
    return rows,status,signature,gr.update(visible=False),[],''

def direct_predict(text,review,signature,league,period,day,zone,page,profile,markets,token):
    # Reuse verified source IDs only if prompt is unchanged; otherwise re-discover.
    raw=str(text or '').strip()
    if review and raw==str(signature or ''):
        rows=list(review)
    else:
        rows,message,_=find_direct(raw,league,period,day,zone,page)
        if not rows:return message,[], 'No prediction was charged.',token,'',gr.update(visible=False)
    msg,data,note,signed,balance=V2R._run(rows,zone,token,markets,profile)
    projected=_projection(data)
    return msg,projected,note,signed,balance,gr.update(visible=bool(projected))

def basket_predict(box,zone,profile,markets,token):
    msg,data,note,signed,balance=V2R.box_predict(box,zone,profile,markets,token)
    projected=_projection(data)
    return msg,projected,note,signed,balance,gr.update(visible=bool(projected))

def basket_add(rows,box,zone,evt:gr.SelectData):
    old=len(box or [])
    packed=V2R.add_row(rows,box,zone,evt)
    new=len(packed[0]);label='**My Bet Basket: %d/16**'%new
    status=('Match added to your Bet Basket.' if new>old else 'Match is already in your Bet Basket.')
    return (*packed,label,gr.update(visible=False),[],status)

def basket_remove(box,key,zone):
    packed=V2R.remove_box(box,key,zone)
    return (*packed,'**My Bet Basket: %d/16**'%len(packed[0]),gr.update(visible=False),[], 'Basket updated.')

def basket_clear(zone):
    packed=V2R.clear_box(zone)
    return (*packed,'**My Bet Basket: 0/16**',gr.update(visible=False),[], 'Bet Basket cleared.')

def remove_direct(items,num,zone):
    try: index=int(num)-1
    except (TypeError,ValueError):index=-1
    rows,table=V2R.remove_direct(items,index,zone)
    return rows,table,gr.update(visible=bool(rows)),gr.update(visible=False),[],''

def _source_date(value,zone):
    if zone not in V2R.TZ_CHOICES:raise ValueError('Choose a supported time zone')
    today=datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).date()
    if not value:d=today
    else:d=date.fromisoformat(str(value)[:10])
    if not today<=d<=(datetime.now(timezone.utc)+timedelta(hours=168)).astimezone(ZoneInfo(zone)).date():
        raise ValueError('Select a day within the coming seven days')
    return d

def _store():
    base=Path('/data/gs_v2r1_daily') if Path('/data').is_dir() else Path('/tmp/gs_v2r1_daily')
    return base

def _read(key):
    item=_MEM.get(key)
    path=_store()/(key+'.json')
    if not item:
        try:item=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError,TypeError):return None
    try:
        stamp=datetime.fromisoformat(item['generated_utc'])
        if stamp.tzinfo is None or not 0<=(datetime.now(timezone.utc)-stamp).total_seconds()<3600:
            return None
        return item
    except (ValueError,KeyError,TypeError):return None

def _write(key,payload):
    _MEM[key]=payload
    try:
        base=_store();base.mkdir(parents=True,exist_ok=True)
        tmp=base/(key+'.tmp');target=base/(key+'.json')
        tmp.write_text(json.dumps(payload),encoding='utf-8');os.replace(tmp,target)
    except OSError as exc:
        LOG.warning('Daily cache uses process-memory fallback: %s',type(exc).__name__)

def _generate(day,zone):
    engine=ENGINE()  # preflights only three active worldwide model roles
    leagues=sorted(engine.good_leagues)
    available={};errors=[]
    for league in leagues:
        try:
            fixtures,issues,_=V2R.discover(league,'Specific date',day,zone,page=1)
            errors.extend(issues)
            for f in fixtures: available[f['key']]=f
        except Exception as exc:
            errors.append(league+':'+type(exc).__name__)
    selected=sorted(available.values(),key=lambda r:(V2R.utc(r['kickoff_utc']),r['key']))[:16]
    if not selected:
        return {'summary':[],'detail':[],'kickoffs':{},'status':'No verified upcoming fixtures found for this date in supported leagues.', 'notes':'Coverage is limited to leagues with validated history.','source_hash':engine.history_hash}
    # IMPORTANT: does not call V2R._run, free.handle, or the quota ledger.
    # The shared service computes once per cache period, not once per visitor.
    values,abstentions=engine.predict(selected,requested_markets=list(V2R.MARKETS))
    weighted=[]
    for row in values:
        best=V2R._best_market(row,V2R.MARKETS)
        if best is not None:
            weighted.append((best[0],str(row[0]),best[1],row))
    weighted.sort(key=lambda p:(-p[0],p[1]))
    picks=weighted[:10]
    summary=[[name,market,f'{score:.1f}%','Model estimate (uncalibrated)'] for score,name,market,_ in picks]
    detail=[list(row[:9]) for _,_,_,row in picks]
    kickoffs={r['home']+' vs '+r['away']:r['kickoff_utc'] for r in selected}
    status=(f'**{len(picks)} verified Quick Picks** for {day} · FREE to view. '
            f'{len(values)} actual model outputs from {len(selected)} eligible candidate fixtures. '
            'No visitor daily credits used.')
    if errors:status+=f' Partial source coverage: {len(errors)} provider checks failed.'
    notes=(f'{len(abstentions)} fixtures abstained. Three worldwide trained specialist models were used; '
           'the ten legacy models remain preserved but not routed. Predictions are uncertain, uncalibrated estimates, not guarantees.')
    return {'summary':summary,'detail':detail,'kickoffs':kickoffs,'status':status,'notes':notes,'source_hash':engine.history_hash}

def quick_picks(day,zone):
    try:
        chosen=_source_date(day,zone)
        # Restrict filenames to internally-derived ISO dates/time-zone hashes.
        import hashlib
        key=chosen.isoformat()+'_'+hashlib.sha256(zone.encode()).hexdigest()[:12]
        with _LOCK:
            entry=_read(key)
            if entry is None:
                entry=_generate(chosen.isoformat(),zone)
                entry['generated_utc']=datetime.now(timezone.utc).isoformat()
                _write(key,entry)
        # Match selections can start during the cache period; remove expired fixtures.
        now=datetime.now(timezone.utc)
        active=set(name for name,kick in entry['kickoffs'].items() if V2R.utc(kick)>now)
        summary=[r for r in entry['summary'] if r[0] in active]
        detail=[r for r in entry['detail'] if r[0] in active]
        status=entry['status'] if summary else ('No eligible future Quick Picks are available for this day. '
               'The next refresh can produce new picks if eligible fixtures appear.')
        return status,summary,detail,entry['notes']
    except Exception as exc:
        LOG.exception('Free daily Quick Picks unavailable')
        return ('Daily Quick Picks temporarily unavailable ('+type(exc).__name__+'). '
                'The model may be refreshing evidence, or no verified fixtures qualify.'),[],[], 'No user credits were consumed.'


def explain_projected(rows):
    if not rows:return 'No verified model probabilities available to explain.'
    paragraphs=[]
    for row in rows[:16]:
        if not isinstance(row,(list,tuple)) or len(row)!=9:continue
        paragraphs.append(f'**{row[0]}** — Home/Draw/Away: {row[1]}, {row[2]}, {row[3]}; '
                          f'Over/Under 2.5: {row[4]}/{row[5]}; Both Teams to Score: {row[6]}/{row[7]}.')
    return ('\n\n'.join(paragraphs) + '\n\nUncalibrated model estimates; not a guarantee.') if paragraphs else 'No valid rows to explain.'
