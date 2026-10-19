"""Soccer AI private, source-backed GPT explanation adapter.

The only job of GPT is qualitative explanation. It never predicts fixtures,
calculates probabilities, or overrides model, club identity or publication gates.
Credential must be supplied server-side per request; never written into a file.
"""
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json, math, re

ENDPOINT = 'https://api.openai.com/v1/responses'
MARKETS = ('FT_1X2_HOME','FT_1X2_DRAW','FT_1X2_AWAY',
           'FT_OVER_2_5','FT_UNDER_2_5','FT_BTTS_YES','FT_BTTS_NO')
RESEARCH_WARNING = ('Private research only: historical results are out of date, '
                    'club identities are not formally approved, and market-specific '
                    'chronological calibration has not been verified.')
SAFE_FALLBACK = ('These model estimates are for private research only. '
                 'They are not validated betting recommendations.')


def safe_fixture_evidence(row, now_utc=None):
    """Only accept the frozen, restricted, seven-market evidence envelope."""
    if not isinstance(row, dict):
        raise ValueError('Research evidence must be a dictionary')
    if row.get('status') != 'UNAPPROVED_FROZEN_HISTORY_RESEARCH_ESTIMATE':
        raise ValueError('Fixture does not have eligible research estimates')
    if any(row.get(flag) is not False for flag in
           ('identity_approved','history_up_to_date','calibration_verified',
            'production_prediction_authorized')):
        raise ValueError('Invalid research approval flags')
    for key in ('key','home','away','kickoff_utc'):
        if not isinstance(row.get(key),str) or not row[key].strip() or len(row[key])>150:
            raise ValueError('Missing or invalid research fixture identity')
        if any(ord(c)<32 for c in row[key]):
            raise ValueError('Control character in fixture identity')
    kick = datetime.fromisoformat(row['kickoff_utc'].replace('Z','+00:00'))
    if kick.tzinfo is None:
        raise ValueError('Fixture kickoff timezone missing')
    now = now_utc or datetime.now(timezone.utc)
    if kick <= now.astimezone(timezone.utc):
        raise ValueError('Fixture has already kicked off')
    p = row.get('market_probabilities')
    if not isinstance(p,dict) or set(p) != set(MARKETS):
        raise ValueError('Exactly seven proven research market outputs required')
    probs = {}
    for k in MARKETS:
        v = p[k]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):
            raise ValueError('Non-numeric model probability')
        if not 0.0<=v<=1.0:
            raise ValueError('Invalid probability range')
        probs[k] = float(v)
    if abs(sum(probs[k] for k in MARKETS[:3])-1.0)>1e-8:
        raise ValueError('1X2 probability mass invalid')
    for a,b in (('FT_OVER_2_5','FT_UNDER_2_5'),('FT_BTTS_YES','FT_BTTS_NO')):
        if abs(probs[a]+probs[b]-1.0)>1e-8:
            raise ValueError('Complementary market probability mass invalid')
    return {'fixture_id':row['key'],'home':row['home'],'away':row['away'],
            'kickoff_utc':kick.astimezone(timezone.utc).isoformat(),
            'market_probabilities':probs,'research_only':True,
            'identity_approved':False,'history_up_to_date':False,
            'calibration_verified':False,'production_prediction_authorized':False}


def extract_text(payload):
    """Parse Responses API text items; no assumption of top-level output_text."""
    if not isinstance(payload,dict):
        raise RuntimeError('GPT_RESPONSE_INVALID_SHAPE')
    chunks=[]
    for message in payload.get('output',[]):
        if not isinstance(message,dict) or message.get('type')!='message':
            continue
        for part in message.get('content',[]):
            if (isinstance(part,dict) and part.get('type')=='output_text'
                and isinstance(part.get('text'),str)):
                chunks.append(part['text'])
    return '\n'.join(chunks).strip()


def allowed_qualitative_text(text):
    """Reject numeric claims or betting instructions from generated commentary."""
    if not isinstance(text,str) or not 5 <= len(text.strip()) <= 650:
        return False
    if re.search(r'\d|[%$€£]|https?://|\b(?:odds|stake|wager|guaranteed|surebet|profit)\b',
                 text,flags=re.I):
        return False
    return True


def make_request(key, model, approved_prompt, source_context=None, transport=None):
    """Exactly one backend-only OpenAI call; no invented match data allowed."""
    if not isinstance(key,str) or not key.strip() or any(c.isspace() for c in key.strip()):
        raise ValueError('OPENAI_CREDENTIAL_INVALID')
    if not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',model):
        raise ValueError('MODEL_ID_INVALID')
    if not isinstance(approved_prompt,str) or len(approved_prompt)<100:
        raise ValueError('APPROVED_PROMPT_MISSING')
    ctx = safe_fixture_evidence(source_context) if source_context is not None else None
    if ctx is None:
        user_text = ('No source-backed eligible football fixture was supplied. '
                     'In one short sentence, explain that no match prediction can be '
                     'provided without verified fixture and model evidence. '
                     'Do not include numbers, club names, odds or betting suggestions.')
    else:
        user_text = ('SOURCE VERIFIED AS PRIVATE RESEARCH, NOT PRODUCTION:\n'
                     + json.dumps(ctx,ensure_ascii=False,sort_keys=True)
                     + '\nWrite at most two short sentences describing ONLY the '
                       'limitations of these uncalibrated historical estimates. '
                       'Do not repeat any numbers, percentages, clubs, scores, dates, '
                       'goals, odds, or recommend a betting choice. '
                       'If information is missing, do not guess.')
    instructions = (approved_prompt + '\n\nCONSTRAINT FOR THIS PRIVATE API TEST: '
                    'Never issue a betting recommendation. Never invent any fixture, '
                    'outcome, numerical statistic, probability or prediction. '
                    'Return qualitative commentary only, with NO digits.')
    request_payload = {
        'model':model,'instructions':instructions,'input':user_text,
        'max_output_tokens':450,'store':False
    }
    if transport is not None:
        answer = transport(request_payload)  # Isolated offline test injection.
    else:
        req = Request(ENDPOINT,
                      data=json.dumps(request_payload).encode('utf-8'),
                      headers={'Authorization':'Bearer '+key.strip(),
                               'Content-Type':'application/json'},
                      method='POST')
        try:
            with urlopen(req,timeout=45) as response:
                if response.status != 200:
                    raise RuntimeError('GPT_HTTP_UNEXPECTED_STATUS')
                answer=json.loads(response.read(2_000_000).decode('utf-8'))
        except HTTPError as exc:
            # Never show raw HTTP response/error body: it may expose request data.
            labels={400:'REQUEST_REJECTED',401:'UNAUTHORIZED_OR_INVALID_KEY',
                    402:'BILLING_REQUIRED',403:'API_ACCESS_DENIED',
                    404:'MODEL_OR_ENDPOINT_UNAVAILABLE',
                    408:'API_TIMEOUT',429:'RATE_LIMIT_OR_BILLING_QUOTA'}
            raise RuntimeError('GPT_HTTP_'+str(exc.code)+'_'+labels.get(exc.code,'SERVICE_ERROR')) from None
        except URLError:
            raise RuntimeError('GPT_NETWORK_UNAVAILABLE') from None
        except TimeoutError:
            raise RuntimeError('GPT_REQUEST_TIMEOUT') from None
    reply=extract_text(answer)
    if not allowed_qualitative_text(reply):
        raise RuntimeError('GPT_REPLY_SAFETY_HOLD_OR_EMPTY_OUTPUT')
    # Only qualitative GPT text is returned; all model probabilities remain
    # immutable data outside GPT and should be displayed by Python/Gradio.
    return {'status':'PASS_PRIVATE_GPT_QUALITATIVE_EXPLANATION',
            'qualitative_explanation':reply,'fixture_evidence':ctx,
            'research_notice':RESEARCH_WARNING,
            'public_recommendations':[], 'published_betting_picks':[],
            'research_only':True,'production_predictions_enabled':False,
            'gpt_output_publication_authorized':False,
            'gpt_call_count':1}
