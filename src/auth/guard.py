"""Global request authentication guard.

Every request must present ONE of:
  1. A logged-in user JWT in `Authorization: Bearer <token>` (signed with
     JWT_SECRET), or
  2. A device JWT in `X-Device-Token` (signed by the client with
     DEVICE_JWT_SECRET, carrying a `device_id` claim).

If the user JWT is valid it takes precedence and `request.user_id` /
`request.user_email` are set. Otherwise the device token is checked and
`request.device_id` is set. Auth/login, health and API-docs endpoints are
exempt (see EXEMPT_PATHS / EXEMPT_PREFIXES) so a client can obtain a token in
the first place.
"""
from flask import request, jsonify

from auth.jwt_utils import decode_token, decode_device_token

# Exact paths that never require auth (obtaining a token, health, root).
EXEMPT_PATHS = {
    "/send-otp",
    "/verify-otp",
    "/signup",
    "/login",
    "/forgot-password",
    "/reset-password",
    "/google-login",
    "/health",
    "/",
}

# Path prefixes that never require auth (Swagger UI + spec, static, CORS
# preflight lands here too via method check below).
EXEMPT_PREFIXES = (
    "/apidocs",
    "/apispec",
    "/flasgger_static",
    "/swagger",
    "/static",
)


def _is_exempt(path):
    if path in EXEMPT_PATHS:
        return True
    return any(path.startswith(p) for p in EXEMPT_PREFIXES)


def current_identity():
    """The caller's identity for the current request, taken ONLY from the
    verified token (never from the request body/query). Returns the logged-in
    user_id when present, else the device_id. Both are set by
    authenticate_request(). Returns None if somehow neither is set (shouldn't
    happen on a protected route). Coerced to str so it matches the string
    user_id column used by favorites/history."""
    user_id = getattr(request, "user_id", None)
    if user_id is not None:
        return str(user_id)
    device_id = getattr(request, "device_id", None)
    if device_id is not None:
        return str(device_id)
    return None


def authenticate_request():
    """Flask before_request hook. Returns None to allow the request, or a
    (response, status) tuple to reject it with 401."""
    # Always allow CORS preflight.
    if request.method == "OPTIONS":
        return None

    if _is_exempt(request.path):
        return None

    # 1. Prefer a logged-in user JWT.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        payload = decode_token(auth_header[7:].strip())
        if payload:
            request.user_id = payload.get("user_id")
            request.user_email = payload.get("email")
            request.device_id = None
            return None

    # 2. Fall back to a device token.
    device_token = request.headers.get("X-Device-Token", "")
    if device_token:
        payload = decode_device_token(device_token)
        if payload:
            request.user_id = None
            request.user_email = None
            request.device_id = payload.get("device_id")
            return None

    return jsonify({
        "status": "error",
        "message": "Authentication required: provide a valid Authorization "
                   "Bearer token (logged-in user) or X-Device-Token (device).",
    }), 401
