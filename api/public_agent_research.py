from datetime import date, datetime, timezone
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict

router=APIRouter(prefix='/api/v1', tags=['research-only-agent'])

# These are narrowly-scoped public, read-only information requests.
# Existing protected /agent/chat and /recommendations endpoints are UNCHANGED.
class PublicAgentQuery(BaseModel):
    model_config=ConfigDict(extra='forbid')
    intent: Literal['fixtures','live_scores','results','prediction_status','help']
    day: date|None=None
    league: str=Field(default='eng.1',max_length=20)
    time_zone: str=Field(default='Africa/Douala',max_length=64)


def _fixture_data(day,league,zone):
    from api.public_fixtures import fixtures_public
    return fixtures_public(day=day,league=league,time_zone=zone)


def _score_data(day,league,zone,kind):
    from api.public_match_scores import _response
    return _response(day,league,zone,kind)


def _answer(query):
    if query.intent=='prediction_status':
        return {'intent':query.intent,'message':'Predictions and personalized recommendations are unavailable until team identity evidence and model calibration are approved.',
                'available':False,'evidence':{'identity_mappings_approved':False,'calibration_approved':False},'matches':[]}
    if query.intent=='help':
        return {'intent':query.intent,'message':'Select fixtures, live scores, or completed results and provide a date and league. All sports data is attributed to the provider; predictions remain locked.',
                'available':True,'evidence':{'provider':'ESPN'},'matches':[]}
    if query.day is None:
        raise HTTPException(status_code=422,detail='day is required for football-data requests')
    if query.intent=='fixtures':
        # The original fixture research service supports the league names defined in its config.
        result=_fixture_data(query.day,query.league,query.time_zone)
        return {'intent':'fixtures','message':'Source-attributed upcoming fixtures, not predictions.',
                'available':True,'evidence':{'provider':'ESPN','day':str(query.day),'league':query.league,'retrieved_utc':datetime.now(timezone.utc).isoformat()},
                'matches':result.get('fixtures',[])}
    if query.intent in ('live_scores','results'):
        result=_score_data(query.day,query.league,query.time_zone,'live' if query.intent=='live_scores' else 'results')
        return {'intent':query.intent,'message':'Source-attributed match states and scores, not predictions.',
                'available':True,'evidence':{'provider':result['source'],'day':result['day'],'league':result['league'],'retrieved_utc':result['fetched_utc']},
                'matches':result.get('matches',[])}
    raise HTTPException(status_code=422,detail='Unsupported intent')

@router.post('/agent/public/query')
def public_agent_query(query:PublicAgentQuery):
    result=_answer(query)
    result.update({'research_only':True,'model_generated_text':False,'production_predictions_enabled':False,
                   'identity_approved':False,'persistent_user_quota_enforced':False})
    return result

@router.get('/recommendations/public/status')
def public_recommendation_status():
    return {'status':'HOLD_UNAPPROVED_IDENTITY_AND_CALIBRATION','recommendations':[],
            'message':'No public betting recommendations are authorized.',
            'research_only':True,'production_predictions_enabled':False,
            'identity_approved':False,'calibration_verified':False}
