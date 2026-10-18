"""GoogleSaada unified one-port staging entry point; no WordPress connection."""
from __future__ import annotations
import ast, hmac, os, runpy, sys
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
import gradio as gr

ROOT=Path(__file__).resolve().parent
STAGING=ROOT/'app_private_googlesaada_auth_staging.py'
# Preserve the explicit original public paid-agent release lock.
def _check_release_lock():
    if not STAGING.is_file(): raise RuntimeError('Original Gradio staging entrypoint missing')
    tree=ast.parse(STAGING.read_text(encoding='utf-8-sig'))
    locks=[]
    for node in ast.walk(tree):
        if isinstance(node,(ast.Assign, ast.AnnAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t,ast.Name) and 'RELEASE' in t.id.upper() and 'LOCK' in t.id.upper():
                    if isinstance(node.value,ast.Constant) and node.value.value is False: locks.append(t.id)
    # If lock is renamed, disable public predictions anyway; never force-enable it.
    os.environ['GOOGLESAADA_PUBLIC_PREDICTIONS_ENABLED']='false'
    return locks

def _create_blocks():
    # Original staging module performs app.launch() under __main__.
    # Capture that exact Blocks instance without exposing another port.
    _check_release_lock()
    for folder in (str(ROOT),str(ROOT/'backend')):
        if folder not in sys.path: sys.path.insert(0,folder)
    os.chdir(ROOT)
    captured=[]
    old=gr.Blocks.launch
    def capture(self,*args,**kwargs):
        captured.append(self)
        return None
    gr.Blocks.launch=capture
    try:
        ns=runpy.run_path(str(STAGING),run_name='__main__')
    finally:
        gr.Blocks.launch=old
    if len(captured)!=1: raise RuntimeError('Expected exactly one existing Gradio application, found '+str(len(captured)))
    blocks=captured[0]
    if not hasattr(blocks,'config') and not hasattr(blocks,'get_config_file'):
        raise RuntimeError('Captured Gradio app lacks config')
    return blocks

api=FastAPI(title='GoogleSaada V8',docs_url=None,redoc_url=None,openapi_url=None)
@api.get('/health')
def health():
    return {'status':'ok','service':'googlesaada-v8','frontend':'gradio','wp_integration':False,
            'prediction_api_enabled':False}
@api.get('/api/v1/admin/status')
def status(authorization:str|None=Header(default=None)):
    token=os.environ.get('GOOGLESAADA_ADMIN_API_TOKEN','')
    if len(token)<32: raise HTTPException(status_code=503,detail='Admin secret not configured')
    if not authorization or not authorization.startswith('Bearer ') or not hmac.compare_digest(authorization[7:],token):
        raise HTTPException(status_code=401,detail='Unauthorized')
    return {'status':'staging','gradio_mounted':True,'production_authorized':False,
            'gpt_key_configured':bool(os.environ.get('OPENAI_API_KEY')),
            'wordpress_integrated':False,'database_configured':bool(os.environ.get('DATABASE_URL'))}
@api.get('/api/v1/predict')
def predict():
    return JSONResponse(status_code=503,content={'error':'REST predictions disabled until native Judge/calibration and access-control acceptance'})

# One public port. Existing UI stays at / ; FastAPI routes above take precedence.

# BEGIN GOOGLESAADA V8 VERIFIED PRIVATE PREDICTION
from fastapi import Request
import hmac as _gs_hmac
import os as _gs_os

@api.post("/api/v1/internal/verified-predict")
async def gs_v8_internal_verified_predict(request: Request):
    # Fail closed without a separately configured staging secret.
    secret = _gs_os.environ.get(
        "GOOGLESAADA_V8_INTERNAL_API_KEY", ""
    )
    supplied = request.headers.get(
        "x-googlesaada-internal-key", ""
    )

    if (
        not secret
        or not supplied
        or not _gs_hmac.compare_digest(secret, supplied)
    ):
        return JSONResponse(
            status_code=403,
            content={"error": "Protected staging endpoint"}
        )

    try:
        from backend.gs_v8_verified_runtime import (
            predict_verified, PredictionAbstained
        )

        body = await request.json()
        if not isinstance(body, dict):
            raise PredictionAbstained("Invalid request")

        required = {
            "home_team_id", "away_team_id", "kickoff_utc"
        }

        if not required.issubset(body):
            raise PredictionAbstained(
                "Missing verified fixture attributes"
            )

        # User-controlled historical_test is NEVER accepted.
        # The service uses its own current evidence cutoff.
        import pandas as _gs_pd
        cutoff = _gs_pd.Timestamp.now(tz="UTC")

        result = predict_verified(
            home_team_id=body["home_team_id"],
            away_team_id=body["away_team_id"],
            kickoff_utc=body["kickoff_utc"],
            evidence_cutoff_utc=cutoff,
            historical_test=False
        )

        return JSONResponse(
            status_code=200, content=result
        )

    except PredictionAbstained as exc:
        return JSONResponse(
            status_code=422,
            content={
                "status": "ABSTAIN",
                "reason": str(exc)
            }
        )
    except Exception:
        # Do not reveal model paths, private data or stack traces.
        return JSONResponse(
            status_code=503,
            content={"error": "Verified runtime unavailable"}
        )
# END GOOGLESAADA V8 VERIFIED PRIVATE PREDICTION


app=gr.mount_gradio_app(api,_create_blocks(),path='/')
