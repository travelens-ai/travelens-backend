"""Firebase Cloud Messaging (FCM) integration.

Lazily initializes a single firebase_admin app (the SDK rejects a second
default init) and exposes thin send helpers. Credentials come from
core.config — inline JSON, a file path, or Application Default Credentials,
tried in that order. Kept import-safe: importing this module never touches the
network or fails when firebase-admin is absent; init happens on first send.
"""
import json
import threading

from core.config import (
    FIREBASE_CREDENTIALS_JSON,
    FIREBASE_CREDENTIALS_FILE,
    FIREBASE_PROJECT_ID,
)

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

        project_id = FIREBASE_PROJECT_ID or None
        if FIREBASE_CREDENTIALS_JSON:
            sa = json.loads(FIREBASE_CREDENTIALS_JSON)
            cred = credentials.Certificate(sa)
            # Service-account JSON carries its own project_id; use it if no
            # explicit override was given.
            project_id = project_id or sa.get("project_id")
        elif FIREBASE_CREDENTIALS_FILE:
            with open(FIREBASE_CREDENTIALS_FILE) as f:
                sa = json.load(f)
            cred = credentials.Certificate(sa)
            project_id = project_id or sa.get("project_id")
        else:
            # ADC — GOOGLE_APPLICATION_CREDENTIALS or the workload's metadata.
            cred = credentials.ApplicationDefault()

        # FCM needs a project id. Certificate creds carry one, but ADC may not,
        # and the SDK errors with "Project ID is required" — pass it explicitly.
        options = {"projectId": project_id} if project_id else None
        _app = firebase_admin.initialize_app(cred, options)
        return _app


def _build_notification(title, body, image):
    """Build an FCM Notification (with optional image), or None when there's no
    title/body/image to show."""
    from firebase_admin import messaging

    if not (title or body or image):
        return None
    return messaging.Notification(title=title, body=body, image=image)


def _build_data(data, link):
    """Merge the deep-link URL into the data payload under `link` so the client
    can route to the target screen when the notification is tapped. FCM requires
    all data values to be strings."""
    payload = {k: str(v) for k, v in (data or {}).items()}
    if link:
        payload["link"] = str(link)
    return payload


def _android_config(link):
    """Android config that (a) opens the deep link on tap via a click_action
    intent when `link` is set, so a dedicated intent-filter activity can catch
    it. The link also rides in the data payload as a fallback."""
    from firebase_admin import messaging

    if not link:
        return None
    return messaging.AndroidConfig(
        notification=messaging.AndroidNotification(click_action="OPEN_DEEP_LINK"),
    )


def send_to_token(token, *, title=None, body=None, data=None, image=None, link=None):
    """Send a notification to a single FCM registration token. `data` is an
    optional dict of string key/values delivered alongside the notification;
    `image` is an optional banner image URL; `link` is an optional deep-link URL
    the client opens on tap (delivered in data as `link`). Returns the FCM
    message id string. Raises on invalid token / send failure."""
    from firebase_admin import messaging

    _get_app()
    message = messaging.Message(
        token=token,
        notification=_build_notification(title, body, image),
        data=_build_data(data, link),
        android=_android_config(link),
    )
    return messaging.send(message)


def send_to_tokens(tokens, *, title=None, body=None, data=None, image=None, link=None):
    """Multicast a notification to many tokens (chunked at FCM's 500/batch
    limit). `image` is an optional banner URL; `link` an optional deep-link the
    client opens on tap. Returns (success_count, failure_count, invalid_tokens)
    where invalid_tokens are those FCM rejected as unregistered/invalid so the
    caller can prune them."""
    from firebase_admin import messaging

    _get_app()
    notification = _build_notification(title, body, image)
    payload = _build_data(data, link)
    android = _android_config(link)

    success = failure = 0
    invalid = []
    # FCM caps a single send_each_for_multicast at 500 tokens.
    for start in range(0, len(tokens), 500):
        chunk = tokens[start : start + 500]
        message = messaging.MulticastMessage(
            tokens=chunk, notification=notification, data=payload, android=android
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
