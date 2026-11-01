
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec
import os
import uuid

# Hard lock: cannot be enabled merely by changing environment variables.
_LIVE_RELEASE_APPROVED = False

def _load_local(name, filename):
    path = Path(__file__).resolve().parent / filename
    spec = spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError("Required security module unavailable")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

_policy_module = _load_local(
    "gs_paid_policy_staging",
    "google_saada_paid_policy.py"
)

_block_module = _load_local(
    "gs_blocked_policy_staging",
    "google_saada_agent_guard.py"
)

def _blocked():
    return dict(_block_module._BLOCKED_RESPONSE)

def _policy_from_environment():
    key_hex = os.environ["GS_AGENT_SIGNING_KEY_HEX"]
    key = bytes.fromhex(key_hex)

    if len(key) < 32:
        raise ValueError("Signing key too short")

    issuer = os.environ["GS_AGENT_ISSUER"]
    audience = os.environ["GS_AGENT_AUDIENCE"]
    db_path = os.environ["GS_AGENT_POLICY_DB"]

    limits = {
        "daily_requests":
            int(os.environ["GS_AGENT_DAILY_REQUESTS"]),
        "daily_budget_cents":
            int(os.environ["GS_AGENT_DAILY_BUDGET_CENTS"]),
        "reserve_cents_per_request":
            int(os.environ["GS_AGENT_RESERVE_CENTS"]),
        "max_concurrent":
            int(os.environ["GS_AGENT_MAX_CONCURRENT"]),
    }

    return _policy_module.LocalPaidPolicy(
        db_path=db_path,
        signing_key=key,
        issuer=issuer,
        audience=audience,
        **limits
    )

def _signed_assertion_from_request(request):
    """
    The future trusted authentication service must issue a
    short-lived signed assertion in a Secure, HttpOnly cookie.

    No username, session hash, textbox, query string or
    unverified proxy header is accepted.
    """
    underlying = getattr(request, "request", None)
    if underlying is None:
        return None

    url = getattr(underlying, "url", None)
    if getattr(url, "scheme", None) != "https":
        return None

    cookies = getattr(underlying, "cookies", None)
    if cookies is None or not hasattr(cookies, "get"):
        return None

    token = cookies.get("gs_agent_identity")

    if not isinstance(token, str):
        return None

    if not (20 <= len(token) <= 8192):
        return None

    return token

def authenticated_explanation_call(
    original_function, *args, request=None, **kwargs
):
    """
    Protects the original paid explanation function.

    Both the saved-code release lock and runtime conditions
    must be satisfied. The default is unconditional denial.
    """
    if not _LIVE_RELEASE_APPROVED:
        return _blocked()

    if os.environ.get("GS_AGENT_PAID_ENABLED") != "1":
        return _blocked()

    # These flags are operational prerequisites, NOT proof
    # that provider rates or storage have been certified.
    if os.environ.get("GS_PROVIDER_RATES_VERIFIED") != "1":
        return _blocked()

    if os.environ.get("GS_SHARED_QUOTA_READY") != "1":
        return _blocked()

    assertion = _signed_assertion_from_request(request)

    if assertion is None:
        return _blocked()

    try:
        policy = _policy_from_environment()

        # UUID is generated server-side.
        request_id = "gs-" + uuid.uuid4().hex

        # This verifies signature, expiry, issuer, audience,
        # permission, quota, concurrency and budget before
        # the explanation function is invoked.
        policy.reserve(assertion, request_id)

    except Exception:
        return _blocked()

    try:
        result = original_function(*args, **kwargs)
    except Exception:
        result = _blocked()

    try:
        policy.finish(request_id)
    except Exception:
        # Do not release reserved budget after uncertainty.
        return _blocked()

    return result
