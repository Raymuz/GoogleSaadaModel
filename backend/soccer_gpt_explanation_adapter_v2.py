"""Soccer AI — versioned, explanation-only GPT Responses API adapter.
No predictor model calls, no numerical probabilities, no public picks.
"""
import json, re
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ENDPOINT = 'https://api.openai.com/v1/responses'


def _visible_text(payload):
    if not isinstance(payload, dict):
        return ''
    chunks = []
    for item in payload.get('output', []):
        if not isinstance(item, dict) or item.get('type') != 'message':
            continue
        for part in item.get('content', []):
            if isinstance(part, dict) and part.get('type') == 'output_text' and isinstance(part.get('text'), str):
                chunks.append(part['text'])
    if chunks:
        return '\n'.join(chunks).strip()
    return payload.get('output_text', '').strip() if isinstance(payload.get('output_text'), str) else ''


def _diagnose(payload):
    """Classify failure without printing unsafe generated text or reasoning."""
    status = str(payload.get('status', 'unknown')) if isinstance(payload, dict) else 'invalid'
    detail = payload.get('incomplete_details') if isinstance(payload, dict) else None
    reason = str((detail or {}).get('reason', '')) if isinstance(detail, dict) else ''
    usage = payload.get('usage', {}) if isinstance(payload, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    out_details = usage.get('output_tokens_details') or {}
    if not isinstance(out_details, dict):
        out_details = {}
    types = [str(x.get('type')) for x in payload.get('output', [])
             if isinstance(x, dict)] if isinstance(payload, dict) and isinstance(payload.get('output'), list) else []
    text = _visible_text(payload)
    metrics = {
        'response_status': status,
        'incomplete_reason': reason or 'NONE',
        'output_item_types': types[:12],
        'visible_character_count': len(text),
        'input_tokens': usage.get('input_tokens'),
        'output_tokens': usage.get('output_tokens'),
        'reasoning_tokens': out_details.get('reasoning_tokens'),
    }
    if status == 'incomplete':
        code = 'HOLD_API_INCOMPLETE_' + re.sub('[^A-Z0-9_]', '_', reason.upper() or 'UNKNOWN')[:64]
    elif status not in ('completed', 'unknown'):
        code = 'HOLD_API_RESPONSE_STATUS_' + re.sub('[^A-Z0-9_]', '_', status.upper())[:40]
    elif not text:
        refusal = any(part.get('type') == 'refusal'
            for item in payload.get('output', []) if isinstance(item, dict)
            for part in item.get('content', []) if isinstance(part, dict)) if isinstance(payload, dict) else False
        code = 'HOLD_API_REFUSAL' if refusal else 'HOLD_API_NO_VISIBLE_OUTPUT'
    elif not 5 <= len(text) <= 800:
        code = 'HOLD_OUTPUT_LENGTH'
    elif re.search(r'\d|[%$€£]|https?://', text, re.I):
        code = 'HOLD_UNSUPPORTED_NUMERIC_OR_LINK_CLAIM'
    elif re.search(r'\b(?:guaranteed|surebet|risk.free|place a bet|bet on|you should wager|stake your)\b', text, re.I):
        code = 'HOLD_BETTING_DIRECTIVE_OR_GUARANTEE'
    else:
        code = 'PASS_PRIVATE_GPT_QUALITATIVE_EXPLANATION'
    return code, text if code.startswith('PASS_') else None, metrics


def private_test(key, model, approved_prompt, transport=None):
    """Perform exactly one live request; transport is for isolated tests only."""
    if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key.strip()):
        raise ValueError('INVALID_COLAB_CREDENTIAL')
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', model):
        raise ValueError('INVALID_MODEL_ID')
    if not isinstance(approved_prompt, str) or len(approved_prompt) < 100:
        raise ValueError('APPROVED_SYSTEM_PROMPT_MISSING')

    request_data = {
      'model': model,
      'instructions': approved_prompt +
          '\n\nPRIVATE API CONNECTIVITY TEST ONLY. No fixture evidence is attached. '
          'Do not output a score, model probability, match name, bet or recommendation. '
          'Write one short plain-English sentence explaining why match predictions '
          'require verified fixture and model evidence. No numbers, digits or URLs.',
      'input': 'Please give the short evidence-limitation statement only.',
      'reasoning': {'effort': 'minimal'},
      'max_output_tokens': 1200,
      'store': False,
    }
    if transport is not None:
        payload = transport(request_data)
    else:
        req = Request(ENDPOINT,
              data=json.dumps(request_data).encode('utf-8'),
              headers={'Authorization':'Bearer ' + key.strip(),
                       'Content-Type':'application/json'}, method='POST')
        try:
            with urlopen(req, timeout=65) as response:
                payload = json.loads(response.read(3_000_000).decode('utf-8'))
        except HTTPError as err:
            # Never persist the response body, which may echo sensitive request data.
            status = int(err.code)
            mapping = {400:'REQUEST_INVALID', 401:'CREDENTIAL_REJECTED',
                       402:'BILLING_REQUIRED',403:'ACCESS_DENIED',
                       404:'MODEL_OR_ENDPOINT_UNAVAILABLE',
                       408:'TIMEOUT', 429:'QUOTA_OR_RATE_LIMIT'}
            return {'status':'HOLD_HTTP_'+str(status)+'_'+mapping.get(status,'SERVICE_ERROR'),
                    'explanation':None,'metrics':{'http_status':status},'api_requests':1}
        except (URLError, TimeoutError):
            return {'status':'HOLD_NETWORK_OR_TIMEOUT','explanation':None,
                    'metrics':{},'api_requests':1}
        except (UnicodeError, ValueError, json.JSONDecodeError):
            return {'status':'HOLD_UNEXPECTED_RESPONSE_ENCODING','explanation':None,
                    'metrics':{},'api_requests':1}
    code, explanation, metrics = _diagnose(payload)
    return {'status':code, 'explanation':explanation,'metrics':metrics,'api_requests':1,
            'research_only':True,'published_picks':[],
            'production_predictions_enabled':False}
