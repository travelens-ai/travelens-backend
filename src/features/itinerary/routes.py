import json
import math
import uuid
from decimal import Decimal

from flask import Blueprint, request, jsonify, Response, stream_with_context

import features.itinerary.service as itinerary_service
from core.images import with_image_urls
from core.ads import section_ad, get_inline_ads_config

itinerary_bp = Blueprint("itinerary", __name__)


def _sanitize_nan(obj):
    """Recursively replace NaN float values with None so jsonify emits null, not NaN."""
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nan(v) for v in obj]
    return obj


@itinerary_bp.route("/generate-itinerary", methods=["POST"])
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
          example:
            places_of_interest: "Goa, Goa"
            user_location: "Mumbai"
            trip_duration: 4
            number_of_people: 3
            travel_group_type: "friends"
            food_preferences: "North Indian, Seafood"
            preferred_activities: ["Beach", "Nightlife", "Sightseeing"]
            trip_type: "Beach"
            current_month: "July"
            start_date: "2026-07-15"
            budget: "25000"
            suggested_places: []
            hotel_preference: "mid"
            arrival_time: "06:00"
            departure_time: "15:00"
    responses:
      200:
        description: Itinerary generated
      503:
        description: Service still loading
    """
    if not itinerary_service.is_initialized():
        return itinerary_service.loading_response()
    try:
        user_preferences = dict(request.json or {})
        user_preferences['_user_id'] = getattr(request, 'user_id', None) or getattr(request, 'device_id', None)
        user_preferences['_session_id'] = str(uuid.uuid4())
        cache_key = json.dumps({k: v for k, v in user_preferences.items() if not k.startswith('_')}, sort_keys=True)

        cached_result, cached_id = itinerary_service.get_cached_itinerary(cache_key)
        if cached_result is not None:
            response = with_image_urls(cached_result) if isinstance(cached_result, dict) else cached_result
            if isinstance(response, dict):
                response["itinerary_id"] = cached_id
                _fix_total_days(response)
                _inject_itinerary_ads(response)
            return jsonify(_sanitize_nan(response)), 200

        result = itinerary_service.recommender.generate_itinerary(user_preferences)
        itinerary_id = itinerary_service.store_itinerary(cache_key, user_preferences, result)

        response = with_image_urls(result) if isinstance(result, dict) else result
        if isinstance(response, dict):
            response["itinerary_id"] = itinerary_id
            _fix_total_days(response)
            _inject_itinerary_ads(response)

        return jsonify(_sanitize_nan(response)), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _fix_total_days(response):
    """Ensure total_days is always an integer. The LLM sometimes returns it as a
    string or omits it; fall back to the actual number of day objects."""
    if not isinstance(response, dict):
        return response
    itin = response.get("data", {}).get("detailed_itinerary", {}) or {}
    try:
        itin["total_days"] = int(itin["total_days"])
    except (TypeError, ValueError, KeyError):
        itin["total_days"] = len(itin.get("itinerary") or [])
    return response


def _inject_itinerary_ads(response):
    """Add ad slots between places/meals sections of each day, and a single ad
    slot for the trip-level hotels section. Mutates and returns the response."""
    if not isinstance(response, dict):
        return response
    itinerary = response.get("data", {}).get("detailed_itinerary", {}) or {}
    for day in itinerary.get("itinerary", []):
        timeline = day.get("timeline") or []
        has_places = any(i.get("type") == "place" for i in timeline)
        has_meals  = any(i.get("type") == "meal"  for i in timeline)
        section_ads = {}
        if has_places and has_meals:
            section_ads["after_places"] = section_ad()
        if section_ads:
            day["section_ads"] = section_ads
    # Single ad slot for the trip-level hotels section.
    if itinerary.get("hotels"):
        itinerary["hotel_section_ad"] = section_ad()
    response["ads"] = get_inline_ads_config("itinerary_section")
    return response


def _decompose_response_events(response, itinerary_id, skip_events=None):
    """Yield granular SSE events from a FINALIZED itinerary — used to replay a
    cached itinerary so it looks identical to a fresh incremental stream. Order:
      1. info           — name/description/city/state/price/total_days/notes
      2. images         — the main destination image gallery
      3. hotels         — trip-level grouped hotels (one event)
      4. similar_place  — one per similar place
      5. per day (1, 2, ...):
           day_info     — the day's theme + day_summary
           timeline_item— one per timeline item (place/meal/hotel)
           ad           — section ad after a day with both places and meals
           meal_options — swappable restaurant alternatives per slot
      6. available_places — not-yet-used places (one event)
      7. done           — terminal marker carrying itinerary_id
    Images are already URL-prefixed by the caller (with_image_urls)."""
    skip_events = skip_events or set()
    data = response.get("data", {}) if isinstance(response, dict) else {}
    itinerary = data.get("detailed_itinerary", {}) or {}

    # 1. Trip info.
    if "info" not in skip_events:
        yield {
            "event": "info",
            "name": itinerary.get("name", ""),
            "description": itinerary.get("description", ""),
            "city": itinerary.get("city", ""),
            "state": itinerary.get("state", ""),
            "price_estimated_range": itinerary.get("price_estimated_range", ""),
            "total_days": itinerary.get("total_days"),
            "notes": itinerary.get("notes", ""),
        }

    # 2. Main images gallery.
    if "images" not in skip_events:
        yield {"event": "images", "images": itinerary.get("images", [])}

    # 3. Trip-level hotels (grouped format: city/selected/alternatives).
    trip_hotels = itinerary.get("hotels", [])
    if trip_hotels:
        yield {"event": "hotels", "hotels": trip_hotels}

    # 4. Similar places, one per event.
    for similar in itinerary.get("similar_places", []):
        yield {"event": "similar_place", "item": similar}

    # 5. Day by day: day_info → timeline items → ad → meal_options.
    for day in itinerary.get("itinerary", []):
        day_no = day.get("day")
        timeline = day.get("timeline", []) or []
        has_places = any(i.get("type") == "place" for i in timeline)
        has_meals  = any(i.get("type") == "meal"  for i in timeline)

        yield {
            "event": "day_info",
            "day": day_no,
            "date": day.get("date", ""),
            "weekday": day.get("weekday", ""),
            "theme": day.get("theme", ""),
            "day_summary": day.get("day_summary", ""),
        }

        for item in timeline:
            yield {"event": "timeline_item", "day": day_no, "item": item}

        if has_places and has_meals:
            yield {"event": "ad", "day": day_no, "item": section_ad()}

        meal_options = day.get("meal_options", {})
        if meal_options:
            yield {"event": "meal_options", "day": day_no, "options": meal_options}

    # 6. Available (not-yet-used) places the client can add to the trip.
    if "available_places" not in skip_events:
        yield {"event": "available_places", "available_places": itinerary.get("available_places", [])}

    # 7. Terminal marker.
    yield {"event": "done", "itinerary_id": itinerary_id}


@itinerary_bp.route("/generate-itinerary/stream", methods=["POST"])
def generate_itinerary_stream():
    """Generate a travel itinerary as a Server-Sent Events (SSE) stream.

    Emits `progress` events while the itinerary is being built, then streams the
    result piece by piece: `images`, `info`, then `place`/`restaurant`/`hotel`
    events day by day, then `similar_place` events, and finally a
    `done` event with the itinerary_id. On failure, emits an `error` event.
    ---
    tags:
      - Travel
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          example:
            places_of_interest: "Manali, Himachal Pradesh"
            user_location: "Delhi"
            trip_duration: 5
            number_of_people: 2
            travel_group_type: "couple"
            food_preferences: "North Indian"
            preferred_activities: ["Trekking", "Snow"]
            trip_type: "Mountain"
            current_month: "July"
            start_date: "2026-07-20"
            budget: "30000"
            suggested_places: []
            hotel_preference: "mid"
            arrival_time: "10:00"
            departure_time: "16:00"
    responses:
      200:
        description: text/event-stream of progress events then the itinerary, streamed in pieces
      503:
        description: Service still loading
    """
    if not itinerary_service.is_initialized():
        return itinerary_service.loading_response()

    user_preferences = dict(request.json or {})
    user_preferences['_user_id'] = getattr(request, 'user_id', None) or getattr(request, 'device_id', None)
    user_preferences['_session_id'] = str(uuid.uuid4())
    cache_key = json.dumps({k: v for k, v in user_preferences.items() if not k.startswith('_')}, sort_keys=True)

    def _json_default(o):
        # DB-sourced numbers (lat/lon, rating, cost) may be Decimal, which the
        # stdlib JSON encoder can't serialize — coerce to float so a single bad
        # value never aborts the stream mid-way (which would drop later events
        # such as similar_places).
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, float) and math.isnan(o):
            return None
        return str(o)

    def _sse(payload):
        # Emit a named SSE event (so EventSource.addEventListener('images', ...)
        # etc. fire) plus the JSON data line. The event name is also kept inside
        # the JSON for clients that only read the default `message` event.
        event_name = payload.get("event", "message") if isinstance(payload, dict) else "message"
        return f"event: {event_name}\ndata: {json.dumps(payload, default=_json_default)}\n\n"

    def _map_event(ev):
        """Turn a raw model event into the wire string, or None to drop it."""
        event = ev.get("event")
        if event == "complete":
            # Days were already streamed incrementally. Persist the full
            # result and emit the terminal `done` marker with the id.
            result = ev.get("data")
            itinerary_id = itinerary_service.store_itinerary(cache_key, user_preferences, result)
            return _sse({"event": "done", "itinerary_id": itinerary_id})
        if event == "ad_slot":
            # Model signals an ad slot; the route owns ad config.
            return _sse({"event": "ad", "day": ev.get("day"), "item": section_ad()})
        if event in ("progress", "error"):
            return _sse(ev)
        # info / images / hotels / similar_place / day_info / place / meal /
        # available_places — URL-prefix any bare image names.
        return _sse(with_image_urls(ev))

    # Heartbeat interval (seconds). Azure App Service's front-end load balancer
    # (ARR) drops any connection idle for ~230s — a limit that CANNOT be raised.
    # Each per-day LLM call runs silently for many seconds, so without traffic
    # in between the stream is killed mid-way (client sees only info/hotels +
    # 1-2 days). We run the generator in a worker thread and emit an SSE comment
    # (": keepalive") whenever no real event has arrived within this window, so
    # bytes keep flowing and the idle timer never trips.
    HEARTBEAT_SECS = 15

    def event_stream():
        import queue as _queue
        import threading as _threading

        # Cached itinerary: replay instantly, no long LLM calls, no heartbeat.
        cached_result, cached_id = itinerary_service.get_cached_itinerary(cache_key)
        if cached_result is not None:
            response = with_image_urls(cached_result) if isinstance(cached_result, dict) else cached_result
            for ev in _decompose_response_events(response, cached_id):
                yield _sse(ev)
            return

        q = _queue.Queue()
        _DONE = object()

        def _produce():
            try:
                for ev in itinerary_service.recommender.generate_itinerary_stream(user_preferences):
                    q.put(("event", ev))
            except Exception as e:
                print(f"Error streaming itinerary: {e}")
                q.put(("error", str(e)))
            finally:
                q.put(("done", _DONE))

        worker = _threading.Thread(target=_produce, daemon=True)
        worker.start()

        while True:
            try:
                kind, payload = q.get(timeout=HEARTBEAT_SECS)
            except _queue.Empty:
                # No event within the window — send a heartbeat comment. SSE
                # comment lines (starting with ':') are ignored by clients.
                yield ": keepalive\n\n"
                continue
            if kind == "done":
                break
            if kind == "error":
                yield _sse({"event": "error", "message": payload})
                continue
            # kind == "event"
            try:
                line = _map_event(payload)
            except Exception as e:
                print(f"Error mapping itinerary event: {e}")
                yield _sse({"event": "error", "message": str(e)})
                continue
            if line:
                yield line

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx)
            "Connection": "keep-alive",
        },
    )


@itinerary_bp.route("/edit-itinerary", methods=["POST"])
def edit_itinerary():
    """Regenerate an itinerary that must include a given list of places
    ---
    tags:
      - Travel
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          example:
            itinerary_id: 1
            places_of_interest: "Goa, Goa"
            user_location: "Mumbai"
            trip_duration: 4
            number_of_people: 3
            travel_group_type: "friends"
            food_preferences: "North Indian, Seafood"
            preferred_activities: ["Beach", "Sightseeing"]
            trip_type: "Beach"
            current_month: "July"
            suggested_places: ["Baga Beach", "Dudhsagar Falls"]
            budget: "25000"
            start_date: "2026-07-15"
            hotel_preference: "luxury"
    responses:
      200:
        description: Itinerary regenerated with the required places
      503:
        description: Service still loading
    """
    if not itinerary_service.is_initialized():
        return itinerary_service.loading_response()
    try:
        user_preferences = dict(request.json or {})
        # The id of the itinerary being edited — updated in place after a
        # successful edit. Not part of the prompt/cache key.
        existing_id = user_preferences.pop("itinerary_id", None)
        user_preferences['_user_id'] = getattr(request, 'user_id', None) or getattr(request, 'device_id', None)
        user_preferences['_session_id'] = itinerary_service.get_session_id(existing_id) or str(uuid.uuid4())
        cache_key = "edit:" + json.dumps({k: v for k, v in user_preferences.items() if not k.startswith('_')}, sort_keys=True)

        result = itinerary_service.recommender.edit_itinerary(user_preferences)

        # Only persist (and overwrite the existing row) when the edit succeeded.
        if isinstance(result, dict) and result.get("status") == "success":
            itinerary_id = itinerary_service.update_itinerary(
                cache_key, existing_id, user_preferences, result
            )
        else:
            itinerary_id = existing_id

        response = with_image_urls(result) if isinstance(result, dict) else result
        if isinstance(response, dict):
            response["itinerary_id"] = itinerary_id
            _fix_total_days(response)
            _inject_itinerary_ads(response)

        return jsonify(_sanitize_nan(response)), 200
    except Exception as e:
        print(f"Error editing itinerary: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@itinerary_bp.route("/popular-destination", methods=["GET"])
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
    if not itinerary_service.is_initialized():
        return itinerary_service.loading_response()
    try:
        result = itinerary_service.recommender.get_popular_destination()
        return jsonify(_sanitize_nan(with_image_urls(result))), 200
    except Exception as e:
        print(f"Error fetching popular destinations: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
