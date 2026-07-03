from flask import Blueprint, request, jsonify

from core.db import is_db_ready
from core.ads import get_inline_ads_config
from auth.guard import current_identity
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
          properties:
            itinerary_id:
              type: integer
              example: 1
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
    user_id = current_identity()
    if not itinerary_id:
        return jsonify({"status": "error", "message": "itinerary_id is required"}), 400
    if not user_id:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    _, (status, message, code) = service.add_favorite(user_id, itinerary_id)
    return jsonify({"status": status, "message": message}), code


@user_bp.route("/favorite", methods=["GET"])
def get_favorites():
    """Get favorite itineraries for the authenticated caller
    ---
    tags:
      - User
    responses:
      200:
        description: List of favorite itineraries
      401:
        description: Authentication required
    """
    user_id = current_identity()
    if not user_id:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

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
          properties:
            itinerary_id:
              type: integer
              example: 1
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
    user_id = current_identity()
    if not itinerary_id:
        return jsonify({"status": "error", "message": "itinerary_id is required"}), 400
    if not user_id:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

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
          properties:
            itinerary_id:
              type: integer
              example: 1
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
    user_id = current_identity()
    if not itinerary_id:
        return jsonify({"status": "error", "message": "itinerary_id is required"}), 400
    if not user_id:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    _, (status, message, code) = service.add_history(user_id, itinerary_id)
    return jsonify({"status": status, "message": message}), code


@user_bp.route("/history", methods=["GET"])
def get_history():
    """Get itinerary history for the authenticated caller
    ---
    tags:
      - User
    responses:
      200:
        description: List of itinerary history
      401:
        description: Authentication required
    """
    user_id = current_identity()
    if not user_id:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    history, (status, message, code) = service.get_history(user_id)
    if history is not None:
        return jsonify({
            "status": status,
            "history": history,
            "ads": get_inline_ads_config("history"),
        }), code
    return jsonify({"status": status, "message": message}), code
