"""GoogleSaada V8 free public prediction gateway. No OAuth or fabricated forecasts."""
import collections
import os
import threading
import time
from fastapi import Request
from fastapi.responses import JSONResponse
from backend.gs_v8_live_bridge import fixtures_summary, forecast, PredictionAbstained

_ALLOWED = {"match_result", "btts", "over_2_5"}
_GUARD = threading.Lock()
_ACTIVITY = collections.OrderedDict()
_MAX_CLIENTS = 5000
_WINDOW = 3600
_MAX_REQUESTS = 12

def enabled():
    return os.getenv("GS_V8_FREE_PUBLIC_ENABLED", "0") == "1"

def _client_key(request):
    # Never trust X-Forwarded-For supplied by a caller. Hosting proxy may
    # aggregate users under the same address; use an API gateway for robust limits.
    client = getattr(request, "client", None)
    value = getattr(client, "host", "") if client else ""
    return str(value or "unknown")[:128]

def _admit(client):
    now = time.monotonic()
    with _GUARD:
        while _ACTIVITY and next(iter(_ACTIVITY.values()))[1] < now - _WINDOW:
            _ACTIVITY.popitem(last=False)
        count, start = _ACTIVITY.pop(client, (0, now))
        if now - start >= _WINDOW:
            count, start = 0, now
        if count >= _MAX_REQUESTS:
            _ACTIVITY[client] = (count, start)
            return False
        _ACTIVITY[client] = (count + 1, start)
        while len(_ACTIVITY) > _MAX_CLIENTS:
            _ACTIVITY.popitem(last=False)
        return True

def _run_prediction(key, markets):
    result = forecast(key)
    outputs = result.get("model_outputs")
    if not isinstance(outputs, dict) or not _ALLOWED.issubset(outputs):
        raise RuntimeError("Model output roles incomplete")
    return {"status":"RESEARCH_PREDICTION", "fixture_key":key,
            "model_result":{**result, "model_outputs":{k:outputs[k] for k in markets}},
            "disclaimer":"Research estimates only; not guaranteed outcomes."}

def install(api):
    @api.get("/api/v1/fixtures")
    def public_fixtures(request: Request):
        if not enabled():
            return JSONResponse(status_code=503, content={"error":"Public predictions pending acceptance"})
        if not _admit(_client_key(request)):
            return JSONResponse(status_code=429, content={"error":"Rate limit exceeded"})
        try:
            return fixtures_summary()
        except PredictionAbstained as exc:
            return JSONResponse(status_code=422, content={"status":"ABSTAIN", "reason":str(exc)})
        except Exception:
            return JSONResponse(status_code=503, content={"error":"Fixture provider unavailable"})

    @api.post("/api/v1/predict")
    async def public_predict(request: Request):
        if not enabled():
            return JSONResponse(status_code=503, content={"error":"Public predictions pending acceptance"})
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return JSONResponse(status_code=415, content={"error":"JSON required"})
        if not _admit(_client_key(request)):
            return JSONResponse(status_code=429, content={"error":"Rate limit exceeded"})
        try:
            raw = await request.body()
            if len(raw) > 4096:
                raise ValueError("Request too large")
            import json
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) - {"fixture_key", "markets"}:
                raise ValueError("Unexpected fields")
            key = body.get("fixture_key")
            markets = body.get("markets", sorted(_ALLOWED))
            if (not isinstance(key, str) or not 1 <= len(key) <= 160
                or not isinstance(markets, list) or not 1 <= len(markets) <= 3
                or any(not isinstance(x, str) for x in markets)
                or len(set(markets)) != len(markets) or not set(markets) <= _ALLOWED):
                raise ValueError("Invalid fixture or markets")
        except (ValueError, TypeError, UnicodeError):
            return JSONResponse(status_code=422, content={"error":"Invalid prediction request"})
        try:
            return _run_prediction(key, markets)
        except PredictionAbstained as exc:
            return JSONResponse(status_code=422, content={"status":"ABSTAIN", "reason":str(exc)})
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Real prediction failed")
            return JSONResponse(status_code=503, content={"error":"Prediction engine temporarily unavailable"})

def add_ui(blocks, gr):
    # This is a new panel; all existing Gradio tabs remain intact.
    with blocks:
        with gr.Tab("Free Live Predictions"):
            gr.Markdown("### Free GoogleSaada V8 Predictions\nNo login required. Research estimates are not guaranteed outcomes.")
            fixture = gr.Dropdown(label="Verified upcoming fixture", choices=[], interactive=True)
            reload_button = gr.Button("Load upcoming fixtures")
            go = gr.Button("Predict selected fixture", variant="primary")
            result = gr.JSON(label="Real model prediction / abstention")
            def fetch_ui(request: gr.Request):
                if not enabled():
                    return gr.update(choices=[], value=None), {"error":"Public predictions pending acceptance"}
                if not _admit(_client_key(request)):
                    return gr.update(), {"error":"Rate limit exceeded"}
                try:
                    data = fixtures_summary()
                    options = [(f.get("home_name", "Home")+" vs "+f.get("away_name", "Away")+" | "+f.get("kickoff_utc", ""), f["fixture_key"]) for f in data["fixtures"]]
                    return gr.update(choices=options, value=None), {"available_fixtures":len(options), "retrieved_utc":data.get("retrieved_utc")}
                except PredictionAbstained as exc:
                    return gr.update(choices=[], value=None), {"status":"ABSTAIN", "reason":str(exc)}
                except Exception:
                    return gr.update(choices=[], value=None), {"error":"Fixture service temporarily unavailable"}
            def predict_ui(key, request: gr.Request):
                if not enabled():
                    return {"error":"Public predictions pending acceptance"}
                if not _admit(_client_key(request)):
                    return {"error":"Rate limit exceeded"}
                if not isinstance(key, str) or not 1 <= len(key) <= 160:
                    return {"error":"Select a verified fixture"}
                try:
                    return _run_prediction(key, sorted(_ALLOWED))
                except PredictionAbstained as exc:
                    return {"status":"ABSTAIN", "reason":str(exc)}
                except Exception:
                    return {"error":"Prediction engine temporarily unavailable"}
            reload_button.click(fetch_ui, outputs=[fixture, result], show_progress="minimal")
            go.click(predict_ui, inputs=[fixture], outputs=[result], show_progress="minimal")
