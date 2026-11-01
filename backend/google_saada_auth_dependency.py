
COOKIE_NAME = "gs_agent_identity"

def make_auth_dependency(verify_identity=None):
    """
    FastAPI/Gradio authentication boundary.

    Requires an explicitly supplied trusted identity verifier.
    Defaults to denying all requests.

    No client-provided username, query parameter or untrusted
    forwarded HTTPS header is accepted as proof of identity.
    """

    def dependency(request):

        if not callable(verify_identity):
            return None

        if str(request.url.scheme).lower() != "https":
            return None

        token = request.cookies.get(COOKIE_NAME)

        if not isinstance(token, str):
            return None

        if not (20 <= len(token) <= 8192):
            return None

        try:
            identity = verify_identity(token)
        except Exception:
            return None

        if not isinstance(identity, str) or not identity.strip():
            return None

        return identity

    return dependency
