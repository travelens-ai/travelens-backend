import copy
import json
import time
import threading

try:
    from langfuse.openai import AzureOpenAI  # auto-traces embeddings.create
except ImportError:
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
            success = recommender.initialize()
            if success:
                _initialized = True
                print("Background initialization complete.")
            else:
                _init_error = _init_error or "Recommender initialization failed (check server logs for details)"
                print("Background initialization returned False.")
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


def _json_default(o):
    """Coerce DB-sourced Decimals (lat/lon, rating, cost) and other exotic
    types so json.dumps never fails when persisting an itinerary."""
    from decimal import Decimal
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def _prune_result_for_storage(result):
    """Return a deep copy of the itinerary result with bulky/derived parts
    stripped before persisting: `data.detailed_itinerary.similar_places`.
    The caller's `result` (and the in-memory cache) keep the full payload."""
    if not isinstance(result, dict):
        return result
    pruned = copy.deepcopy(result)
    # Token usage is persisted in its own columns, not in the JSON blob.
    pruned.pop("token_usage", None)
    data = pruned.get("data")
    if isinstance(data, dict):
        detailed = data.get("detailed_itinerary")
        if isinstance(detailed, dict):
            detailed.pop("similar_places", None)
            detailed.pop("available_places", None)
    return pruned


def _extract_token_usage(result):
    """Pull (input_token, output_token) out of a generation result. The model
    attaches `token_usage` at the result top level; returns (None, None) when
    it's absent (e.g. a cached/edited result without usage)."""
    usage = result.get("token_usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return None, None
    return usage.get("input_token"), usage.get("output_token")


def store_itinerary(cache_key, user_preferences, result):
    itinerary_id = None
    input_token, output_token = _extract_token_usage(result)
    if is_db_ready():
        conn = get_connection()
        try:
            cursor = conn.cursor()
            # OUTPUT INSERTED.id returns the new identity as part of the INSERT's
            # own result set. This is immune to the scope/batch pitfall that makes
            # a separate `SELECT SCOPE_IDENTITY()` return NULL (it runs in a
            # different batch scope), which was leaving itinerary_id null.
            cursor.execute(
                "INSERT INTO itineraries (request_json, response_json, status, input_token, output_token) "
                "OUTPUT INSERTED.id VALUES (?, ?, ?, ?, ?)",
                (json.dumps(user_preferences, default=_json_default), json.dumps(_prune_result_for_storage(result), default=_json_default), "success", input_token, output_token),
            )
            itinerary_id = int(cursor.fetchone()[0])
            conn.commit()
            cursor.close()
        except Exception as db_err:
            print(f"Failed to store itinerary: {db_err}")
        finally:
            conn.close()
    _itinerary_cache[cache_key] = (time.time(), result, itinerary_id)
    return itinerary_id


def get_session_id(itinerary_id):
    """Return the _session_id stored with the original generate call for this itinerary.
    Used by /edit so all edits share the same Langfuse session as the original trip."""
    if not itinerary_id:
        return None
    if is_db_ready():
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT request_json FROM itineraries WHERE id = ?", (itinerary_id,))
            row = cursor.fetchone()
            cursor.close()
            if row and row[0]:
                prefs = json.loads(row[0])
                return prefs.get('_session_id')
        except Exception:
            pass
        finally:
            conn.close()
    # Fall back to in-memory cache: scan for an entry whose stored id matches.
    for _key, (_, _result, stored_id) in _itinerary_cache.items():
        if stored_id == itinerary_id:
            return None  # we don't store prefs separately in the cache tuple
    return None


def share_itinerary(*, receiver_user_id, receiver_device_id, itinerary_id):
    """Record an itinerary shared with a receiver.

    A share is identified only by its receiver + itinerary_id (no sender). The
    receiver is a logged-in user (receiver_user_id, optional) OR an anonymous
    device (receiver_device_id); at least one must be given. Validates that the
    itinerary exists. Returns ({...row}, (status, msg, code))."""
    if not itinerary_id:
        return None, ("error", "itinerary_id is required", 400)
    if receiver_user_id is None and not receiver_device_id:
        return None, ("error", "receiver_user_id or receiver_device_id is required", 400)
    if not is_db_ready():
        return None, ("error", "Database is connecting, please try again shortly", 503)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM itineraries WHERE id = ?", (itinerary_id,))
        if cursor.fetchone() is None:
            return None, ("error", "Itinerary not found", 404)

        cursor.execute(
            "INSERT INTO shared_itineraries "
            "(receiver_user_id, receiver_device_id, itinerary_id) "
            "OUTPUT INSERTED.id VALUES (?, ?, ?)",
            (receiver_user_id, receiver_device_id, itinerary_id),
        )
        new_id = int(cursor.fetchone()[0])
        conn.commit()
        return (
            {
                "id": new_id,
                "receiver_user_id": receiver_user_id,
                "receiver_device_id": receiver_device_id,
                "itinerary_id": itinerary_id,
            },
            ("success", "Shared", 201),
        )
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def get_shared_itineraries(*, receiver_user_id, receiver_device_id):
    """Itineraries shared *with* the given receiver, joined to the itinerary row.

    Matches on whichever receiver identity the caller has (user id for a
    logged-in user, else device id). Each entry carries the share metadata plus
    the parsed itinerary request/response JSON. Returns (list, (status,...))."""
    if receiver_user_id is None and not receiver_device_id:
        return None, ("error", "No receiver identity on the request", 401)
    if not is_db_ready():
        return None, ("error", "Database is connecting, please try again shortly", 503)

    if receiver_user_id is not None:
        where, param = "s.receiver_user_id = ?", receiver_user_id
    else:
        where, param = "s.receiver_device_id = ?", receiver_device_id

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT s.id, "
            "s.receiver_user_id, s.receiver_device_id, s.itinerary_id, s.created_at, "
            "i.request_json, i.response_json, i.status, i.created_at AS itinerary_created_at "
            "FROM shared_itineraries s "
            "LEFT JOIN itineraries i ON s.itinerary_id = i.id "
            f"WHERE {where} "
            "ORDER BY s.id DESC",
            (param,),
        )
        rows = cursor.fetchall()
        data = []
        for r in rows:
            (sid, r_uid, r_did, itin_id, created_at,
             req_json, resp_json, itin_status, itin_created) = r
            data.append({
                "id": sid,
                "receiver_user_id": r_uid,
                "receiver_device_id": r_did,
                "itinerary_id": itin_id,
                "created_at": created_at.isoformat() if created_at else None,
                "itinerary": {
                    "id": itin_id,
                    "status": itin_status,
                    "request_json": json.loads(req_json) if req_json else None,
                    "response_json": json.loads(resp_json) if resp_json else None,
                    "created_at": itin_created.isoformat() if itin_created else None,
                } if req_json is not None or resp_json is not None else None,
            })
        return data, ("success", "OK", 200)
    except Exception as e:
        print(f"Failed to fetch shared itineraries: {e}")
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def get_itinerary_by_id(itinerary_id):
    """Return (request_json_dict, response_json_dict) for a stored itinerary, or (None, None)."""
    if not itinerary_id or not is_db_ready():
        return None, None
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT request_json, response_json FROM itineraries WHERE id = ?",
            (itinerary_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return None, None
        return json.loads(row[0] or '{}'), json.loads(row[1] or '{}')
    except Exception as e:
        print(f"Failed to fetch itinerary {itinerary_id}: {e}")
        return None, None
    finally:
        conn.close()


def update_itinerary(cache_key, itinerary_id, user_preferences, result):
    """Overwrite an existing itinerary row's request/response JSON after a
    successful edit. Returns the itinerary_id if the row was updated, else None
    (e.g. the id doesn't exist). Falls back to inserting a new row when no id is
    provided."""
    if not itinerary_id:
        return store_itinerary(cache_key, user_preferences, result)

    updated = False
    input_token, output_token = _extract_token_usage(result)
    if is_db_ready():
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE itineraries SET request_json = ?, response_json = ?, status = ?, input_token = ?, output_token = ? WHERE id = ?",
                (json.dumps(user_preferences, default=_json_default), json.dumps(_prune_result_for_storage(result), default=_json_default), "success", input_token, output_token, itinerary_id),
            )
            conn.commit()
            updated = cursor.rowcount > 0
            cursor.close()
        except Exception as db_err:
            print(f"Failed to update itinerary {itinerary_id}: {db_err}")
        finally:
            conn.close()

    if not updated:
        # Row not found (or DB write failed) — fall back to a fresh insert so
        # the edit isn't lost, and return the new id.
        return store_itinerary(cache_key, user_preferences, result)

    _itinerary_cache[cache_key] = (time.time(), result, itinerary_id)
    return itinerary_id
