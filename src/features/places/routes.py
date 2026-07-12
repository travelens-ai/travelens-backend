import traceback

from flask import Blueprint, request, jsonify

import features.places.service as places_service
from features.itinerary.service import is_initialized, loading_response
from core.images import with_image_urls

places_bp = Blueprint("places", __name__)


@places_bp.route("/places", methods=["GET"])
def get_places():
    """Get places by keyword prefix, or by type: popular, nearby, trending or weekend
    ---
    tags:
      - Places
    parameters:
      - in: query
        name: keyword
        type: string
        description: Search places whose name starts with this keyword (min 3 characters)
      - in: query
        name: type
        type: string
        enum: [popular, nearby, trending, weekend]
        description: Type of places to fetch (used when keyword is not provided)
      - in: query
        name: lat
        type: number
        description: User latitude (required for nearby and weekend)
      - in: query
        name: long
        type: number
        description: User longitude (required for nearby and weekend)
    responses:
      200:
        description: Returns matching places
      400:
        description: Invalid type, keyword too short, or missing params
      503:
        description: Service still loading
    """
    if not is_initialized():
        return loading_response()

    keyword = request.args.get("keyword")
    if keyword is not None:
        keyword = keyword.strip()
        if len(keyword) < 3:
            return jsonify({"status": "error", "message": "keyword must be at least 3 characters"}), 400
        try:
            result = places_service.query_by_keyword(keyword)
            return jsonify({"status": "success", "places": result}), 200
        except Exception as e:
            traceback.print_exc()
            return jsonify({"status": "error", "message": str(e)}), 500

    place_type = request.args.get("type")
    if not place_type or place_type not in ("popular", "nearby", "trending", "weekend"):
        return jsonify({"status": "error", "message": "type query param is required (popular/nearby/trending/weekend)"}), 400

    lat = request.args.get("lat", type=float)
    long = request.args.get("long", type=float)

    if place_type in ("nearby", "weekend") and (lat is None or long is None):
        return jsonify({"status": "error", "message": "lat and long are required for nearby/weekend"}), 400

    try:
        if place_type == "popular":
            result = places_service.query_popular(lat=lat, lon=long)
            return jsonify({"status": "success", "places": with_image_urls(result)}), 200

        if place_type == "trending":
            result = places_service.query_trending(lat=lat, lon=long)
            return jsonify({"status": "success", "places": with_image_urls(result)}), 200

        if place_type == "nearby":
            result = places_service.query_nearby(lat, long)
            return jsonify({"status": "success", "places": with_image_urls(result)}), 200

        result = places_service.query_weekend(lat, long)
        return jsonify({"status": "success", "places": with_image_urls(result)}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
