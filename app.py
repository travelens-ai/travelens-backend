from flask import Flask, request, jsonify
from Itenary_recommendation_model_jupiter import ItenaryRecommendationSystem
from generate_images import ImageGenerator
import os
import threading
import multiprocessing as mp
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

api_key = os.getenv('GOOGLE_API_KEY')
recommender = ItenaryRecommendationSystem(api_key=api_key)
imageGenerator = ImageGenerator()
recommender.image_generator = imageGenerator

_initialized = False
_init_error = None

def _do_initialize():
    global _initialized, _init_error
    try:
        recommender.initialize()
        _initialized = True
        print("Background initialization complete.")
    except Exception as e:
        _init_error = str(e)
        print(f"Background initialization failed: {e}")

threading.Thread(target=_do_initialize, daemon=True).start()

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "initialized": _initialized}), 200

def _loading_response():
    msg = _init_error if _init_error else "Service is starting up, please retry in a moment."
    return jsonify({"status": "loading", "message": msg}), 503

@app.route('/generate-itinerary', methods=['POST'])
def generate_itinerary():
    if not _initialized:
        return _loading_response()
    try:
        user_preferences = request.json
        result = recommender.generate_itinerary(user_preferences)
        return jsonify(result), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/popular-destination', methods=['GET'])
def get_popular_destination():
    if not _initialized:
        return _loading_response()
    try:
        result = recommender.get_popular_destination()
        return jsonify(result), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/generate-images', methods=['POST'])
def generate_images():
    if not _initialized:
        return _loading_response()
    try:
        user_preferences = request.json
        result = imageGenerator.getPlaces(user_preferences)
        return jsonify(result), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == '__main__':
    mp.set_start_method("spawn", force=True)
    port = int(os.environ.get('PORT', 4000))
    app.run(host='0.0.0.0', port=port, threaded=True)
