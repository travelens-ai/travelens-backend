from flask import Blueprint, request, jsonify

from features.itinerary.service import imageGenerator, is_initialized, loading_response

images_bp = Blueprint("images", __name__)


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
