
from fastapi import FastAPI, Request, HTTPException
import hashlib
import hmac
import uuid

def create_offline_mixed_access_app(
    policy, trusted_origin, csrf_key, mock_explainer
):
    """
    Isolated offline architecture test.

    NOT a production deployment or login implementation.
    The caller must supply a mock-only explanation function.
    """

    if (
        not trusted_origin.startswith("https://")
        or not isinstance(csrf_key, bytes)
        or len(csrf_key) < 32
    ):
        raise ValueError("Invalid test configuration")

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None
    )

    def authenticate(request):
        token = request.cookies.get("gs_agent_identity", "")

        try:
            identity = policy.verify_identity(token)
        except Exception:
            raise HTTPException(
                status_code=401, detail="unauthorized"
            )

        return token, identity

    def csrf_value(token):
        message = (
            "googlesaada-csrf-v1:" + token
        ).encode("utf-8")

        return hmac.new(
            csrf_key,
            message,
            hashlib.sha256
        ).hexdigest()

    @app.get("/public/health")
    def public_health():
        return {
            "status": "public_available",
            "actual_model_invoked": False
        }

    @app.post("/public/predict")
    def public_prediction_demo():
        return {
            "status": "MOCK_ONLY",
            "actual_model_invoked": False
        }

    @app.get("/agent/csrf")
    def csrf_endpoint(request: Request):
        token, _ = authenticate(request)

        return {
            "csrf_token": csrf_value(token)
        }

    @app.post("/agent/explain")
    def protected_explanation(request: Request):

        if request.headers.get("origin") != trusted_origin:
            raise HTTPException(
                status_code=403,
                detail="origin_denied"
            )

        token, _ = authenticate(request)

        supplied = request.headers.get("x-gs-csrf", "")

        if not hmac.compare_digest(
            supplied,
            csrf_value(token)
        ):
            raise HTTPException(
                status_code=403,
                detail="csrf_denied"
            )

        request_id = "test-" + uuid.uuid4().hex

        try:
            policy.reserve(token, request_id)

        except Exception as exc:
            if type(exc).__name__ == "PolicyDenied":
                reason = str(exc)

                rate_reasons = {
                    "daily_request_quota",
                    "daily_budget_limit",
                    "concurrency_limit"
                }

                code = 429 if reason in rate_reasons else 403

                raise HTTPException(
                    status_code=code,
                    detail="policy_denied"
                )

            raise HTTPException(
                status_code=503,
                detail="policy_unavailable"
            )

        try:
            return {
                "status": "MOCK_ONLY",
                "answer": mock_explainer(),
                "paid_api_used": False
            }
        finally:
            policy.finish(request_id)

    return app
