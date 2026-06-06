from flask import Flask, request, jsonify
from Itenary_recommendation_model_jupiter import ItenaryRecommendationSystem
from generate_images import ImageGenerator
import os
import time
import threading
import multiprocessing as mp
import requests as http_requests
from flask_cors import CORS
from dotenv import load_dotenv
from openai import AzureOpenAI
from flasgger import Swagger
import math
import pandas as pd

import socket

load_dotenv()

app = Flask(__name__)
CORS(app)


try:
    _server_ip = socket.gethostbyname(socket.gethostname())
except Exception:
    _server_ip = "unknown"


@app.after_request
def add_server_ip(response):
    if response.content_type and "application/json" in response.content_type:
        try:
            data = response.get_json(silent=True)
            if isinstance(data, dict):
                data["server_ip"] = _server_ip
                response.data = jsonify(data).data
        except Exception:
            pass
    return response

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



# City coordinates cache for nearby calculation
_city_coords_cache = {}
_city_coords_loaded = False


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _load_city_coords():
    global _city_coords_cache, _city_coords_loaded
    import pickle
    cache_file = 'city_coords.pkl'
    if os.path.exists(cache_file):
        with open(cache_file, 'rb') as f:
            _city_coords_cache = pickle.load(f)
        _city_coords_loaded = True
        print(f"Loaded {len(_city_coords_cache)} city coordinates from cache.")
        return

    places_df = pd.read_csv('indian_travel_places.csv')
    cities = places_df['city'].unique()
    for city in cities:
        try:
            resp = http_requests.get(
                f"https://nominatim.openstreetmap.org/search?q={city},India&format=json&limit=1",
                headers={"User-Agent": "Travelens/1.0"},
                timeout=5,
            )
            if resp.status_code == 200 and resp.json():
                data = resp.json()[0]
                _city_coords_cache[city] = (float(data["lat"]), float(data["lon"]))
            time.sleep(1)
        except Exception:
            pass

    with open(cache_file, 'wb') as f:
        pickle.dump(_city_coords_cache, f)
    _city_coords_loaded = True
    print(f"Geocoded and cached {len(_city_coords_cache)} cities.")


threading.Thread(target=_load_city_coords, daemon=True).start()


@app.route('/places', methods=['GET'])
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
    if not _initialized:
        return _loading_response()

    place_type = request.args.get("type")
    if not place_type or place_type not in ("popular", "nearby", "trending", "weekend"):
        return jsonify({"status": "error", "message": "type query param is required (popular/nearby/trending/weekend)"}), 400

    lat = request.args.get("lat", type=float)
    long = request.args.get("long", type=float)

    if place_type in ("nearby", "weekend") and (lat is None or long is None):
        return jsonify({"status": "error", "message": "lat and long are required for nearby/weekend"}), 400

    try:
        if place_type == "popular":
            result = recommender.get_popular_destination() or []
            return jsonify({"status": "success", "places": result}), 200

        if place_type == "trending":
            places_df = pd.read_csv('indian_travel_places.csv')
            places_df['no of rating'] = pd.to_numeric(places_df['no of rating'], errors='coerce').fillna(0)
            trending_df = places_df.nlargest(10, 'no of rating')
            result = trending_df.fillna('').to_dict(orient='records')
            return jsonify({"status": "success", "places": result}), 200

        # For nearby and weekend, calculate distances
        places_df = pd.read_csv('indian_travel_places.csv')
        places_df['rating'] = pd.to_numeric(places_df['rating'], errors='coerce').fillna(0)

        nearby_with_dist = []
        for _, row in places_df.iterrows():
            city = row['city']
            if city in _city_coords_cache:
                dist = _haversine(lat, long, _city_coords_cache[city][0], _city_coords_cache[city][1])
                nearby_with_dist.append((dist, row.fillna('').to_dict()))

        nearby_with_dist.sort(key=lambda x: x[0])

        if place_type == "nearby":
            result = [place for _, place in nearby_with_dist[:10]]
            return jsonify({"status": "success", "places": result}), 200

        # Weekend: places within 300km, sorted by rating (2 days 1 night)
        weekend_candidates = [(d, p) for d, p in nearby_with_dist if d <= 300]
        weekend_candidates.sort(key=lambda x: float(x[1].get("rating", 0) or 0), reverse=True)
        result = [place for _, place in weekend_candidates[:10]]
        return jsonify({"status": "success", "places": result}), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    mp.set_start_method("spawn", force=True)
    port = int(os.environ.get('PORT', 4000))
    app.run(host='0.0.0.0', port=port, threaded=True)
