"""GoogleSaada V8 authenticated prediction gateway.

Uses the original V2O CreditLedger and externally issued short-lived signed
session. Never issues identity tokens; never accepts user-supplied features.
Fail-closed if deployment identity or persistent ledger is unconfigured.
"""
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from backend.gs_v8_live_bridge import forecast, PredictionAbstained
from backend.google_saada_v2o_auth_credits_v1 import (
    CreditLedger, principal_from_assertion, AccessDenied, QuotaDenied, InFlight,
)

log = logging.getLogger('googlesaada.v8.public')
SUPPORTED = {'match_result', 'btts', 'over_2_5'}

def _principal(request):
    if os.getenv('GS_PREDICT_AUTH_ENABLED') != '1':
        raise AccessDenied('Verified user authentication not configured')
    origin = request.headers.get('origin', '').rstrip('/')
    allowed = {x.strip().rstrip('/') for x in os.getenv('GS_PREDICT_TRUSTED_ORIGINS', '').split(',') if x.strip()}
    if not origin or origin not in allowed or urlparse(origin).scheme != 'https':
        raise AccessDenied('Request origin not approved')
    # Require proper server-side HTTPS/proxy termination configuration.
    if request.url.scheme != 'https':
        raise AccessDenied('HTTPS request required')
    assertion = request.cookies.get('gs_prediction_identity')
    return principal_from_assertion(assertion)

def _ledger():
    path = os.getenv('GS_PREDICT_LEDGER_PATH', '')
    if (os.getenv('GS_PREDICT_LEDGER_PRODUCTION_READY') != '1'
        or not path or not Path(path).is_absolute()
        or not (Path(path).as_posix().startswith('/data/')
                or os.getenv('GS_PREDICT_SANDBOX_ONLY') == '1')):
        raise RuntimeError('Persistent credit ledger not approved')
    return CreditLedger(path)

def install(api):
    @api.post('/api/v1/predict')
    async def prediction(request: Request):
        if os.getenv('GS_PREDICTION_API_ENABLED', '0') != '1':
            return JSONResponse(status_code=503, content={'error':'Public prediction API disabled pending full acceptance'})
        try:
            principal = _principal(request)
        except AccessDenied:
            return JSONResponse(status_code=401, content={'error':'Verified login required'})
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) - {'fixture_key', 'markets'}:
                raise ValueError('Unsupported input fields')
            fixture_key = body.get('fixture_key')
            markets = body.get('markets', sorted(SUPPORTED))
            if (not isinstance(fixture_key, str) or not 1 <= len(fixture_key) <= 160
                or not isinstance(markets, list) or not markets
                or len(markets) != len(set(markets)) or not set(markets) <= SUPPORTED):
                raise ValueError('Invalid fixture or requested markets')
        except (ValueError, TypeError, AttributeError):
            return JSONResponse(status_code=422, content={'error':'Invalid prediction request'})
        try:
            ledger = _ledger()
        except Exception:
            return JSONResponse(status_code=503, content={'error':'Authenticated credit ledger unavailable'})
        try:
            state, rid, prior = ledger.reserve(principal, [fixture_key], markets, 'v8-public-api')
            if state == 'REPLAY':
                return JSONResponse(status_code=prior.get('_http_status', 200), content={k:v for k,v in prior.items() if k != '_http_status'})
        except (QuotaDenied, InFlight, ValueError):
            return JSONResponse(status_code=429, content={'error':'Prediction quota or request in progress'})
        except Exception:
            return JSONResponse(status_code=503, content={'error':'Unable to reserve prediction credit'})
        # Every successful reservation must be settled; inability to settle
        # deliberately returns 503 and requires ledger review.
        actual = 0
        code = 503
        output = {'error':'Prediction unavailable'}
        try:
            result = forecast(fixture_key)  # REAL provider snapshot + real 56-feature models
            outputs = result.get('model_outputs', {})
            if not isinstance(outputs, dict) or not SUPPORTED.issubset(outputs):
                raise RuntimeError('Incomplete model result')
            # Only selected markets are returned. No calibration is invented.
            result['model_outputs'] = {k: outputs[k] for k in markets}
            output = {'status':'RESEARCH_PREDICTION', 'fixture_key':fixture_key,
                      'model_result':result,
                      'disclaimer':'Research estimates, not guaranteed outcomes.'}
            actual, code = 1, 200
        except PredictionAbstained:
            code = 422
            output = {'status':'ABSTAIN', 'reason':'Verified evidence insufficient or fixture ineligible'}
        except Exception:
            log.exception('Prediction execution failed')
        try:
            ledger.settle(rid, principal, actual, {**output, '_http_status': code})
        except Exception:
            log.exception('Prediction credit settlement requires review')
            return JSONResponse(status_code=503, content={'error':'Credit settlement requires review'})
        return JSONResponse(status_code=code, content=output)
