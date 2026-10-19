
from datetime import date, datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from zoneinfo import ZoneInfo
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

router=APIRouter(prefix='/api/v1',tags=['public-football-data'])
# Supported provider competition codes; 'bl1' is the historical/internal Bundesliga alias.
PROVIDER_LEAGUES={'eng.1':'eng.1','esp.1':'esp.1','ger.1':'ger.1','bl1':'ger.1'}
MAX_RESPONSE_BYTES=2_500_000

class ProviderUnavailable(RuntimeError): pass
class ProviderPayloadError(RuntimeError): pass

def _fetch_scoreboard(league,day):
    qs=urlencode({'dates':day.replace('-',''),'limit':200})
    url='https://site.api.espn.com/apis/site/v2/sports/soccer/'+PROVIDER_LEAGUES[league]+'/scoreboard?'+qs
    request=Request(url,headers={'User-Agent':'GoogleSaada-V8-ReadOnly/1.0','Accept':'application/json'})
    try:
        with urlopen(request,timeout=12) as response:
            body=response.read(MAX_RESPONSE_BYTES+1)
        if len(body)>MAX_RESPONSE_BYTES:raise ProviderPayloadError('Response too large')
        obj=json.loads(body)
    except (HTTPError,URLError,TimeoutError,OSError) as exc:
        raise ProviderUnavailable(type(exc).__name__) from exc
    except (ValueError,UnicodeError) as exc:
        raise ProviderPayloadError('Invalid JSON') from exc
    if not isinstance(obj,dict) or not isinstance(obj.get('events'),list):
        raise ProviderPayloadError('Unexpected events schema')
    return obj

# Injectable transport for deterministically testing all public route contracts.
_transport=_fetch_scoreboard

def set_transport_for_testing(new_transport):
    global _transport
    old=_transport
    _transport=new_transport
    return old

def _normalize(payload,league):
    output=[]
    events=payload.get('events')
    if not isinstance(events,list):raise ProviderPayloadError('Missing events list')
    for event in events[:250]:
        if not isinstance(event,dict):continue
        competitions=event.get('competitions') or []
        if not isinstance(competitions,list) or not competitions:continue
        competition=competitions[0]
        if not isinstance(competition,dict):continue
        competitors=competition.get('competitors') or []
        if not isinstance(competitors,list):continue
        home=next((c for c in competitors if isinstance(c,dict) and c.get('homeAway')=='home'),None)
        away=next((c for c in competitors if isinstance(c,dict) and c.get('homeAway')=='away'),None)
        if not home or not away:continue
        status=event.get('status') or {}
        typ=status.get('type') if isinstance(status,dict) else {}
        if not isinstance(typ,dict):typ={}
        state=str(typ.get('state') or '').lower()
        if state not in ('pre','in','post'):continue
        def compact(team):
            obj=team.get('team') or {}
            if not isinstance(obj,dict):obj={}
            score=team.get('score')
            return {'id':str(obj.get('id') or ''),'name':str(obj.get('displayName') or obj.get('name') or ''),
                    'score':str(score) if score is not None else None}
        home_data=compact(home);away_data=compact(away)
        if not home_data['id'] or not away_data['id'] or not home_data['name'] or not away_data['name']:
            continue
        output.append({'source':'ESPN','provider_competition':PROVIDER_LEAGUES[league],
                       'match_id':str(event.get('id') or ''),'kickoff_utc':event.get('date'),
                       'state':state,'status':str(typ.get('description') or typ.get('name') or ''),
                       'home':home_data,'away':away_data,'identity_approved':False,
                       'production_prediction_authorized':False})
    return output

def _response(day,league,time_zone,kind):
    if league not in PROVIDER_LEAGUES:
        raise HTTPException(status_code=422,detail='Unsupported provider competition')
    try:ZoneInfo(time_zone)
    except Exception:raise HTTPException(status_code=422,detail='Invalid IANA timezone')
    try:
        payload=_transport(league,day.isoformat())
        items=_normalize(payload,league)
    except (ProviderUnavailable,ProviderPayloadError) as exc:
        raise HTTPException(status_code=503,detail='Score data provider unavailable') from exc
    except Exception as exc:
        raise HTTPException(status_code=503,detail='Score data service unavailable') from exc
    wanted='in' if kind=='live' else 'post'
    filtered=[x for x in items if x['state']==wanted]
    return {'day':day.isoformat(),'league':league,'time_zone':time_zone,'source':'ESPN',
            'fetched_utc':datetime.now(timezone.utc).isoformat(),
            'status':'provider_response','data_kind':kind,'count':len(filtered),
            'matches':filtered,'research_only':True,'production_predictions_enabled':False}

@router.get('/scores/public/live')
def public_live_scores(day:date=Query(...),league:str=Query('eng.1',max_length=20),
                       time_zone:str=Query('Africa/Douala',max_length=64)):
    return _response(day,league,time_zone,'live')

@router.get('/scores/public/results')
def public_match_results(day:date=Query(...),league:str=Query('eng.1',max_length=20),
                         time_zone:str=Query('Africa/Douala',max_length=64)):
    return _response(day,league,time_zone,'results')
