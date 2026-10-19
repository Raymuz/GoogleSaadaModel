"""Automatic anonymous session client for existing GoogleSaada Gradio UI.
No login screens or accounts. Use gr.State(token) per visitor, not a global token.
Calls the existing Stage 5A FastAPI routes in process (no external HTTP URL).
"""
from fastapi.testclient import TestClient
from api.main import app
from datetime import date

SESSION_ENDPOINT='/api/v1/agent/public/session'
CHAT_ENDPOINT='/api/v1/agent/public/conversation'
QUOTA_ENDPOINT='/api/v1/agent/public/quota'

def _client():
    return TestClient(app)

def start_anonymous_session():
    with _client() as client:
        response=client.post(SESSION_ENDPOINT)
    if response.status_code!=200:
        return '', 'Unable to begin a guest session. Try again.'
    data=response.json()
    return data['access_token'], 'Guest session ready — no sign-in required.'


# GS_V8_STAGE5C_DATE_PAYLOAD_REPAIR_V1
def _gs5c_resolve_day(message, day, time_zone):
    from datetime import date,timedelta,datetime
    import re
    from zoneinfo import ZoneInfo
    if day is not None and str(day).strip():
        return date.fromisoformat(str(day).strip()).isoformat()
    matches=re.findall(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)',message)
    if matches:
        return date.fromisoformat(matches[-1]).isoformat()
    lower=message.lower()
    if 'today' in lower or 'tomorrow' in lower or 'yesterday' in lower:
        try: tz=ZoneInfo(time_zone)
        except (KeyError,ValueError): tz=ZoneInfo('UTC')
        current=datetime.now(tz).date()
        if 'tomorrow' in lower:return (current+timedelta(days=1)).isoformat()
        if 'yesterday' in lower:return (current-timedelta(days=1)).isoformat()
        return current.isoformat()
    return None

def ask_public_agent(message, token, history=None, day=None, league='eng.1',time_zone='Africa/Douala'):
    # Intended signature for Gradio: inputs=[message, token_state, chat_state,...]
    # Do not persist token to disk or show token in chat/history.
    history=list(history or [])[-24:]
    message=(message or '').strip()
    if not message:
        return token or '', history, 'Enter a question to continue.', None
    if not token:
        token,notice=start_anonymous_session()
        if not token:
            return '', history, notice, None
    payload={'message':message,'league':league,'time_zone':time_zone}
    effective_day=_gs5c_resolve_day(message, day, time_zone)
    if effective_day: payload['day']=effective_day
    with _client() as client:
        response=client.post(CHAT_ENDPOINT,json=payload,
            headers={'Authorization':'Bearer '+token})
        if response.status_code==401:
            # Transparent refresh for expired/reset anonymous sessions.
            token,_=start_anonymous_session()
            if not token:return '',history,'Guest session unavailable.',None
            response=client.post(CHAT_ENDPOINT,json=payload,
                headers={'Authorization':'Bearer '+token})
    if response.status_code==429:
        return token,history,'Daily guest usage limit reached. Please return after UTC midnight.',0
    if response.status_code==422:
        return token,history,'Please provide a valid date for this football-data question.',None
    if response.status_code!=200:
        return token,history,'The football-data service is unavailable. No result was invented.',None
    body=response.json()
    answer=str(body.get('reply','No verified answer available.'))
    history.append({'role':'user','content':message})
    history.append({'role':'assistant','content':answer})
    return token,history,'',body.get('remaining_today')

def remaining_public_quota(token):
    if not token:return 'Start a guest conversation to see remaining requests.'
    with _client() as client:
        r=client.get(QUOTA_ENDPOINT,headers={'Authorization':'Bearer '+token})
    if r.status_code!=200:return 'Guest session is no longer active.'
    d=r.json()
    return f"Guest requests remaining today: {d['remaining']} / {d['limit']}"
