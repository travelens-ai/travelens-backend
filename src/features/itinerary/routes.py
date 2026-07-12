import json
from decimal import Decimal

from flask import Blueprint, request, jsonify, Response, stream_with_context

import features.itinerary.service as itinerary_service
from core.images import with_image_urls
from core.ads import section_ad, get_inline_ads_config

itinerary_bp = Blueprint("itinerary", __name__)


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
    responses:
      200:
        description: Itinerary generated
      503:
        description: Service still loading
    """
    if not itinerary_service.is_initialized():
        return itinerary_service.loading_response()
    try:
        user_preferences = request.json
        cache_key = json.dumps(user_preferences, sort_keys=True)

        cached_result, cached_id = itinerary_service.get_cached_itinerary(cache_key)
        if cached_result is not None:
            response = with_image_urls(cached_result) if isinstance(cached_result, dict) else cached_result
            if isinstance(response, dict):
                response["itinerary_id"] = cached_id
                _fix_total_days(response)
                _inject_itinerary_ads(response)
            return jsonify(response), 200

        result = itinerary_service.recommender.generate_itinerary(user_preferences)
        itinerary_id = itinerary_service.store_itinerary(cache_key, user_preferences, result)

        response = with_image_urls(result) if isinstance(result, dict) else result
        if isinstance(response, dict):
            response["itinerary_id"] = itinerary_id
            _fix_total_days(response)
            _inject_itinerary_ads(response)

        return jsonify(response), 200
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
        places = day.get("places_to_visit") or []
        meals = day.get("meals") or {}
        section_ads = {}
        if places and meals:
            section_ads["after_places"] = section_ad()
        if section_ads:
            day["section_ads"] = section_ads
    # Single ad slot for the trip-level hotels section.
    if itinerary.get("hotels"):
        itinerary["hotel_section_ad"] = section_ad()
    response["ads"] = get_inline_ads_config("itinerary_section")
    return response


def _decompose_response_events(response, itinerary_id, skip_events=None):
    """Yield granular SSE event dicts from a finalized itinerary response, in
    this order:
      1. ads_config   — inline ad slot config
      2. info         — name/title, description, city, state, totals, notes
      3. images       — the main destination image gallery
      4. hotels       — trip-level hotels (one event, all 3 options)
      5. per day:
           place      — each place_to_visit, one per event
           ad         — ad slot between places and meals
           meal       — breakfast/lunch/dinner, one per event (with slot field)
      6. similar_place— each similar place, one per event
      7. done         — terminal marker carrying itinerary_id
    `skip_events` is a set of event names to omit. Images are already
    URL-prefixed by the caller (with_image_urls)."""
    skip_events = skip_events or set()
    data = response.get("data", {}) if isinstance(response, dict) else {}
    itinerary = data.get("detailed_itinerary", {}) or {}

    # 0. Inline ad slot config.
    if "ads_config" not in skip_events:
        yield {"event": "ads_config", "ads": get_inline_ads_config("itinerary_section")}

    # 1. Top-level info.
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

    # 3. Trip-level hotels (one event, emitted once for the whole trip).
    trip_hotels = itinerary.get("hotels", [])
    if trip_hotels:
        yield {"event": "hotels", "hotels": trip_hotels}

    # 4. Day by day: places → ad → meals (breakfast/lunch/dinner).
    for day in itinerary.get("itinerary", []):
        day_no = day.get("day")
        places = day.get("places_to_visit", [])
        meals = day.get("meals", {})

        for place in places:
            yield {"event": "place", "day": day_no, "item": place}
        if places and meals:
            yield {"event": "ad", "day": day_no, "item": section_ad()}

        for slot in ("breakfast", "lunch", "dinner"):
            meal = meals.get(slot)
            if meal:
                yield {"event": "meal", "day": day_no, "slot": slot, "item": meal}

    # 5. Similar places, one per event.
    for similar in itinerary.get("similar_places", []):
        yield {"event": "similar_place", "item": similar}

    # 6. Terminal marker.
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
    responses:
      200:
        description: text/event-stream of progress events then the itinerary, streamed in pieces
      503:
        description: Service still loading
    """
    if not itinerary_service.is_initialized():
        return itinerary_service.loading_response()

    user_preferences = request.json
    cache_key = json.dumps(user_preferences, sort_keys=True)

    def _json_default(o):
        # DB-sourced numbers (lat/lon, rating, cost) may be Decimal, which the
        # stdlib JSON encoder can't serialize — coerce to float so a single bad
        # value never aborts the stream mid-way (which would drop later events
        # such as similar_places).
        if isinstance(o, Decimal):
            return float(o)
        return str(o)

    def _sse(payload):
        # Emit a named SSE event (so EventSource.addEventListener('images', ...)
        # etc. fire) plus the JSON data line. The event name is also kept inside
        # the JSON for clients that only read the default `message` event.
        event_name = payload.get("event", "message") if isinstance(payload, dict) else "message"
        return f"event: {event_name}\ndata: {json.dumps(payload, default=_json_default)}\n\n"

    def event_stream():
        try:
            # Serve a cached itinerary instantly when we have one.
            cached_result, cached_id = itinerary_service.get_cached_itinerary(cache_key)
            if cached_result is not None:
                response = with_image_urls(cached_result) if isinstance(cached_result, dict) else cached_result
                for ev in _decompose_response_events(response, cached_id):
                    yield _sse(ev)
                return

            for ev in itinerary_service.recommender.generate_itinerary_stream(user_preferences):
                if ev.get("event") == "complete":
                    result = ev.get("data")
                    itinerary_id = itinerary_service.store_itinerary(cache_key, user_preferences, result)
                    response = with_image_urls(result) if isinstance(result, dict) else result
                    # The image gallery was already streamed early; skip it here
                    # to avoid a duplicate. `info` is re-sent with the LLM's
                    # authoritative description/price once the plan is built.
                    for piece in _decompose_response_events(response, itinerary_id,
                                                            skip_events={"images"}):
                        yield _sse(piece)
                elif ev.get("event") == "images":
                    # Early destination gallery — URL-prefix it like the rest.
                    ev = {**ev, "images": with_image_urls(ev.get("images", []))}
                    yield _sse(ev)
                else:
                    # progress / info / error events pass straight through
                    yield _sse(ev)
        except Exception as e:
            print(f"Error streaming itinerary: {e}")
            yield _sse({"event": "error", "message": str(e)})

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
        cache_key = "edit:" + json.dumps(user_preferences, sort_keys=True)

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

        return jsonify(response), 200
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
        return jsonify(result), 200
    except Exception as e:
        print(f"Error fetching popular destinations: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
