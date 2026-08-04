import json
from urllib.parse import quote

from core.db import get_connection
from integrations import firebase

# Lazily-built Azure OpenAI client (shared across calls). Kept module-level and
# lazy so importing this module never touches the network / OpenAI SDK — only
# the AI-copy endpoint pays that cost, on first use.
_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        try:
            from langfuse.openai import AzureOpenAI  # auto-traces the call
        except ImportError:
            from openai import AzureOpenAI
        from core.config import (
            AZURE_OPENAI_API_KEY,
            AZURE_OPENAI_ENDPOINT,
            AZURE_OPENAI_API_VERSION,
        )
        _openai_client = AzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION,
        )
    return _openai_client


# Base for every deep link.
_DEEPLINK_BASE = "https://travelens.in"

# Notification types the model may pick from, each mapping to one allowed
# in-app route. GENERATE_ITINERARY additionally takes query params the model
# fills from the campaign (place, noOfDays, interest, foodPref, foodType,
# groupType, date). Keep in sync with the client's deep-link routing.
NOTIFICATION_TYPES = ["GENERATE_ITINERARY", "CUSTOM_ITINERARY", "HISTORY", "FAVORITE"]

# type -> fixed path under _DEEPLINK_BASE.
_ROUTE_PATHS = {
    "GENERATE_ITINERARY": "/app/itinerary/generate",
    "CUSTOM_ITINERARY": "/app/custom-itinerary",
    "HISTORY": "/app/history",
    "FAVORITE": "/app/favorite",
}

# Query params the model may supply for the GENERATE_ITINERARY route, in the
# order they should appear on the URL.
_GENERATE_PARAMS = ["place", "noOfDays", "interest", "foodPref", "foodType", "groupType", "date"]


def _build_deeplink(ntype, params):
    """Assemble the full deep link for a notification type. Only GENERATE_ITINERARY
    carries query params; unknown params are dropped and values URL-encoded."""
    path = _ROUTE_PATHS.get(ntype, _ROUTE_PATHS["GENERATE_ITINERARY"])
    url = _DEEPLINK_BASE + path
    if ntype == "GENERATE_ITINERARY" and isinstance(params, dict):
        pairs = []
        for key in _GENERATE_PARAMS:
            val = params.get(key)
            if val is None or str(val).strip() == "":
                continue
            pairs.append(f"{key}={quote(str(val).strip())}")
        if pairs:
            url += "?" + "&".join(pairs)
    return url


def generate_notification_copy(*, prompt, tone=None):
    """Generate push-notification content from an admin prompt using the LLM.
    `prompt` describes the campaign/intent (e.g. "weekend getaways near Goa, 30%
    off hotels"); `tone` is an optional style hint ("exciting", "urgent",
    "friendly"). Returns (result, (status, message, code)) where result is
    {"title", "body", "type", "link", "image"}:
      - type:  one of NOTIFICATION_TYPES — the in-app route the notification opens
      - link:  the full https://travelens.in deep link for that type (with query
               params for GENERATE_ITINERARY)
      - image: a relevant banner image URL (built from model-suggested keywords)

    Constraints baked into the prompt: FCM notifications truncate aggressively,
    so title <= ~40 chars and body <= ~120 chars. Output is forced to JSON."""
    from core.config import AZURE_OPENAI_CHAT_DEPLOYMENT

    if not prompt or not str(prompt).strip():
        return None, ("error", "prompt is required", 400)

    tone_line = f"Tone: {tone}.\n" if tone else ""
    system_msg = (
        "You are a marketing copywriter for Travelens, an AI travel-itinerary "
        "app for India. Write a single push notification for the given campaign. "
        "Rules: title <= 40 characters, body <= 120 characters, no emojis unless "
        "they add clear value, no quotes around the text, make it action-driven "
        "and specific.\n"
        "Also decide where tapping the notification should take the user. Choose "
        f"exactly one `type` from {NOTIFICATION_TYPES}:\n"
        "- GENERATE_ITINERARY: kick off AI itinerary generation for a place. Use "
        "when the campaign promotes visiting a specific destination.\n"
        "- CUSTOM_ITINERARY: open the custom itinerary builder.\n"
        "- HISTORY: open the user's past trips.\n"
        "- FAVORITE: open the user's saved/favorite places.\n"
        "If (and only if) type is GENERATE_ITINERARY, also fill `params` with any "
        "of these you can infer from the campaign (omit unknowns, do NOT guess): "
        "place (destination), noOfDays (integer), interest, foodPref, foodType, "
        "groupType (e.g. family/couple/friends/solo), date (YYYY-MM-DD). For other "
        "types, `params` must be an empty object.\n"
        "Also give `image_keywords`: 1-3 comma-separated keywords for a relevant "
        "banner photo (e.g. 'goa,beach,sunset').\n"
        "Respond ONLY with a JSON object: "
        '{"title": "...", "body": "...", "type": "...", '
        '"params": {...}, "image_keywords": "..."}.'
    )
    user_msg = f"{tone_line}Campaign: {prompt}"

    try:
        client = _get_openai_client()
        resp = client.chat.completions.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.8,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content or ""
        data = json.loads(text)
        title = (data.get("title") or "").strip()
        body = (data.get("body") or "").strip()
        if not title and not body:
            return None, ("error", "Model returned empty copy", 502)

        # Validate/normalize type; fall back to GENERATE_ITINERARY if off-list.
        ntype = (data.get("type") or "").strip().upper()
        if ntype not in NOTIFICATION_TYPES:
            ntype = "GENERATE_ITINERARY"

        # Build the deep link server-side from the fixed route table, so the URL
        # is always one of the four allowed routes with a correct domain.
        link = _build_deeplink(ntype, data.get("params"))

        # Turn keywords into a real, always-resolvable stock image URL. The admin
        # can replace this before sending if they have a specific asset.
        keywords = (data.get("image_keywords") or "").strip()
        image = f"https://source.unsplash.com/1200x630/?{quote(keywords or 'travel,india')}"

        return {
            "title": title,
            "body": body,
            "type": ntype,
            "link": link,
            "image": image,
        }, ("success", "Copy generated", 200)
    except json.JSONDecodeError:
        return None, ("error", "Model returned malformed JSON", 502)
    except Exception as e:
        return None, ("error", str(e), 500)


def _resolve_tokens(*, device_id=None, user_id=None):
    """Return a list of distinct fcm_token strings from device_tokens matching
    the given filter. With neither device_id nor user_id, returns every token
    (broadcast). Duplicates are removed so a device isn't notified twice."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        sql = "SELECT DISTINCT fcm_token FROM device_tokens WHERE fcm_token IS NOT NULL"
        params = []
        if device_id is not None:
            sql += " AND device_id = ?"
            params.append(device_id)
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        cursor.execute(sql, params)
        return [r[0] for r in cursor.fetchall() if r[0]]
    finally:
        cursor.close()
        conn.close()


def _prune_tokens(tokens):
    """Delete rows whose fcm_token FCM reported as invalid/unregistered."""
    if not tokens:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.executemany(
            "DELETE FROM device_tokens WHERE fcm_token = ?",
            [(t,) for t in tokens],
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


def _log_sent_notification(*, title, body, image, link, data, target_type,
                           target_value, result, status, error):
    """Persist an audit row for a send attempt. Best-effort: a logging failure
    must never break the actual send, so errors here are swallowed (printed)."""
    result = result or {}
    try:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO sent_notifications
                    (title, body, image, link, data, target_type, target_value,
                     targeted, success_count, failure_count, pruned, status, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    body,
                    image,
                    link,
                    json.dumps(data) if data else None,
                    target_type,
                    target_value,
                    result.get("targeted"),
                    result.get("success"),
                    result.get("failure"),
                    result.get("pruned"),
                    status,
                    error,
                ),
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    except Exception as e:
        print(f"[messaging] failed to log sent notification: {e}")


def send_notification(*, title, body, data=None, token=None, device_id=None,
                       user_id=None, image=None, link=None):
    """Send an FCM push notification to a target audience. Targeting precedence:
    an explicit `token` > `device_id`/`user_id` filter > broadcast to all.
    `image` is an optional banner URL and `link` an optional deep-link the client
    opens on tap. Invalid tokens FCM rejects are pruned from device_tokens. Every
    attempt (success or failure) is recorded in sent_notifications.
    Returns (result, (status, message, code)) where result is a dict of send counts."""
    # Resolve which target this send is aimed at, for the audit log.
    if token:
        target_type, target_value = "token", token
    elif user_id:
        target_type, target_value = "user", str(user_id)
    elif device_id:
        target_type, target_value = "device", str(device_id)
    else:
        target_type, target_value = "broadcast", None

    result = None
    status = "success"
    error = None
    outcome = None
    try:
        if token:
            msg_id = firebase.send_to_token(
                token, title=title, body=body, data=data, image=image, link=link
            )
            result = {"success": 1, "failure": 0, "message_id": msg_id}
            outcome = (result, ("success", "Notification sent", 200))
        else:
            tokens = _resolve_tokens(device_id=device_id, user_id=user_id)
            if not tokens:
                status, error = "error", "No registered tokens for the given target"
                outcome = (None, ("error", error, 404))
            else:
                success, failure, invalid = firebase.send_to_tokens(
                    tokens, title=title, body=body, data=data, image=image, link=link
                )
                _prune_tokens(invalid)
                result = {
                    "success": success,
                    "failure": failure,
                    "targeted": len(tokens),
                    "pruned": len(invalid),
                }
                outcome = (result, ("success", "Notifications sent", 200))
    except Exception as e:
        status, error = "error", str(e)
        outcome = (None, ("error", error, 500))

    _log_sent_notification(
        title=title, body=body, image=image, link=link, data=data,
        target_type=target_type, target_value=target_value,
        result=result, status=status, error=error,
    )
    return outcome


def save_token(*, device_id, fcm_token, user_id=None):
    """Upsert an FCM token for a (device_id, user_id) pair. Updates the token in
    place when the pair already exists, else inserts a new row. Returns
    (row_id, (status, message, code)).

    user_id may be NULL, so the existence check uses an IS-NULL-aware match
    rather than a plain `= ?` (which never matches NULL)."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id FROM device_tokens
            WHERE device_id = ?
              AND ((user_id IS NULL AND ? IS NULL) OR user_id = ?)
            """,
            (device_id, user_id, user_id),
        )
        row = cursor.fetchone()
        if row:
            row_id = int(row[0])
            cursor.execute(
                "UPDATE device_tokens SET fcm_token = ?, updated_at = SYSUTCDATETIME() WHERE id = ?",
                (fcm_token, row_id),
            )
            conn.commit()
            return row_id, ("success", "Token updated", 200)

        cursor.execute(
            """
            INSERT INTO device_tokens (device_id, user_id, fcm_token)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?)
            """,
            (device_id, user_id, fcm_token),
        )
        row = cursor.fetchone()
        row_id = int(row[0]) if row and row[0] is not None else None
        conn.commit()
        return row_id, ("success", "Token saved", 201)
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def list_tokens():
    """Return all device-token registrations joined to the owning user, as a
    list of dicts keyed by column name. Returns (rows, (status, message, code)).

    Identity resolution: a token is linked to a user by `user_id` when present,
    otherwise it falls back to `device_id`. Both are matched against `users.id`
    via COALESCE; since users.id is an INT and device_id is a free-form string,
    TRY_CONVERT is used so a non-numeric device_id yields no match (NULL user
    columns) instead of erroring the whole query."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT dt.*,
                   u.name  AS user_name,
                   u.email AS user_email,
                   u.phone AS user_phone,
                   u.profile_picture AS user_profile_picture
            FROM device_tokens dt
            LEFT JOIN users u
              ON u.id = TRY_CONVERT(INT, COALESCE(dt.user_id, dt.device_id))
            ORDER BY dt.updated_at DESC
            """
        )
        columns = [col[0] for col in cursor.description]
        rows = [
            {
                col: (val.isoformat() if hasattr(val, "isoformat") else val)
                for col, val in zip(columns, r)
            }
            for r in cursor.fetchall()
        ]
        return rows, ("success", "Tokens fetched", 200)
    except Exception as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def update_user_id(*, device_id, user_id):
    """Set the user_id on a device's token row(s) — typically when a device-only
    registration (user_id NULL) is claimed after the user logs in. Returns
    (rows_updated, (status, message, code)).

    If a row for (device_id, user_id) already exists, the UNIQUE index would
    reject the update, so those conflicting device-only rows are removed first."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Drop any device-only row that would collide with the target pair.
        cursor.execute(
            """
            DELETE FROM device_tokens
            WHERE device_id = ? AND user_id IS NULL
              AND EXISTS (
                  SELECT 1 FROM device_tokens
                  WHERE device_id = ? AND user_id = ?
              )
            """,
            (device_id, device_id, user_id),
        )
        cursor.execute(
            "UPDATE device_tokens SET user_id = ?, updated_at = SYSUTCDATETIME() "
            "WHERE device_id = ? AND user_id IS NULL",
            (user_id, device_id),
        )
        updated = cursor.rowcount
        conn.commit()
        if updated == 0:
            return 0, ("error", "No device-only token found for this device", 404)
        return updated, ("success", "user_id updated", 200)
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()
