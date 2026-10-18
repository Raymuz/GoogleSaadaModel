# GoogleSaada V8 public prediction integration candidate

Based on private staging revision `1597f62d17cf574373208d53cec3f617fc42dec1`.

Adds a real FastAPI `POST /api/v1/predict`, using the existing verified fixture snapshot and worldwide models, original V2O short-lived signed identity validation, and original persistent 20-credit UTC-day ledger. Does not create identity sessions, fake fixtures, bypass quotas, or promote to production.

**Default: locked (HTTP 503).** Only set `GS_PREDICTION_API_ENABLED=1` after a trusted identity issuer issues the `gs_prediction_identity` session cookie, `GS_PREDICT_SIGNING_KEY_HEX`, issuer/audience, trusted HTTPS origins, `GS_PREDICT_AUTH_ENABLED=1`, and `GS_PREDICT_LEDGER_PATH=/data/...` with `GS_PREDICT_LEDGER_PRODUCTION_READY=1` are configured and tested. A frontend/client must be wired to the new endpoint and its browser credentials; this bundle does not do that.

Important: the existing V8 entrypoint has `openapi_url=None`. Consequently `/openapi.json` 404 cannot be used to test route existence. Test POST `/api/v1/predict` with API disabled: expected 503. The original GET route also continues to return 503. The private internal forecasting routes are unchanged. HTTP 200 from this API will require actual prediction and settled credits.

**Not yet full-release accepted:** provider score corroboration/history ingestion, two calibration approvals, external signed-session issuance and HTTPS proxy trust, functional UI integration, specialist-agent functionality, all 13 pathways, complete end-to-end acceptance, public production promotion. Do not treat restoring source files alone as proof of these features.
