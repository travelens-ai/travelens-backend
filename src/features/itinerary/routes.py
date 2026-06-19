import json

from flask import Blueprint, request, jsonify

import features.itinerary.service as itinerary_service
from core.images import with_image_urls

itinerary_bp = Blueprint("itinerary", __name__)


@itinerary_bp.route("/generate-itinerary", methods=["POST"])
def generate_itinerary():
    """Generate travel itinerary
    ---
    tags:
      - Travel
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
    responses:
      200:
        description: Itinerary generated
      503:
        description: Service still loading
    """
    if not itinerary_service.is_initialized():
        return itinerary_service.loading_response()
    try:
        user_preferences = request.json
        cache_key = json.dumps(user_preferences, sort_keys=True)

        cached_result, cached_id = itinerary_service.get_cached_itinerary(cache_key)
        if cached_result is not None:
            response = with_image_urls(cached_result) if isinstance(cached_result, dict) else cached_result
            if isinstance(response, dict):
                response["itinerary_id"] = cached_id
            return jsonify(response), 200

        result = itinerary_service.recommender.generate_itinerary(user_preferences)
        itinerary_id = itinerary_service.store_itinerary(cache_key, user_preferences, result)

        response = with_image_urls(result) if isinstance(result, dict) else result
        if isinstance(response, dict):
            response["itinerary_id"] = itinerary_id

        return jsonify(response), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@itinerary_bp.route("/edit-itinerary", methods=["POST"])
def edit_itinerary():
    """Regenerate an itinerary that must include a given list of places
    ---
    tags:
      - Travel
    parameters:
      - in: body
        name: body
        required: true
        description: Same payload as /generate-itinerary plus a `places` list of place names that must be included, and an `itinerary_id` of the itinerary to update in place
        schema:
          type: object
    responses:
      200:
        description: Itinerary regenerated with the required places
      503:
        description: Service still loading
    """
    if not itinerary_service.is_initialized():
        return itinerary_service.loading_response()
    try:
        user_preferences = dict(request.json or {})
        # The id of the itinerary being edited — updated in place after a
        # successful edit. Not part of the prompt/cache key.
        existing_id = user_preferences.pop("itinerary_id", None)
        cache_key = "edit:" + json.dumps(user_preferences, sort_keys=True)

        result = itinerary_service.recommender.edit_itinerary(user_preferences)

        # Only persist (and overwrite the existing row) when the edit succeeded.
        if isinstance(result, dict) and result.get("status") == "success":
            itinerary_id = itinerary_service.update_itinerary(
                cache_key, existing_id, user_preferences, result
            )
        else:
            itinerary_id = existing_id

        response = with_image_urls(result) if isinstance(result, dict) else result
        if isinstance(response, dict):
            response["itinerary_id"] = itinerary_id

        return jsonify(response), 200
    except Exception as e:
        print(f"Error editing itinerary: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@itinerary_bp.route("/popular-destination", methods=["GET"])
def get_popular_destination():
    """Get popular destinations
    ---
    tags:
      - Travel
    responses:
      200:
        description: List of popular destinations
      503:
        description: Service still loading
    """
    if not itinerary_service.is_initialized():
        return itinerary_service.loading_response()
    try:
        result = itinerary_service.recommender.get_popular_destination()
        return jsonify(result), 200
    except Exception as e:
        print(f"Error fetching popular destinations: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
