
import os
import sys
import json
import secrets
import traceback
from pathlib import Path

from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

result = {
    "application_imported": False,
    "original_root_route": None,
    "original_ui_status": None,
    "registry_summary": None,
    "registry_models": None,
    "unqualified_prediction_blocked": None,
    "unauthorized_registry_rejected": None,
    "error": None
}

try:
    os.environ["GOOGLESAADA_MODEL_REGISTRY_PATH"] = (
        sys.argv[1]
    )
    token = secrets.token_urlsafe(48)
    os.environ["GOOGLESAADA_REGISTRY_API_TOKEN"] = token

    # Import original app.py plus additive registry router.
    from app import app

    result["application_imported"] = True

    with TestClient(
        app, raise_server_exceptions=False
    ) as client:

        headers = {
            "Authorization": "Bearer " + token
        }

        def request(method, path, **kwargs):
            try:
                response = client.request(
                    method, path, **kwargs
                )
                return {
                    "status": response.status_code,
                    "content_type": response.headers.get(
                        "content-type", ""
                    ),
                    "body_preview": response.text[:200]
                }
            except Exception as exc:
                return {
                    "error": type(exc).__name__
                }

        result["original_root_route"] = request(
            "GET", "/"
        )

        result["original_ui_status"] = request(
            "GET", "/api/ui/status"
        )

        result["registry_summary"] = request(
            "GET", "/v1/registry/summary",
            headers=headers
        )

        result["registry_models"] = request(
            "GET", "/v1/models",
            headers=headers
        )

        result["unqualified_prediction_blocked"] = request(
            "POST", "/v1/predict",
            headers=headers,
            json={
                "model_id": "test-unqualified",
                "features": {}
            }
        )

        result["unauthorized_registry_rejected"] = request(
            "GET", "/v1/models"
        )

except Exception as exc:
    result["error"] = (
        type(exc).__name__ + ": " + str(exc)[:500]
    )
    result["traceback"] = traceback.format_exc()[-3000:]

print("GOOGLESAADA_TEST_JSON_START")
print(json.dumps(result, default=str))
print("GOOGLESAADA_TEST_JSON_END")
