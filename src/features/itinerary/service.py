import copy
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


def _prune_result_for_storage(result):
    """Return a deep copy of the itinerary result with the bulky/derived parts
    stripped out before persisting: `data.places` and
    `data.detailed_itinerary.similar_places`. The caller's `result` (and the
    in-memory cache) keep the full payload — only the stored copy is trimmed."""
    if not isinstance(result, dict):
        return result
    pruned = copy.deepcopy(result)
    data = pruned.get("data")
    if isinstance(data, dict):
        data.pop("places", None)
        detailed = data.get("detailed_itinerary")
        if isinstance(detailed, dict):
            detailed.pop("similar_places", None)
    return pruned


def store_itinerary(cache_key, user_preferences, result):
    itinerary_id = None
    if is_db_ready():
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO itineraries (request_json, response_json, status) VALUES (%s, %s, %s)",
                (json.dumps(user_preferences), json.dumps(_prune_result_for_storage(result)), "success"),
            )
            conn.commit()
            itinerary_id = cursor.lastrowid
            cursor.close()
        except Exception as db_err:
            print(f"Failed to store itinerary: {db_err}")
    _itinerary_cache[cache_key] = (time.time(), result, itinerary_id)
    return itinerary_id


def update_itinerary(cache_key, itinerary_id, user_preferences, result):
    """Overwrite an existing itinerary row's request/response JSON after a
    successful edit. Returns the itinerary_id if the row was updated, else None
    (e.g. the id doesn't exist). Falls back to inserting a new row when no id is
    provided."""
    if not itinerary_id:
        return store_itinerary(cache_key, user_preferences, result)

    updated = False
    if is_db_ready():
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE itineraries SET request_json = %s, response_json = %s, status = %s WHERE id = %s",
                (json.dumps(user_preferences), json.dumps(_prune_result_for_storage(result)), "success", itinerary_id),
            )
            conn.commit()
            updated = cursor.rowcount > 0
            cursor.close()
        except Exception as db_err:
            print(f"Failed to update itinerary {itinerary_id}: {db_err}")

    if not updated:
        # Row not found (or DB write failed) — fall back to a fresh insert so
        # the edit isn't lost, and return the new id.
        return store_itinerary(cache_key, user_preferences, result)

    _itinerary_cache[cache_key] = (time.time(), result, itinerary_id)
    return itinerary_id
