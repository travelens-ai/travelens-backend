import hashlib
import hmac
import json
import time
import base64
from functools import wraps

from flask import request, jsonify

from core.config import JWT_SECRET, JWT_EXPIRY, DEVICE_JWT_SECRET


def decode_device_token(token):
    """Verify a device JWT signed by the client with DEVICE_JWT_SECRET (HS256).

    Expected payload: {"device_id": <str>, "iat": <int>, "exp": <int optional>}.
    Returns the payload dict on a valid signature (and unexpired, if `exp` is
    present), else None. Used for not-logged-in requests: the client proves it
    is our app by signing with the shared secret and naming its device_id."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, signature = parts
        expected_sig = hmac.HMAC(DEVICE_JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        payload_data = json.loads(base64.urlsafe_b64decode(payload))
        # exp is optional for device tokens; enforce it when present.
        exp = payload_data.get("exp")
        if exp is not None and exp < int(time.time()):
            return None
        if not payload_data.get("device_id"):
            return None
        return payload_data
    except Exception:
        return None


def create_token(user_id, email):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload_data = {
        "user_id": user_id,
        "email": email,
        "exp": int(time.time()) + JWT_EXPIRY,
        "iat": int(time.time()),
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).decode().rstrip("=")
    signature = hmac.HMAC(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"{header}.{payload}.{signature}"


def decode_token(token):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, signature = parts
        expected_sig = hmac.HMAC(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).hexdigest()
        if signature != expected_sig:
            return None
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        payload_data = json.loads(base64.urlsafe_b64decode(payload))
        if payload_data["exp"] < int(time.time()):
            return None
        return payload_data
    except Exception:
        return None


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        if not token:
            return jsonify({"status": "error", "message": "Token is missing"}), 401
        payload = decode_token(token)
        if not payload:
            return jsonify({"status": "error", "message": "Token is invalid or expired"}), 401
        request.user_id = payload["user_id"]
        request.user_email = payload["email"]
        return f(*args, **kwargs)
    return decorated
