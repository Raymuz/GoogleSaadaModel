"""GoogleSaada V8 protected read-only operational API; Gradio remains on its own port."""
from __future__ import annotations
import hmac, os
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

app=FastAPI(title="GoogleSaada Backend",version="8.0-staging",docs_url=None,redoc_url=None,openapi_url=None)
ROOT=Path(__file__).resolve().parent

@app.get("/health",tags=["health"])
def health():
    return {"status":"ok", "service":"googlesaada-fastapi", "scope":"staging-health-only"}

@app.get("/api/v1/admin/status",tags=["admin"])
def admin_status(authorization: str|None=Header(default=None)):
    token=os.getenv("GOOGLESAADA_ADMIN_API_TOKEN","").strip()
    if not token or len(token)<32: raise HTTPException(status_code=503,detail="Admin access not configured")
    if not authorization or not authorization.startswith("Bearer ") or not hmac.compare_digest(authorization[7:],token):
        raise HTTPException(status_code=401,detail="Unauthorized")
    checks={name:(ROOT/name).is_file() for name in (
      "app.py", "app_private_googlesaada_auth_staging.py",
      "backend/google_saada_gpt_only_agent.py",
      "backend/google_saada_master_orchestrator_v2.py",
      "backend/google_saada_postgres_quota.py")}
    return {"status":"source-present" if all(checks.values()) else "missing-files",
            "checks":checks, "runtime_models_verified":False,
            "live_auth_verified":False,"production_approved":False}

@app.get("/api/v1/predict")
def prediction_is_not_exposed():
    return JSONResponse(status_code=503,content={"error":"Prediction REST routing is disabled pending certified Judge/calibration and authentication"})
