"""Admin JWT: mint/verify tokens and the `admin_required` route decorator.

Mirrors the hand-rolled HS256 scheme in `auth/jwt_utils.py` (no PyJWT dependency)
but the payload carries `admin_id` instead of `user_id`, so an admin token and
an app-user token are never interchangeable even though both are signed with
JWT_SECRET. The decorator sets `request.admin_id` / `request.admin_email` from
the verified token only — never from the request body.
"""
import base64
import hashlib
import hmac
import json
import time
from functools import wraps

from flask import request, jsonify

from core.config import JWT_SECRET, JWT_EXPIRY


def create_admin_token(admin_id, email, status="admin"):
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).decode().rstrip("=")
    payload_data = {
        "admin_id": admin_id,
        "email": email,
        # Admin tier ('admin' | 'super admin'); gates super-admin-only routes.
        "status": status,
        "scope": "admin",
        "exp": int(time.time()) + JWT_EXPIRY,
        "iat": int(time.time()),
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(payload_data).encode()
    ).decode().rstrip("=")
    signature = hmac.HMAC(
        JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{header}.{payload}.{signature}"


def decode_admin_token(token):
    """Return the payload for a valid, unexpired admin token, else None.

    Rejects tokens that lack the admin scope/claim so an app-user JWT (same
    secret, but `user_id`/no scope) can't be replayed against admin routes."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, signature = parts
        expected_sig = hmac.HMAC(
            JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        payload_data = json.loads(base64.urlsafe_b64decode(payload))
        if payload_data.get("exp", 0) < int(time.time()):
            return None
        if payload_data.get("scope") != "admin" or payload_data.get("admin_id") is None:
            return None
        return payload_data
    except Exception:
        return None


def admin_required(f):
    """Guard an admin route: require a valid `Authorization: Bearer <admin JWT>`.

    On success sets request.admin_id / request.admin_email and calls the view.
    On any failure returns 401 with the uniform {"message": ...} shape the panel
    expects."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"message": "Not authenticated"}), 401
        payload = decode_admin_token(auth_header[7:].strip())
        if not payload:
            return jsonify({"message": "Invalid or expired token"}), 401
        request.admin_id = payload["admin_id"]
        request.admin_email = payload.get("email")
        request.admin_status = payload.get("status", "admin")
        return f(*args, **kwargs)
    return decorated


def super_admin_required(f):
    """Guard a route to super admins only. Runs after `admin_required` (so a
    valid admin token is already required) and re-checks the tier against the
    live DB — not just the token — so a demoted admin loses access immediately
    even if their old token still claims 'super admin'. 403 otherwise."""
    @wraps(f)
    @admin_required
    def decorated(*args, **kwargs):
        # Imported here to avoid a circular import at module load time.
        from auth.admin.service import get_admin_status
        if get_admin_status(request.admin_id) != "super admin":
            return jsonify({"message": "Super admin privilege required"}), 403
        return f(*args, **kwargs)
    return decorated
