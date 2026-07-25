from flask import Blueprint, request, jsonify

from features.itinerary.service import imageGenerator, is_initialized, loading_response
from models.recommendation.db_persistence import _fetch_image_urls

images_bp = Blueprint("images", __name__)


@images_bp.route("/place-image-urls", methods=["POST"])
def place_image_urls():
    """Fetch external image URLs for a list of places without any DB/CDN side-effects.
    ---
    tags:
      - Images
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            places:
              type: array
              items:
                type: string
              description: List of place name strings to look up images for
    responses:
      200:
        description: Map of place name to fetched image URL (empty string if none found)
      400:
        description: Missing or invalid request body
    """
    body = request.get_json(silent=True) or {}
    places = body.get("places")
    if not isinstance(places, list) or not places:
        return jsonify({"status": "error", "message": "'places' must be a non-empty list"}), 400

    results = {}
    for name in places:
        name = str(name).strip()
        if not name:
            continue
        urls = _fetch_image_urls(name, count=1)
        results[name] = urls[0][0] if urls else ""

    return jsonify({"status": "success", "results": results}), 200


@images_bp.route("/generate-images", methods=["POST"])
def generate_images():
    """Generate images for places
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
        description: Images generated
      503:
        description: Service still loading
    """
    if not is_initialized():
        return loading_response()
    try:
        user_preferences = request.json
        result = imageGenerator.getPlaces(user_preferences)
        return jsonify(result), 200
    except Exception as e:
        print(f"Error generating images: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
