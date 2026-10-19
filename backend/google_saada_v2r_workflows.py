"""GoogleSaada V2R: public two-path prediction interface.

The source of model estimates remains the existing V2Q three-specialist
orchestrator. No fallback probabilities, fabricated results, training or billing.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo
import importlib.util, json, re, sys, logging, unicodedata
import gradio as gr

APP=Path(__file__).resolve().parent.parent
_STAGE=APP/'global_v3/v2i_consolidated_candidate/backend'
_spec=importlib.util.spec_from_file_location('gs_v2r_provider',str(_STAGE/'google_saada_global_scores_v2.py'))
if _spec is None or _spec.loader is None: raise RuntimeError('Verified worldwide fixture provider missing')
provider=importlib.util.module_from_spec(_spec)
sys.modules['gs_v2r_provider']=provider
_spec.loader.exec_module(provider)
TZ_CHOICES=('Africa/Douala','America/Chicago','UTC','Europe/London','America/New_York','Europe/Paris','Africa/Lagos')
ALL='All available competitions — page of five'
COMPETITIONS=tuple(provider.COMPETITIONS)
LEAGUE_PAGES=tuple(tuple(COMPETITIONS[i:i+5]) for i in range(0,len(COMPETITIONS),5))
MARKETS=('Full-time result','Over/Under 2.5','Both Teams to Score')
PER_REQUEST=16
LOG=logging.getLogger('googlesaada.v2r')

_context={}
def configure(*,old,free,ledger,engine_factory,week):
    _context.clear();_context.update(old=old,free=free,ledger=ledger,engine_factory=engine_factory,week=week)

def now_utc():return datetime.now(timezone.utc)
def utc(value):
    if isinstance(value,datetime): t=value
    else:t=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('Provider kickoff missing timezone')
    return t.astimezone(timezone.utc)

def normalize(s):
    s=unicodedata.normalize('NFKD',str(s))
    s=''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(re.sub('[^a-z0-9]+',' ',s.casefold()).split())

def competition_batch(league,page):
    if league==ALL:
        try:n=int(page)
        except (TypeError,ValueError):raise ValueError('Choose a valid competition page')
        if not 1<=n<=len(LEAGUE_PAGES):raise ValueError('Invalid competition page')
        return LEAGUE_PAGES[n-1]
    if league not in provider.COMPETITIONS:raise ValueError('Select an available competition')
    return (league,)

def _period(period,selected_date,zone,clock):
    today=clock.astimezone(ZoneInfo(zone)).date()
    if period=='Today': lo=hi=today
    elif period=='Next 7 days':lo=today;hi=(clock+timedelta(hours=168)).astimezone(ZoneInfo(zone)).date()
    elif period=='Specific date':
        lo=hi=date.fromisoformat(str(selected_date))
        if not today<=lo<=(clock+timedelta(hours=168)).astimezone(ZoneInfo(zone)).date():
            raise ValueError('Choose a date within the next seven days')
    else:raise ValueError('Select Today, Next 7 days, or Specific date')
    return lo,hi

def discover(league,period,selected_date,tz,query='',page=1,*,clock=None,source=None):
    """Only official provider fixture IDs; 7-day UTC guard regardless of browsing date."""
    if tz not in TZ_CHOICES:raise ValueError('Unsupported time zone')
    if len(str(query or ''))>150:raise ValueError('Search text too long')
    clock=now_utc() if clock is None else utc(clock)
    names=competition_batch(league,page)
    lo,hi=_period(period,selected_date,tz,clock)
    # Date queries are localized, account for UTC offsets (never trust the client timestamp).
    utc_dates={(d+timedelta(days=offset)).strftime('%Y%m%d')
               for d in (lo+timedelta(days=i) for i in range((hi-lo).days+1))
               for offset in (-1,0,1)}
    if len(names)*len(utc_dates)>60:
        raise ValueError('Select one competition or a narrower date window')
    tasks=[(n,d) for n in names for d in sorted(utc_dates)]
    fetch=source or provider._fetch
    got={};errors=[]
    def read(n,d):
        events=fetch(n,d)
        if not isinstance(events,list):raise RuntimeError('Unexpected fixture provider response')
        return [provider._parse(e,n) for e in events]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(read,n,d):(n,d) for n,d in tasks}
        for future in as_completed(futures):
            n,d=futures[future]
            try:
                for r in future.result():
                    if not isinstance(r,dict) or r.get('source')!='ESPN' or r.get('phase')!='UPCOMING':continue
                    kickoff=utc(r['kickoff_utc'])
                    if not clock<kickoff<clock+timedelta(hours=168):continue
                    if not lo<=kickoff.astimezone(ZoneInfo(tz)).date()<=hi:continue
                    if query and normalize(query) not in normalize(r['league']+' '+r['home']+' '+r['away']):continue
                    if r['key'] in got:
                        if any(got[r['key']].get(k)!=r.get(k) for k in ('home','away','kickoff_utc','league')):
                            raise ValueError('Conflicting ESPN fixture identity')
                    got[r['key']]=r
            except Exception as exc:
                errors.append(n+'/'+d+':'+type(exc).__name__)
    rows=sorted(got.values(),key=lambda r:(utc(r['kickoff_utc']),r['key']))
    return rows,errors,len(tasks)

def fixture_table(rows,tz):
    zone=ZoneInfo(tz)
    return [[r['league'],utc(r['kickoff_utc']).astimezone(zone).strftime('%Y-%m-%d %H:%M %Z'),r['home'],r['away'],'Add to Bet Box ↗'] for r in rows]

def box_table(rows,tz):
    zone=ZoneInfo(tz)
    return [[r['league'],utc(r['kickoff_utc']).astimezone(zone).strftime('%Y-%m-%d %H:%M %Z'),r['home']+' vs '+r['away']] for r in rows]

def browse(league,period,day,tz,search,page):
    try:
        rows,errors,calls=discover(league,period,day,tz,search,page)
        status=f'**{len(rows)} verified upcoming fixtures found** across {calls} source-date checks. Click a fixture row to add it to My Bet Box.'
        if errors:status+=f' **Partial provider coverage:** {len(errors)} date queries failed. Unverified games will not be predicted.'
        return fixture_table(rows,tz),rows,status
    except Exception as exc:
        return [],[],f'Fixture search unavailable: {type(exc).__name__}: {str(exc)[:140]}'

def _box_response(box,tz):
    import gradio as gr
    choices=[(r['home']+' vs '+r['away']+' | '+r['league'],r['key']) for r in box]
    return box,box_table(box,tz),gr.update(choices=choices,value=None),f'**My Bet Box: {len(box)}/16 matches**. Only successfully returned model predictions use the free daily allowance.'

def add_row(rows,box,tz,evt:gr.SelectData):
    # Server state is advisory; the prediction request rechecks every ID at source.
    rows=list(rows or []);box=list(box or [])
    index=getattr(evt,'index',None)
    if isinstance(index,(list,tuple)):index=index[0] if index else None
    if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(rows):
        return _box_response(box,tz)
    item=rows[index]
    if item.get('key') not in {r['key'] for r in box}:
        if len(box)>=PER_REQUEST:raise ValueError('My Bet Box is limited to 16 distinct fixtures')
        box.append(item)
    return _box_response(box,tz)

def remove_box(box,key,tz):
    return _box_response([r for r in box or [] if r['key']!=key],tz)

def clear_box(tz):return _box_response([],tz)

def _parse_request(raw,available):
    raw=str(raw or '').strip()
    if not raw or len(raw)>2500:raise ValueError('Enter a short fixture request (maximum 2,500 characters)')
    text=raw
    names={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,
           'eleven':11,'twelve':12,'thirteen':13,'fourteen':14,'fifteen':15,'sixteen':16}
    for word,n in names.items():
        text=re.sub(r'(?i)\b'+word+r'\s+(?=(?:upcoming\s+)?(?:match(?:es)?|games?|fixtures?)\b)',str(n)+' ',text)
    instruction=re.search(r'(?i)\b(?:show|find|give me|list|search|predict|analy[sz]e)\s+(?:the\s+)?(?:next\s+)?(?:upcoming\s+)?(\d{1,3})\s+(?:upcoming\s+)?(?:match(?:es)?|games?|fixtures?)\b',text)
    if instruction:
        number=int(instruction.group(1))
        if not 1<=number<=16:raise ValueError('Choose 1–16 matches')
        if not available:raise ValueError('No verified upcoming matches available for this search')
        return available[:number]
    # Strict fixture parsing; never hallucinate teams or substitute another match.
    lines=[x.strip() for x in re.split(r'[\n;]+',text) if x.strip()]
    if len(lines)>16:raise ValueError('Maximum 16 matches')
    matches=[]
    for line in lines:
        match=re.fullmatch(r'(.{2,100}?)\s+(?:vs?\.?|versus)\s+(.{2,100}?)',line,re.I)
        if not match:raise ValueError('Enter "Home vs Away" lines or "Find 5 matches"')
        h,a=normalize(match.group(1)),normalize(match.group(2))
        found=[r for r in available if normalize(r['home'])==h and normalize(r['away'])==a]
        if len(found)!=1:raise ValueError('Fixture unavailable or ambiguous: '+line[:100])
        matches.extend(found)
    if len({r['key'] for r in matches})!=len(matches):raise ValueError('Duplicate fixtures entered')
    return matches

def direct_lookup(text,league,period,day,tz,page):
    try:
        available,errors,_=discover(league,period,day,tz,page=page)
        matches=_parse_request(text,available)
        if errors:
            raise ValueError('Provider coverage incomplete; retry or select a specific league')
        status=f'**{len(matches)} verified matches ready to review.** Remove unwanted matches before clicking Predict.'
        return matches,box_table(matches,tz),status,str(text or '').strip()
    except Exception as exc:
        return [],[],f'Unable to identify matches: {str(exc)[:200]}',str(text or '').strip()

def remove_direct(matches,selected_index,tz):
    matches=list(matches or [])
    if isinstance(selected_index,str) and selected_index.isdigit(): selected_index=int(selected_index)
    if not isinstance(selected_index,int) or not 0<=selected_index<len(matches):return matches,box_table(matches,tz)
    matches.pop(selected_index)
    return matches,box_table(matches,tz)

def _refetch_current(selected,*,clock=None,fetch=None):
    """No client-authored probability, score, club or kickoff is trusted."""
    clock=now_utc() if clock is None else utc(clock)
    fetch=fetch or provider._fetch
    grouped={};verified=[]
    if not isinstance(selected,(tuple,list)) or not 1<=len(selected)<=16:
        raise ValueError('Select 1–16 fixtures')
    if len({r['key'] for r in selected})!=len(selected):raise ValueError('Duplicate fixture IDs')
    for r in selected:
        key=r.get('key')
        if not isinstance(key,str) or not re.fullmatch(r'[a-z0-9.]+\|\d{4,20}',key):
            raise ValueError('Invalid provider fixture ID')
        comp,mid=key.split('|',1)
        matches=[n for n,c in provider.COMPETITIONS.items() if c==comp]
        if len(matches)!=1:raise ValueError('Unsupported competition ID')
        kick=utc(r.get('kickoff_utc'))
        if not clock<kick<clock+timedelta(hours=168):raise ValueError('Started or out-of-range kickoff')
        league=matches[0]
        slot=(league,kick.strftime('%Y%m%d'))
        if slot not in grouped:
            raw=fetch(*slot)
            parsed=[provider._parse(x,league) for x in raw]
            grouped[slot]={x['key']:x for x in parsed if isinstance(x,dict)}
        true=grouped[slot].get(key)
        if true is None or true.get('phase')!='UPCOMING':raise ValueError('Fixture is no longer scheduled')
        if any(true.get(k)!=r.get(k) for k in ('home','away','kickoff_utc','league')):
            raise ValueError('Selected fixture changed; refresh the schedule')
        if str(true.get('home_team_id'))==str(true.get('away_team_id')):
            raise ValueError('Invalid team identities')
        verified.append(true)
    return verified

class CheckedWeek:
    # Adapter preserves V2Q's existing _resolve checks and ledger settlement.
    def __init__(self,selected,week):self.selected=selected;self.week=week
    def window(self,league,tz,old,force=False):
        verified=_refetch_current(self.selected)
        t=now_utc();return verified,t,t+timedelta(hours=168),[]
    def _resolve(self,keys,raw,available):return self.week._resolve(keys,raw,available)
    def _utc(self,value):return self.week._utc(value)

def _run(selected,zone,token,markets,profile):
    cfg=_context
    if not cfg:raise RuntimeError('V2R workflow is not connected to production model adapters')
    if not selected:return ('No verified upcoming fixtures selected.',[],'No prediction was charged.',token,'')
    if not isinstance(markets,(list,tuple)) or not markets or not set(markets).issubset(MARKETS):
        return ('Choose a supported trained-model market.',[],'No prediction was charged.',token,'')
    week=CheckedWeek(selected,cfg['week'])
    # Empty manual text: exact checked fixture IDs only, not approximate aliases.
    try:
        result=cfg['free'].handle([r['key'] for r in selected],'',profile,'',ALL,zone,token,markets,
                            week=week,old=cfg['old'],ledger=cfg['ledger'],engine_factory=cfg['engine_factory'])
    except Exception as exc:
        LOG.exception('Verified V2R prediction callback failed before model results')
        return (f'Prediction unavailable: {type(exc).__name__}',[],
                'An error occurred in server-side validation or inference. No model probabilities displayed.',token,
                'Check the free allowance before retrying; contact support if credits do not reconcile.')
    return result

def direct_predict(text,review,review_signature,league,period,day,zone,page,profile,markets,token):
    if review and str(text or '').strip()==str(review_signature or ''):
        selected=list(review)
    else:
        selected,_,status,_=direct_lookup(text,league,period,day,zone,page)
        if not selected:return status,[], 'No prediction was charged.',token,''
    return _run(selected,zone,token,markets,profile)

def box_predict(box,zone,profile,markets,token):return _run(list(box or []),zone,token,markets,profile)

def recommend(league,zone,profile,token):
    # Recommend only based on actual trained-worldwide model output; unverified leagues abstain.
    try:
        available,errors,_=discover(league,'Next 7 days',None,zone,page=1)
        if errors:raise RuntimeError('Provider coverage incomplete')
        selected=available[:min(len(available),5)]
        if not selected:raise ValueError('No verified upcoming fixtures in this competition')
        msg,rows,note,signed,balance=_run(selected,zone,token,list(MARKETS),profile)
        if rows:
            def chance(r):
                try:return max(float(str(r[i]).rstrip('%')) for i in (1,2,3))
                except (IndexError,TypeError,ValueError):return -1
            # Confidence preference is presentation order, NEVER betting/financial certainty.
            rows.sort(key=chance,reverse=(profile!='High'))
            note+=' Presentation order follows full-time model confidence only; no bet is guaranteed.'
        return msg,rows,note,signed,balance
    except Exception as exc:
        return ('Verified worldwide recommendations unavailable.',[],str(exc)[:160]+' No prediction was charged.',token,'')

def explain_rows(rows):
    if not rows:return 'No verified model probabilities available to explain.'
    snippets=[]
    for row in rows[:16]:
        if len(row)<13:continue
        snippets.append(f'**{row[0]}** — H/D/A: {row[1]}, {row[2]}, {row[3]}; '
                        f'Over/Under 2.5: {row[4]}/{row[5]}; BTTS Yes/No: {row[6]}/{row[7]}.')
    return ('**GoogleSaada specialist Whiteboard summary** (observed model outputs, not GPT-generated forecasts):\n\n'
             +'\n\n'.join(snippets)+'\n\nThese estimates may be uncalibrated for unseen teams. No guarantee of results.')


# V2R recommendation enhancement: source-verified selection only; no new models.
RECOMMEND_MODES=('Any combination','All available competitions','Selected leagues')
RISK_THRESHOLDS={'Low':80.0,'Medium':70.0,'High':60.0,'Balanced':75.0}
RECOMMEND_MARKETS=MARKETS
RECOMMEND_LIMIT=20
SOURCE_QUERY_BUDGET=75

def _parse_probability(value):
    """Only an explicitly emitted model percentage is eligible for ranking."""
    import math
    if not isinstance(value,str) or not re.fullmatch(r'\s*\d{1,3}(?:\.\d+)?%\s*',value):
        return None
    p=float(value.strip()[:-1])
    return p if math.isfinite(p) and 0<=p<=100 else None

def _best_market(row,markets):
    if not isinstance(row,(tuple,list)) or len(row)!=13:
        return None
    candidates=[]
    groups={'Full-time result':[(1,'Home win'),(2,'Draw'),(3,'Away win')],
            'Over/Under 2.5':[(4,'Over 2.5'),(5,'Under 2.5')],
            'Both Teams to Score':[(6,'BTTS Yes'),(7,'BTTS No')]}
    for market in markets:
        for index,label in groups.get(market,[]):
            p=_parse_probability(row[index])
            if p is not None:candidates.append((p,label))
    return max(candidates,key=lambda v:v[0]) if candidates else None


def recommend_v2(
    mode,selected_leagues,period,selected_date,
    zone,profile,count,markets,token,candidate_budget=10
):
    """Rank verified model predictions within a credit budget."""

    def rejected(msg):
        return msg, [], [], 'No new prediction request was submitted.', token, ''

    try:
        if isinstance(count,bool) or isinstance(candidate_budget,bool):
            raise ValueError('Invalid recommendation count or budget')

        count=int(count)
        candidate_budget=int(candidate_budget)

        if not 1<=count<=RECOMMEND_LIMIT:
            raise ValueError('Choose 1–20 recommendations')

        if not count<=candidate_budget<=20:
            raise ValueError('Candidate budget must be at least the requested count, maximum 20')

        if profile not in RISK_THRESHOLDS:
            raise ValueError('Invalid analysis preference')

        if mode not in RECOMMEND_MODES:
            raise ValueError('Invalid competition selection')

        if (
            not isinstance(markets,(list,tuple))
            or not markets
            or len(set(markets))!=len(markets)
            or not set(markets)<=set(MARKETS)
        ):
            raise ValueError('Only supported prediction markets may be selected')

        if mode=='Selected leagues':

            if (
                not isinstance(selected_leagues,(list,tuple))
                or not selected_leagues
                or len(set(selected_leagues))!=len(selected_leagues)
                or not set(selected_leagues)<=set(COMPETITIONS)
            ):
                raise ValueError('Select verified leagues without duplicates')

            leagues=list(selected_leagues)

        else:
            leagues=list(COMPETITIONS)

        if not _context:
            raise RuntimeError('Verified prediction or quota adapter missing')

        uid,_=_context['ledger'].identity(token)
        available=_context['ledger'].remaining(uid)

        if not isinstance(available,int) or available<candidate_budget:
            return rejected(
                'Insufficient daily credits for the selected candidate budget'
            )

        low,high=_period(
            period,selected_date,zone,now_utc()
        )

        date_queries=min(
            10,(high-low).days+3
        )

        scan_cap=max(
            1,SOURCE_QUERY_BUDGET//date_queries
        )

        scanned=leagues[:scan_cap]

        failures=[]
        found={}

        # Discover source-verified upcoming fixtures.
        for league in scanned:

            try:
                rows,issues,_=discover(
                    league,period,selected_date,zone
                )

                failures.extend(issues)

                for fixture in rows:

                    key=str(fixture['key'])

                    if key in found and found[key]!=fixture:
                        raise ValueError('Conflicting fixture identity')

                    found[key]=fixture

            except Exception as exc:
                failures.append(
                    f'{league}: {type(exc).__name__}'
                )

        # Use a balanced competition selection.
        pools={lg:[] for lg in scanned}

        for fixture in found.values():
            if fixture['league'] in pools:
                pools[fixture['league']].append(fixture)

        for pool in pools.values():
            pool.sort(
                key=lambda x:(
                    utc(x['kickoff_utc']),x['key']
                )
            )

        candidates=[]
        names=set()

        while (
            len(candidates)<candidate_budget
            and any(pools.values())
        ):

            for lg in scanned:

                if len(candidates)>=candidate_budget:
                    break

                if not pools[lg]:
                    continue

                fixture=pools[lg].pop(0)

                display=(
                    f"{fixture['home']} vs {fixture['away']}"
                )

                if display in names:
                    continue

                names.add(display)
                candidates.append(fixture)

        if not candidates:
            return rejected(
                'No eligible upcoming verified fixtures. '
                'Try another date or league.'
            )

        outputs=[]
        used_token=token
        balance=''
        notices=[]

        requested_names={
            f"{r['home']} vs {r['away']}"
            for r in candidates
        }

        # Maximum 16 verified fixtures per engine call.
        for offset in range(
            0,len(candidates),PER_REQUEST
        ):

            batch=candidates[
                offset:offset+PER_REQUEST
            ]

            message,rows,note,used_token,balance=_run(
                batch,zone,used_token,list(markets),profile
            )

            if (
                not isinstance(rows,list)
                or len(rows)>len(batch)
            ):
                raise RuntimeError(
                    'Unexpected prediction result count'
                )

            notices.append(str(note or '')[:200])

            if not rows:
                break

            for row in rows:

                if (
                    not isinstance(row,list)
                    or len(row)!=13
                    or row[0] not in requested_names
                ):
                    raise RuntimeError(
                        'Invalid recommendation evidence'
                    )

            outputs.extend(rows)

        if len({
            row[0] for row in outputs
        })!=len(outputs):
            raise RuntimeError(
                'Duplicate recommendation results'
            )

        threshold=RISK_THRESHOLDS[profile]
        scored=[]

        for row in outputs:

            best=_best_market(row,markets)

            if best and best[0]>=threshold:
                scored.append((
                    best[0],str(row[0]),best[1],row
                ))

        scored.sort(
            key=lambda x:(-x[0],x[1])
        )

        chosen=scored[:count]

        table=[
            [
                name,market,
                f'{prob:.1f}%',
                f'>={threshold:.0f}% (uncalibrated)'
            ]
            for prob,name,market,row in chosen
        ]

        support=[
            row for _,name,market,row in chosen
        ]

        msg=(
            f'{len(chosen)}/{count} recommendations qualified '
            f'from {len(outputs)} actual model outputs; '
            f'analyzed up to {len(candidates)} candidate fixtures '
            f'across {len(scanned)}/{len(leagues)} leagues.'
        )

        if len(chosen)<count:
            msg+=' Threshold not lowered to fill list.'

        if len(scanned)<len(leagues):
            msg+=' Partial league coverage due to source-query budget.'

        if failures:
            msg+=f' Provider warnings: {len(failures)}.'

        note=(
            'Each successful model analysis consumes one '
            'daily credit, even if it does not qualify. '
            'Percentages are not guaranteed or calibrated '
            'betting risk. '
            +' '.join(notices[:2])[:350]
        )

        return (
            msg,table,support,note,
            used_token,balance
        )

    except (ValueError,TypeError,KeyError,RuntimeError) as exc:

        LOG.exception(
            'Recommendation search unavailable'
        )

        return rejected(
            'Recommendations unavailable: '+str(exc)[:140]
        )
