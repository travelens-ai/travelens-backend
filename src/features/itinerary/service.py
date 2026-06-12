import json
import time
import threading

from openai import AzureOpenAI

from core.config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_CHAT_DEPLOYMENT,
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
)
from core.db import get_connection, is_db_ready
from models.Itenary_recommendation_model_jupiter import ItenaryRecommendationSystem
from integrations.generate_images import ImageGenerator

_client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_version=AZURE_OPENAI_API_VERSION,
)

recommender = ItenaryRecommendationSystem(_client, AZURE_OPENAI_CHAT_DEPLOYMENT, AZURE_OPENAI_EMBEDDING_DEPLOYMENT)
imageGenerator = ImageGenerator()
recommender.image_generator = imageGenerator

_itinerary_cache = {}
_initialized = False
_init_error = None


def initialize_recommender():
    global _initialized, _init_error

    def _do_init():
        global _initialized, _init_error
        try:
            recommender.initialize()
            _initialized = True
            print("Background initialization complete.")
        except Exception as e:
            _init_error = str(e)
            print(f"Background initialization failed: {e}")

    threading.Thread(target=_do_init, daemon=True).start()


def is_initialized():
    return _initialized


def get_init_error():
    return _init_error


def loading_response():
    from flask import jsonify
    msg = _init_error if _init_error else "Service is starting up, please retry in a moment."
    return jsonify({"status": "loading", "message": msg}), 503


def get_cached_itinerary(cache_key):
    cached = _itinerary_cache.get(cache_key)
    if not cached:
        return None, None
    cache_time, cache_result, cache_id = cached
    if (time.time() - cache_time) < 86400:
        return cache_result, cache_id
    del _itinerary_cache[cache_key]
    return None, None


def store_itinerary(cache_key, user_preferences, result):
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
    _itinerary_cache[cache_key] = (time.time(), result, itinerary_id)
    return itinerary_id
