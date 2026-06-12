import traceback

from flask import Blueprint, request, jsonify

import features.places.service as places_service
from features.itinerary.service import is_initialized, loading_response

places_bp = Blueprint("places", __name__)


@places_bp.route("/places", methods=["GET"])
def get_places():
    """Get places by type: popular, nearby, trending or weekend
    ---
    tags:
      - Places
    parameters:
      - in: query
        name: type
        type: string
        required: true
        enum: [popular, nearby, trending, weekend]
        description: Type of places to fetch
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
        description: Returns 10 places of the requested type
      400:
        description: Invalid type or missing params
      503:
        description: Service still loading
    """
    if not is_initialized():
        return loading_response()

    place_type = request.args.get("type")
    if not place_type or place_type not in ("popular", "nearby", "trending", "weekend"):
        return jsonify({"status": "error", "message": "type query param is required (popular/nearby/trending/weekend)"}), 400

    lat = request.args.get("lat", type=float)
    long = request.args.get("long", type=float)

    if place_type in ("nearby", "weekend") and (lat is None or long is None):
        return jsonify({"status": "error", "message": "lat and long are required for nearby/weekend"}), 400

    try:
        if place_type == "popular":
            result = places_service.query_popular()
            return jsonify({"status": "success", "places": result}), 200

        if place_type == "trending":
            result = places_service.query_trending()
            return jsonify({"status": "success", "places": result}), 200

        if place_type == "nearby":
            result = places_service.query_nearby(lat, long)
            return jsonify({"status": "success", "places": result}), 200

        result = places_service.query_weekend(lat, long)
        return jsonify({"status": "success", "places": result}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
