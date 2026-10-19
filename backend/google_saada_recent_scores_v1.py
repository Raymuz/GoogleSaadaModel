"""ESPN verified completed-game observations for request-time ML features.

This is NOT a trained model, prediction, unverified news feed, or data rewrite.
Explicit final state, two source IDs, numeric scores and chronology are required.
Historical parquet/models/registry remain immutable.
"""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timedelta,timezone
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from threading import RLock
from collections import Counter
import hashlib,json,re,time

CODES=frozenset(('eng.1','esp.1','ger.1'))
CACHE={}
LOCK=RLock()
MAX_LOOKBACK_DAYS=21
BUFFER_HOURS=3

def utc(value):
    t=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if t.tzinfo is None:raise ValueError('Missing UTC offset')
    return t.astimezone(timezone.utc)

def _number(value):
    if isinstance(value,dict):value=value.get('value')
    if isinstance(value,bool) or value is None:return None
    try:
        n=float(value)
        if n.is_integer() and 0<=n<=30:return int(n)
    except (TypeError,ValueError,OverflowError):pass
    return None

def parse_final(event,code,now=None):
    """Returns None when ESPN does not prove the event was completed."""
    now=now or datetime.now(timezone.utc)
    if code not in CODES or not isinstance(event,dict):return None
    mid=str(event.get('id','')).strip()
    if not re.fullmatch(r'[0-9]{3,20}',mid):return None
    status=((event.get('status') or {}).get('type') or {})
    if not (status.get('completed') is True and str(status.get('state','')).lower()=='post'):
        return None
    tags=str(status.get('name',''))+' '+str(status.get('description',''))
    if any(x in tags.upper() for x in ('ABANDON','CANCEL','POSTPON','SUSPEND','FORFEIT')):return None
    try: start=utc(event['date'])
    except (ValueError,KeyError,TypeError):return None
    # Start-time is not completion time. Conservative +3h offset avoids
    # treating a newly-started match as a fully settled result.
    if not (start+timedelta(hours=BUFFER_HOURS)<now):return None
    competitions=event.get('competitions')
    if not isinstance(competitions,list) or len(competitions)!=1:return None
    teams=competitions[0].get('competitors')
    if not isinstance(teams,list):return None
    sides={}
    for part in teams:
        if not isinstance(part,dict):continue
        side=part.get('homeAway')
        if side not in ('home','away') or side in sides:return None
        team=part.get('team') or {}
        ident=str(team.get('id','')).strip()
        name=team.get('displayName')
        score=_number(part.get('score'))
        if not re.fullmatch(r'[0-9]{1,20}',ident) or not isinstance(name,str) or not name.strip() or score is None:return None
        names=tuple(sorted({v.strip() for k in ('displayName','shortDisplayName','name') if isinstance((v:=team.get(k)),str) and v.strip() and len(v)<101}))
        if name.strip() not in names:return None
        sides[side]={'team_id':ident,'name':name.strip(),'options':names,'score':score}
    if set(sides)!={'home','away'} or sides['home']['team_id']==sides['away']['team_id']:return None
    record={'key':code+'|'+mid,'source':'ESPN','source_competition_id':code,
        'source_match_id':mid,'kickoff_utc':start.isoformat(),
        'home':sides['home']['name'],'away':sides['away']['name'],
        'home_team_id':sides['home']['team_id'],'away_team_id':sides['away']['team_id'],
        'home_name_options':list(sides['home']['options']), 'away_name_options':list(sides['away']['options']),
        'home_goals':sides['home']['score'],'away_goals':sides['away']['score'],'phase':'FINAL'}
    record['source_evidence_sha256']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
    return record

def _fetch(code,ymd):
    if code not in CODES or not re.fullmatch(r'[0-9]{8}',ymd):raise ValueError('Unapproved source query')
    with LOCK:
        saved=CACHE.get((code,ymd))
        if saved and time.monotonic()-saved[0]<300:return saved[1]
    url='https://site.api.espn.com/apis/site/v2/sports/soccer/'+code+'/scoreboard?'+urlencode({'dates':ymd,'limit':500})
    request=Request(url,headers={'User-Agent':'GoogleSaada-VerifiedFeatures/1.0','Accept':'application/json'})
    with urlopen(request,timeout=12) as response:
        if response.status!=200:raise RuntimeError('Source responded with non-200 status')
        data=response.read(5000001)
    if len(data)>5000000:raise RuntimeError('Source response exceeds size limit')
    payload=json.loads(data.decode('utf-8'))
    if not isinstance(payload,dict) or not isinstance(payload.get('events'),list):raise RuntimeError('Source schema changed')
    with LOCK:CACHE[(code,ymd)]=(time.monotonic(),payload['events'])
    return payload['events']

def completed(codes,prediction_cutoff,history_last,*,lookback_days=21,transport=None,now=None):
    """Fetch all recent final scores for exact supported leagues.

    Rejects partial network responses to prevent falsely complete recent form.
    Any result must be after frozen history and strictly EARLIER UTC DAY than
    EVERY predicted match in the request, preventing same-day leakage.
    """
    now=now or datetime.now(timezone.utc)
    cutoff=utc(prediction_cutoff)
    historical=utc(history_last)
    codes=sorted(set(codes))
    if not codes or not set(codes).issubset(CODES):raise ValueError('Unsupported competition')
    if isinstance(lookback_days,bool) or not isinstance(lookback_days,int) or not 1<=lookback_days<=MAX_LOOKBACK_DAYS:raise ValueError('Unsafe lookback')
    until=min(now.date(),cutoff.date()-timedelta(days=1))
    start=max(historical.date()+timedelta(days=1),until-timedelta(days=lookback_days-1))
    days=[start+timedelta(days=i) for i in range(max(0,(until-start).days+1))]
    jobs=[(code,d.strftime('%Y%m%d')) for code in codes for d in days]
    fetch=transport or _fetch
    found={};failed=[];discard=Counter()
    with ThreadPoolExecutor(max_workers=min(5,max(1,len(jobs)))) as pool:
        futures={pool.submit(fetch,c,d):(c,d) for c,d in jobs}
        for future in as_completed(futures):
            code,ymd=futures[future]
            try:
                events=future.result()
                if not isinstance(events,list):raise ValueError('Source events must be a list')
                for raw in events:
                    row=parse_final(raw,code,now=now)
                    if not row:discard['not_trusted_final']+=1;continue
                    when=utc(row['kickoff_utc'])
                    if when<=historical or when.date()>=cutoff.date():discard['not_new_or_causal']+=1;continue
                    previous=found.get(row['key'])
                    if previous is not None and previous!=row:raise ValueError('Conflicting provider scores for same fixture')
                    found[row['key']]=row
            except Exception as exc:
                failed.append(code+':'+ymd+':'+type(exc).__name__)
    metadata={'provider':'ESPN','source_fetch_utc':now.isoformat(),
       'latest_history_utc':historical.isoformat(),'forecast_earliest_utc':cutoff.isoformat(),
       'lookback_days':lookback_days,'source_requests':len(jobs),'source_errors':len(failed),
       'discarded':dict(discard)}
    if failed:
        return [],{**metadata,'status':'RECENT_SOURCE_PARTIAL_FROZEN_BASELINE','accepted_finals':0}
    rows=sorted(found.values(),key=lambda r:(r['kickoff_utc'],r['key']))
    return rows,{**metadata,'status':'RECENT_SOURCE_COMPLETE','accepted_finals':len(rows),
                 'most_recent_final_kickoff':rows[-1]['kickoff_utc'] if rows else None}
