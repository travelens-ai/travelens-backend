from flask import Blueprint, request, jsonify

from core.db import is_db_ready
import features.messaging.service as service

messaging_bp = Blueprint("messaging", __name__)


@messaging_bp.before_request
def check_db_ready():
    if not is_db_ready():
        return jsonify({"status": "error", "message": "Database is connecting, please try again shortly"}), 503


@messaging_bp.route("/messaging", methods=["POST"])
def register_token():
    """Register/update an FCM push token for a device
    ---
    tags:
      - Messaging
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - device_id
            - fcm_token
          properties:
            device_id:
              type: string
              example: "2b62682caa3b7cde"
            fcm_token:
              type: string
              example: "dGhpcy1pcy1hLWZjbS10b2tlbg"
    responses:
      201:
        description: Token saved
      200:
        description: Token updated
      400:
        description: Missing required field
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    device_id = data.get("device_id")
    fcm_token = data.get("fcm_token")

    missing = [f for f, v in (("device_id", device_id), ("fcm_token", fcm_token)) if not v]
    if missing:
        return jsonify({"status": "error", "message": f"{', '.join(missing)} is required"}), 400

    # user_id comes from the verified token when a logged-in user submits; device
    # callers have none.
    user_id = getattr(request, "user_id", None)
    if user_id is not None:
        user_id = str(user_id)

    row_id, (status, msg, code) = service.save_token(
        device_id=device_id,
        fcm_token=fcm_token,
        user_id=user_id,
    )
    if row_id is not None:
        return jsonify({"status": status, "message": msg, "id": row_id}), code
    return jsonify({"status": status, "message": msg}), code


@messaging_bp.route("/get-messaging", methods=["GET"])
def list_tokens():
    """Fetch all device-token registrations (device_id + user_id, no token value)
    ---
    tags:
      - Messaging
    responses:
      200:
        description: List of device-token registrations
      500:
        description: Server error
    """
    rows, (status, msg, code) = service.list_tokens()
    if rows is not None:
        return jsonify({"status": status, "message": msg, "data": rows}), code
    return jsonify({"status": status, "message": msg}), code


@messaging_bp.route("/messaging", methods=["PUT"])
def update_token_user():
    """Attach a user_id to a device's push token after login
    ---
    tags:
      - Messaging
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - device_id
          properties:
            device_id:
              type: string
              example: "2b62682caa3b7cde"
    responses:
      200:
        description: user_id updated
      401:
        description: Authentication required (logged-in user token)
      404:
        description: No device-only token found for this device
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    device_id = data.get("device_id")
    if not device_id:
        return jsonify({"status": "error", "message": "device_id is required"}), 400

    # The user_id to attach is taken from the verified logged-in token, never the
    # body — so only an authenticated user can claim a device's token.
    user_id = getattr(request, "user_id", None)
    if user_id is None:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    user_id = str(user_id)

    _, (status, msg, code) = service.update_user_id(device_id=device_id, user_id=user_id)
    return jsonify({"status": status, "message": msg}), code


@messaging_bp.route("/send-notification", methods=["POST"])
def send_notification():
    """Send a Firebase push notification to a target audience

    Targeting precedence: an explicit `token` wins; otherwise a
    `device_id`/`user_id` filter is used; with none of those, the notification
    is broadcast to every registered device. Invalid tokens are pruned.
    ---
    tags:
      - Messaging
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - title
            - body
          properties:
            title:
              type: string
              example: "Weekend getaway ideas"
            body:
              type: string
              example: "Discover the best places to visit near you this weekend."
            image:
              type: string
              description: Optional banner image URL shown in the notification
              example: "https://travelens.in/app/assets/weekend-banner.png"
            link:
              type: string
              description: Optional deep-link URL opened when the notification is tapped (delivered in data as `link`)
              example: "travelens://itinerary/123"
            data:
              type: object
              description: Optional string key/values delivered with the push
              example: {"screen": "explore", "city": "Goa"}
            token:
              type: string
              description: Send to this single FCM token only
            device_id:
              type: string
              description: Send to all tokens for this device
            user_id:
              type: string
              description: Send to all tokens for this user
    responses:
      200:
        description: Notification(s) sent
      400:
        description: Missing title/body
      404:
        description: No registered tokens for the given target
      500:
        description: Send failure
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    title = data.get("title")
    body = data.get("body")
    if not title and not body:
        return jsonify({"status": "error", "message": "title or body is required"}), 400

    result, (status, msg, code) = service.send_notification(
        title=title,
        body=body,
        data=data.get("data"),
        token=data.get("token"),
        device_id=data.get("device_id"),
        user_id=data.get("user_id"),
        image=data.get("image"),
        link=data.get("link"),
    )
    payload = {"status": status, "message": msg}
    if result is not None:
        payload["result"] = result
    return jsonify(payload), code
