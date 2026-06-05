from flask import Flask, request, jsonify
from Itenary_recommendation_model_jupiter import ItenaryRecommendationSystem
from generate_images import ImageGenerator
import os
import time
import threading
import multiprocessing as mp
from flask_cors import CORS
from dotenv import load_dotenv
from openai import AzureOpenAI
from flasgger import Swagger

load_dotenv()

app = Flask(__name__)
CORS(app)

from swagger_config import swagger_template, swagger_config
Swagger(app, template=swagger_template, config=swagger_config)

from auth import auth_bp
from db import init_db_async, get_connection, is_db_ready

app.register_blueprint(auth_bp)

init_db_async()

_itinerary_cache = {}

client = AzureOpenAI(
    api_key=os.getenv('AZURE_OPENAI_API_KEY'),
    azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
    api_version=os.getenv('AZURE_OPENAI_API_VERSION', '2024-12-01-preview'),
)

chat_deployment = os.getenv('AZURE_OPENAI_CHAT_DEPLOYMENT')
embedding_deployment = os.getenv('AZURE_OPENAI_EMBEDDING_DEPLOYMENT')

recommender = ItenaryRecommendationSystem(client, chat_deployment, embedding_deployment)
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
    """Health check
    ---
    tags:
      - System
    responses:
      200:
        description: Service is healthy
    """
    return jsonify({"status": "healthy", "initialized": _initialized}), 200

def _loading_response():
    msg = _init_error if _init_error else "Service is starting up, please retry in a moment."
    return jsonify({"status": "loading", "message": msg}), 503

@app.route('/generate-itinerary', methods=['POST'])
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
    if not _initialized:
        return _loading_response()
    try:
        import json
        user_preferences = request.json
        cache_key = json.dumps(user_preferences, sort_keys=True)

        # Check local cache
        cached = _itinerary_cache.get(cache_key)
        if cached:
            cache_time, cache_result, cache_id = cached
            if (time.time() - cache_time) < 86400:
                response = cache_result.copy() if isinstance(cache_result, dict) else cache_result
                if isinstance(response, dict):
                    response["itinerary_id"] = cache_id
                return jsonify(response), 200
            else:
                del _itinerary_cache[cache_key]

        result = recommender.generate_itinerary(user_preferences)

        # Store in DB and get itinerary_id
        itinerary_id = None
        if is_db_ready():
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO itineraries (request_json, response_json, status) VALUES (%s, %s, %s)",
                    (json.dumps(user_preferences), json.dumps(result), "success"),
                )
                conn.commit()
                itinerary_id = cursor.lastrowid
                cursor.close()
                conn.close()
            except Exception as db_err:
                print(f"Failed to store itinerary: {db_err}")

        # Store in local cache
        _itinerary_cache[cache_key] = (time.time(), result, itinerary_id)

        if isinstance(result, dict):
            result["itinerary_id"] = itinerary_id

        return jsonify(result), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/popular-destination', methods=['GET'])
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
