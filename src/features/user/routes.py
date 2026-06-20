from flask import Blueprint, request, jsonify

from core.db import is_db_ready
from core.ads import get_inline_ads_config
import features.user.service as service

user_bp = Blueprint("user", __name__)


@user_bp.before_request
def check_db_ready():
    if not is_db_ready():
        return jsonify({"status": "error", "message": "Database is connecting, please try again shortly"}), 503


@user_bp.route("/favorite", methods=["POST"])
def add_favorite():
    """Add itinerary to favorites
    ---
    tags:
      - User
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - itinerary_id
            - user_id
          properties:
            itinerary_id:
              type: integer
              example: 1
            user_id:
              type: string
              description: Device ID (before signup) or user ID (after signup)
              example: "abc123-device-uuid"
    responses:
      201:
        description: Added to favorites
      409:
        description: Already in favorites
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    itinerary_id = data.get("itinerary_id")
    user_id = data.get("user_id")
    if not itinerary_id or not user_id:
        return jsonify({"status": "error", "message": "itinerary_id and user_id are required"}), 400

    _, (status, message, code) = service.add_favorite(user_id, itinerary_id)
    return jsonify({"status": status, "message": message}), code


@user_bp.route("/favorite", methods=["GET"])
def get_favorites():
    """Get favorite itineraries by user_id
    ---
    tags:
      - User
    parameters:
      - in: query
        name: user_id
        type: string
        required: true
        description: Device ID or user ID
    responses:
      200:
        description: List of favorite itineraries
      400:
        description: user_id is required
    """
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "user_id query param is required"}), 400

    favorites, (status, message, code) = service.get_favorites(user_id)
    if favorites is not None:
        return jsonify({
            "status": status,
            "favorites": favorites,
            "ads": get_inline_ads_config("favorites"),
        }), code
    return jsonify({"status": status, "message": message}), code


@user_bp.route("/favorite", methods=["DELETE"])
def remove_favorite():
    """Remove itinerary from favorites
    ---
    tags:
      - User
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - itinerary_id
            - user_id
          properties:
            itinerary_id:
              type: integer
              example: 1
            user_id:
              type: string
              example: "abc123-device-uuid"
    responses:
      200:
        description: Removed from favorites
      404:
        description: Favorite not found
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    itinerary_id = data.get("itinerary_id")
    user_id = data.get("user_id")
    if not itinerary_id or not user_id:
        return jsonify({"status": "error", "message": "itinerary_id and user_id are required"}), 400

    _, (status, message, code) = service.remove_favorite(user_id, itinerary_id)
    return jsonify({"status": status, "message": message}), code


@user_bp.route("/history", methods=["POST"])
def add_history():
    """Add itinerary to history
    ---
    tags:
      - User
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - itinerary_id
            - user_id
          properties:
            itinerary_id:
              type: integer
              example: 1
            user_id:
              type: string
              description: Device ID (before signup) or user ID (after signup)
              example: "abc123-device-uuid"
    responses:
      201:
        description: Added to history
      404:
        description: Itinerary not found
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    itinerary_id = data.get("itinerary_id")
    user_id = data.get("user_id")
    if not itinerary_id or not user_id:
        return jsonify({"status": "error", "message": "itinerary_id and user_id are required"}), 400

    _, (status, message, code) = service.add_history(user_id, itinerary_id)
    return jsonify({"status": status, "message": message}), code


@user_bp.route("/history", methods=["GET"])
def get_history():
    """Get itinerary history by user_id
    ---
    tags:
      - User
    parameters:
      - in: query
        name: user_id
        type: string
        required: true
        description: Device ID or user ID
    responses:
      200:
        description: List of itinerary history
      400:
        description: user_id is required
    """
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "user_id query param is required"}), 400

    history, (status, message, code) = service.get_history(user_id)
    if history is not None:
        return jsonify({
            "status": status,
            "history": history,
            "ads": get_inline_ads_config("history"),
        }), code
    return jsonify({"status": status, "message": message}), code
