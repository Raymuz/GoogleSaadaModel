from datetime import date
from typing import Callable
from fastapi import APIRouter, HTTPException, Query
from zoneinfo import ZoneInfo
router=APIRouter(prefix='/api/v1',tags=['public-football-data'])
def original_discovery(day: str, league: str, zone: str):
    """Use historical discovery if available; otherwise query real ESPN events.

    ESPN identifiers are research-only, NOT approved historical club mappings.
    An upstream outage remains HTTP 503, never a fabricated empty fixture list.
    """
    from api.public_match_scores import PROVIDER_LEAGUES, _transport, _normalize
    from datetime import datetime, timezone

    supported = ('eng.1', 'esp.1', 'ger.1')
    selections = supported if league == 'All supported leagues' else (league,)
    if any(code not in PROVIDER_LEAGUES for code in selections):
        raise ValueError('Unsupported ESPN competition: ' + league)
    try:
        from api.research_service import get_engine
        original = get_engine().discover(day, league, zone)
        if isinstance(original, list):
            return original  # Preserve functioning historical discovery.
    except Exception:
        pass  # Fall through to the existing source-attributed ESPN service.

    output = []
    for code in selections:
        # Use the same injectable/provider-tested transport as Stage 4L.
        data = _transport(code, day)
        matches = _normalize(data, code)
        for event in matches:
            if event.get('state') != 'pre':
                continue
            home, away = event.get('home') or {}, event.get('away') or {}
            identifier = event.get('match_id')
            kickoff = event.get('kickoff_utc')
            if not identifier or not kickoff or not home.get('id') or not away.get('id'):
                continue
            output.append({
                'key': 'ESPN|' + code + '|' + str(identifier),
                'source': 'ESPN',
                'source_competition_id': PROVIDER_LEAGUES[code],
                'source_match_id': str(identifier),
                'league': code,
                'home': home.get('name'),
                'away': away.get('name'),
                'home_team_id': str(home['id']),
                'away_team_id': str(away['id']),
                'kickoff_utc': kickoff,
                'phase': 'UPCOMING',
            })
    return output

_discovery:Callable=original_discovery
def set_discovery_for_testing(fn:Callable):
    global _discovery
    old=_discovery
    _discovery=fn
    return old
@router.get('/fixtures/public')
def fixtures_public(day:date=Query(...),league:str=Query('All supported leagues',max_length=128),time_zone:str=Query('Africa/Douala',max_length=64)):
    try:ZoneInfo(time_zone)
    except Exception:raise HTTPException(status_code=422,detail='Invalid IANA time zone')
    try:items=_discovery(day.isoformat(),league,time_zone)
    except (OSError,TimeoutError,ConnectionError):raise HTTPException(status_code=503,detail='Fixture provider unavailable')
    except (ValueError,KeyError):raise HTTPException(status_code=422,detail='Invalid league or provider data')
    except Exception:raise HTTPException(status_code=503,detail='Fixture discovery unavailable')
    if not isinstance(items,list):raise HTTPException(status_code=503,detail='Invalid provider result')
    allowed=('key','source','source_competition_id','source_match_id','league','home','away','home_team_id','away_team_id','kickoff_utc','phase')
    safe=[]
    for item in items[:250]:
        if not isinstance(item,dict):raise HTTPException(status_code=503,detail='Invalid fixture')
        safe.append({key:item.get(key) for key in allowed})
    return {'date':day.isoformat(),'league':league,'time_zone':time_zone,'source':'ESPN',
            'fixtures':safe,'count':len(safe),'research_only':True,'production_predictions_enabled':False}
