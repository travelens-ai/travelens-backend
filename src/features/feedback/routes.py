from flask import Blueprint, request, jsonify

from core.db import is_db_ready
from auth.guard import current_identity
import features.feedback.service as service

feedback_bp = Blueprint("feedback", __name__)


@feedback_bp.before_request
def check_db_ready():
    if not is_db_ready():
        return jsonify({"status": "error", "message": "Database is connecting, please try again shortly"}), 503


@feedback_bp.route("/feedback", methods=["POST"])
def submit_feedback():
    """Submit user feedback
    ---
    tags:
      - Feedback
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - device_id
            - message
            - type
          properties:
            type:
              type: string
              example: "bug"
            message:
              type: string
              example: "The itinerary page crashed when I tapped share."
            device_id:
              type: string
              example: "2b62682caa3b7cde"
            name:
              type: string
              example: "Test"
            email:
              type: string
              example: "gkgig@iguf.uff"
            phone:
              type: string
              example: "9999999999"
            itinerary_id:
              type: integer
              example: 589
    responses:
      201:
        description: Feedback submitted
      400:
        description: Missing required field
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    type_ = data.get("type")
    message = data.get("message")
    device_id = data.get("device_id")

    missing = [f for f, v in (("type", type_), ("message", message), ("device_id", device_id)) if not v]
    if missing:
        return jsonify({"status": "error", "message": f"{', '.join(missing)} is required"}), 400

    # user_id comes from the verified token when a logged-in user submits; device
    # callers have none. Fall back to whatever identity the token carries.
    user_id = getattr(request, "user_id", None)
    if user_id is not None:
        user_id = str(user_id)

    feedback_id, (status, msg, code) = service.create_feedback(
        type=type_,
        message=message,
        device_id=device_id,
        user_id=user_id,
        name=data.get("name"),
        email=data.get("email"),
        phone=data.get("phone"),
        itinerary_id=data.get("itinerary_id"),
    )
    if feedback_id is not None:
        return jsonify({"status": status, "message": msg, "feedback_id": feedback_id}), code
    return jsonify({"status": status, "message": msg}), code
