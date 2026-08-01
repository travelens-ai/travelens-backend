"""Firebase Cloud Messaging (FCM) integration.

Lazily initializes a single firebase_admin app (the SDK rejects a second
default init) and exposes thin send helpers. Credentials come from
core.config — inline JSON, a file path, or Application Default Credentials,
tried in that order. Kept import-safe: importing this module never touches the
network or fails when firebase-admin is absent; init happens on first send.
"""
import json
import threading

from core.config import FIREBASE_CREDENTIALS_JSON, FIREBASE_CREDENTIALS_FILE

_lock = threading.Lock()
_app = None  # cached firebase_admin.App once initialized


def _get_app():
    """Return the initialized firebase_admin app, creating it once. Raises
    RuntimeError if firebase-admin isn't installed or credentials are bad."""
    global _app
    if _app is not None:
        return _app
    with _lock:
        if _app is not None:  # double-checked: another thread may have won
            return _app
        try:
            import firebase_admin
            from firebase_admin import credentials
        except ImportError as exc:
            raise RuntimeError(
                "firebase-admin is not installed; add it to requirements.txt"
            ) from exc

        if FIREBASE_CREDENTIALS_JSON:
            cred = credentials.Certificate(json.loads(FIREBASE_CREDENTIALS_JSON))
        elif FIREBASE_CREDENTIALS_FILE:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS_FILE)
        else:
            # ADC — GOOGLE_APPLICATION_CREDENTIALS or the workload's metadata.
            cred = credentials.ApplicationDefault()

        _app = firebase_admin.initialize_app(cred)
        return _app


def send_to_token(token, *, title=None, body=None, data=None):
    """Send a notification to a single FCM registration token. `data` is an
    optional dict of string key/values delivered alongside the notification.
    Returns the FCM message id string. Raises on invalid token / send failure."""
    from firebase_admin import messaging

    _get_app()
    message = messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body)
        if (title or body)
        else None,
        data={k: str(v) for k, v in (data or {}).items()},
    )
    return messaging.send(message)


def send_to_tokens(tokens, *, title=None, body=None, data=None):
    """Multicast a notification to many tokens (chunked at FCM's 500/batch
    limit). Returns (success_count, failure_count, invalid_tokens) where
    invalid_tokens are those FCM rejected as unregistered/invalid so the caller
    can prune them."""
    from firebase_admin import messaging

    _get_app()
    notification = (
        messaging.Notification(title=title, body=body) if (title or body) else None
    )
    payload = {k: str(v) for k, v in (data or {}).items()}

    success = failure = 0
    invalid = []
    # FCM caps a single send_each_for_multicast at 500 tokens.
    for start in range(0, len(tokens), 500):
        chunk = tokens[start : start + 500]
        message = messaging.MulticastMessage(
            tokens=chunk, notification=notification, data=payload
        )
        resp = messaging.send_each_for_multicast(message)
        success += resp.success_count
        failure += resp.failure_count
        for token, result in zip(chunk, resp.responses):
            if not result.success:
                exc = result.exception
                # Unregistered / invalid-argument tokens are dead — flag them.
                if isinstance(
                    exc,
                    (
                        messaging.UnregisteredError,
                        messaging.SenderIdMismatchError,
                    ),
                ) or "invalid" in str(exc).lower():
                    invalid.append(token)
    return success, failure, invalid
