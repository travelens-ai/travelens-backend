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
                _inject_itinerary_ads(response)
            return jsonify(response), 200

        result = itinerary_service.recommender.generate_itinerary(user_preferences)
        itinerary_id = itinerary_service.store_itinerary(cache_key, user_preferences, result)

        response = with_image_urls(result) if isinstance(result, dict) else result
        if isinstance(response, dict):
            response["itinerary_id"] = itinerary_id
            _inject_itinerary_ads(response)

        return jsonify(response), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


def _inject_itinerary_ads(response):
    """Add ad slots between the places / accommodations / restaurants sections of
    each day in a (non-streaming) itinerary response. Mutates and returns the
    response. Each day gets a `section_ads` block so the client can place an ad
    between the sections without disturbing the existing arrays."""
    if not isinstance(response, dict):
        return response
    itinerary = response.get("data", {}).get("detailed_itinerary", {}) or {}
    for day in itinerary.get("itinerary", []):
        places = day.get("places_to_visit") or []
        hotels = day.get("hotels") or []
        restaurants = day.get("restaurants") or []
        section_ads = {}
        # Ad after places (before accommodations) and after accommodations
        # (before restaurants) — only when both adjacent sections exist.
        if places and hotels:
            section_ads["after_places"] = section_ad()
        if hotels and restaurants:
            section_ads["after_accommodations"] = section_ad()
        if section_ads:
            day["section_ads"] = section_ads
    # Attach the inline ad slot config for the itinerary section so the client
    # knows the slot's adtype/dimensions/refresh behavior.
    response["ads"] = get_inline_ads_config("itinerary_section")
    return response


def _decompose_response_events(response, itinerary_id, skip_events=None):
    """Yield granular SSE event dicts from a finalized itinerary response, in
    this order:
      1. info         — name/title, description, city, state, totals, notes
      2. images       — the main destination image gallery
      3. place        — each place_to_visit, one per event, day by day
         restaurant   — each restaurant, one per event, day by day
         hotel        — each hotel, one per event, day by day
      4. similar_place— each similar place, one per event
      5. places       — the recommended `places` list (whole array)
      6. done         — terminal marker carrying itinerary_id
    `skip_events` is a set of event names to omit (e.g. {"info","images"} when
    they were already streamed early). Images are already URL-prefixed by the
    caller (with_image_urls)."""
    skip_events = skip_events or set()
    data = response.get("data", {}) if isinstance(response, dict) else {}
    itinerary = data.get("detailed_itinerary", {}) or {}

    # 0. Inline ad slot config for the itinerary section (so the client knows
    # the adtype/dimensions/refresh for the `ad` events streamed below).
    if "ads_config" not in skip_events:
        yield {"event": "ads_config", "ads": get_inline_ads_config("itinerary_section")}

    # 1. Title / description and top-level info. Emit the authoritative values
    # from the generated itinerary unless they were already streamed early.
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

    # 3. Day by day: places, then an ad, then accommodations, then an ad, then
    # restaurants — one item per event, ad slots between the sections.
    for day in itinerary.get("itinerary", []):
        day_no = day.get("day")
        places = day.get("places_to_visit", [])
        hotels = day.get("hotels", [])
        restaurants = day.get("restaurants", [])

        for place in places:
            yield {"event": "place", "day": day_no, "item": place}
        if places and hotels:
            yield {"event": "ad", "day": day_no, "item": section_ad()}

        for hotel in hotels:
            yield {"event": "hotel", "day": day_no, "item": hotel}
        if hotels and restaurants:
            yield {"event": "ad", "day": day_no, "item": section_ad()}

        for restaurant in restaurants:
            yield {"event": "restaurant", "day": day_no, "item": restaurant}

    # 4. Similar places, one per event.
    for similar in itinerary.get("similar_places", []):
        yield {"event": "similar_place", "item": similar}

    # 5. The recommended places list.
    yield {"event": "places", "places": data.get("places", [])}

    # 6. Terminal marker.
    yield {"event": "done", "itinerary_id": itinerary_id}


@itinerary_bp.route("/generate-itinerary/stream", methods=["POST"])
def generate_itinerary_stream():
    """Generate a travel itinerary as a Server-Sent Events (SSE) stream.

    Emits `progress` events while the itinerary is being built, then streams the
    result piece by piece: `images`, `info`, then `place`/`restaurant`/`hotel`
    events day by day, then `similar_place` events, then `places`, and finally a
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
        description: Same payload as /generate-itinerary plus a `places` list of place names that must be included, and an `itinerary_id` of the itinerary to update in place
        schema:
          type: object
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
