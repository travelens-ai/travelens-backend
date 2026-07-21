import copy
import os
import pickle
import random
import threading
import time as _time

import pandas as pd
from flask import json

from core.db import fetch_dicts, new_connection, is_db_ready
from models.recommendation import image_helpers as _img
from models.recommendation import db_persistence as _dbp

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

# Circuit-breaker: skip DB lat/lon lookups for 30s after a connection failure.
_db_lat_long_dead_until = 0.0
_db_lat_long_dead_lock = None


def _get_lock():
    global _db_lat_long_dead_lock
    if _db_lat_long_dead_lock is None:
        _db_lat_long_dead_lock = threading.Lock()
    return _db_lat_long_dead_lock


def db_lat_long(system, table, name):
    global _db_lat_long_dead_until
    if not name:
        return None
    if not is_db_ready():
        return None

    lock = _get_lock()
    with lock:
        if _time.monotonic() < _db_lat_long_dead_until:
            return None

    name_col = "property_name" if table == "hotels" else "name"
    try:
        rows = fetch_dicts(
            f"SELECT TOP 1 lat, lon, full_address FROM {table} "
            f"WHERE LOWER({name_col}) = ? AND lat IS NOT NULL AND lon IS NOT NULL",
            (str(name).strip().lower(),),
        )
    except Exception as e:
        print(f"  _db_lat_long failed for {name!r} in {table} ({e})")
        with _get_lock():
            _db_lat_long_dead_until = _time.monotonic() + 30.0
        return None
    if rows:
        return {
            "lat": float(rows[0]["lat"]),
            "lon": float(rows[0]["lon"]),
            "full_address": rows[0].get("full_address") or "",
        }
    return None


def db_lat_long_batch(system, table, names):
    global _db_lat_long_dead_until
    names = [n for n in names if n]
    if not names or not is_db_ready():
        return {}

    lock = _get_lock()
    with lock:
        if _time.monotonic() < _db_lat_long_dead_until:
            return {}

    name_col = "property_name" if table == "hotels" else "name"
    lower_names = [str(n).strip().lower() for n in names]
    placeholders = ",".join(["?" for _ in lower_names])
    try:
        rows = fetch_dicts(
            f"SELECT {name_col}, lat, lon, full_address FROM {table} "
            f"WHERE LOWER({name_col}) IN ({placeholders}) AND lat IS NOT NULL AND lon IS NOT NULL",
            lower_names,
        )
    except Exception as e:
        print(f"  _db_lat_long_batch failed for {table} ({e})")
        with _get_lock():
            _db_lat_long_dead_until = _time.monotonic() + 30.0
        return {}

    result = {}
    for row in rows:
        key = str(row.get(name_col) or "").strip().lower()
        if key and key not in result:
            result[key] = {
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "full_address": row.get("full_address") or "",
            }
    return result


def save_lat_long_to_db(system, table, name, lat, lon, full_address):
    if not name:
        return
    name_col = "property_name" if table == "hotels" else "name"
    try:
        conn = new_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {table} SET lat = ?, lon = ?, full_address = ? "
            f"WHERE LOWER({name_col}) = ? AND (lat IS NULL OR lon IS NULL)",
            (lat, lon, full_address, str(name).strip().lower()),
        )
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"  _save_lat_long_to_db failed for {name!r} in {table} ({e})")


def resolve_lat_long(system, table, name, location_hint=""):
    coords = db_lat_long(system, table, name)
    if coords is not None:
        return coords
    return None


def attach_db_fields(system, itinerary):
    if system.places_df is None or system.places_df.empty:
        return
    db_lookup = {
        str(r.get("placename") or r.get("name", "")).strip().lower(): r
        for r in system.places_df.to_dict(orient="records")
    }
    for day in itinerary.get("itinerary", []):
        for item in day.get("timeline", []):
            if item.get("type") != "place":
                continue
            key = str(item.get("name", "")).strip().lower()
            db = db_lookup.get(key)
            if not db:
                continue
            if db.get("opening_hours"):
                item["opening_hours"] = db["opening_hours"]
            if db.get("website_uri"):
                item["website_uri"] = db["website_uri"]
            if db.get("phone_number"):
                item["phone_number"] = db["phone_number"]
            if db.get("google_maps_uri"):
                item["google_maps_uri"] = db["google_maps_uri"]
            if db.get("full_address"):
                item["full_address"] = db["full_address"]
            if db.get("good_for_children") is not None:
                item["good_for_children"] = db["good_for_children"]
            if db.get("accessibility"):
                item["accessibility"] = db["accessibility"]
            item["editorial_summary"] = db.get("editorial_summary") or ""
            item["review_summary"] = db.get("review_summary") or ""
            item["short_formatted_address"] = db.get("short_formatted_address") or ""
            item["google_rating"] = db.get("google_rating")
            item["google_rating_count"] = db.get("google_rating_count")


def attach_lat_long(system, itinerary):
    place_entities = []
    rest_entities = []
    hotel_entities = []

    for day in itinerary.get('itinerary', []):
        for item in day.get('timeline', []):
            t = item.get('type')
            if t == 'place':
                place_entities.append(item)
            elif t == 'meal':
                rest_entities.append(item)
        for slot_opts in day.get('meal_options', {}).values():
            for opt in (slot_opts or []):
                if isinstance(opt, dict):
                    rest_entities.append(opt)
    for hotel_group in itinerary.get('hotels', []):
        if isinstance(hotel_group, dict):
            sel = hotel_group.get('selected')
            if isinstance(sel, dict):
                hotel_entities.append(sel)
            for alt in hotel_group.get('alternatives', []):
                if isinstance(alt, dict):
                    hotel_entities.append(alt)

    def _apply_batch(entities, table):
        if not entities:
            return
        names = [e.get('name') for e in entities]
        coords = db_lat_long_batch(system, table, names)
        for entity in entities:
            key = str(entity.get('name') or '').strip().lower()
            res = coords.get(key)
            if res:
                entity['lat'] = res['lat']
                entity['lon'] = res['lon']
                entity['full_address'] = res.get('full_address', '')
            else:
                entity.setdefault('lat', None)
                entity.setdefault('lon', None)
                entity.setdefault('full_address', '')

    _apply_batch(place_entities, 'places')
    _apply_batch(rest_entities, 'restaurants')
    _apply_batch(hotel_entities, 'hotels')


def compute_travel_times(system, itinerary):
    from math import radians, sin, cos, sqrt, atan2

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return R * 2 * atan2(sqrt(a), sqrt(1 - a))

    for day in itinerary.get('itinerary', []):
        locatable = [
            item for item in day.get('timeline', [])
            if item.get('lat') is not None and item.get('lon') is not None
        ]
        for i, item in enumerate(locatable):
            if i == 0:
                item['travel_from_prev'] = None
                continue
            prev = locatable[i - 1]
            try:
                dist_km = haversine(
                    float(prev['lat']), float(prev['lon']),
                    float(item['lat']), float(item['lon'])
                )
                mins = max(5, int(dist_km / 30 * 60))
                mode = 'walking' if dist_km < 1.5 else 'auto' if dist_km < 4 else 'cab'
                item['travel_from_prev'] = {
                    'duration_mins': mins,
                    'mode': mode,
                    'note': f'~{mins} min by {mode} from {prev["name"]}'
                }
            except (TypeError, KeyError, ValueError):
                pass


def attach_weather(system, itinerary, start_date_str):
    from datetime import datetime, timedelta
    from concurrent.futures import ThreadPoolExecutor
    from features.weather.service import get_weather_by_coords

    try:
        trip_start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        return

    tasks = []
    for i, day in enumerate(itinerary.get('itinerary', [])):
        day_date = (trip_start + timedelta(days=i)).strftime("%Y-%m-%d")
        for item in day.get('timeline', []):
            if item.get('type') == 'place':
                tasks.append((item, i, day_date))

    if not tasks:
        return

    def _fetch_weather(task):
        place, day_idx, day_date = task
        lat = place.get('lat')
        lon = place.get('lon')
        if lat is None or lon is None:
            return None, day_idx
        try:
            result, _ = get_weather_by_coords(float(lat), float(lon), day_date, days=1)
            return result, day_idx
        except Exception:
            return None, day_idx

    with ThreadPoolExecutor(max_workers=8) as ex:
        weather_results = list(ex.map(_fetch_weather, tasks))

    day_weather = {}
    for (place, day_idx, _), (result, _) in zip(tasks, weather_results):
        if result and result.get('weather'):
            entry = result['weather'][0]
            place['weather'] = entry
            if day_idx not in day_weather:
                day_weather[day_idx] = entry
        else:
            place['weather'] = None

    for i, day in enumerate(itinerary.get('itinerary', [])):
        day['weather'] = day_weather.get(i)


def finalize_trip_level(system, itinerary, places):
    if places.empty:
        places = system.getEditPlaces(itinerary['city'], itinerary['state'])

    merged_places = list(places.to_dict(orient='records'))
    popular_destinations = system.get_popular_destination()
    if popular_destinations:
        merged_places.extend(popular_destinations)

    place_image_map = {}
    for place in merged_places:
        image = place.get('image')
        if image and pd.notna(image):
            pname = place.get('placename') or place.get('city') or place.get('name', '')
            if pname:
                place_image_map[pname] = image

    placename = itinerary['name']

    try:
        with open(os.path.join(_PROJECT_ROOT, 'similar_places.pkl'), 'rb') as f:
            similar_places_data = pickle.load(f)
    except FileNotFoundError:
        similar_places_data = {}

    if placename not in place_image_map and placename in similar_places_data:
        place_image_map[placename] = similar_places_data.get(placename).get('image')
    if placename not in place_image_map:
        place_image_map[placename] = 'default' + str(random.randint(1, 7)) + '.webp'

    main_single_image = None
    if placename in place_image_map and pd.notna(place_image_map[placename]):
        main_single_image = place_image_map[placename]

    main_images = _img.get_images_for_places(system, [placename]).get(
        str(placename).strip().lower(), []
    )
    if not main_images:
        main_images = _img.search_images_by_keywords(
            system, [placename, itinerary.get('city'), itinerary.get('state')], limit=5
        )
    if not main_images and main_single_image:
        main_images = [main_single_image]
    itinerary['images'] = main_images

    similar = itinerary.get('similar_places') or []
    threading.Thread(target=system.save_similar_places, args=(similar,), daemon=True).start()
    for place in similar:
        sp_name = place.get('placename')
        matching_place = similar_places_data.get(sp_name) if sp_name else None
        img = matching_place.get('image') if matching_place else None
        if img and pd.notna(img) and str(img).strip():
            place['image'] = img
        else:
            place['image'] = 'default' + str(random.randint(1, 7)) + '.webp'

    system._place_image_fallback = {
        str(name).strip().lower(): img for name, img in place_image_map.items()
    }
    return places


def _parse_time_key(item):
    """Return a sortable (hour, minute) tuple from suggested_time / suggested_start_time."""
    from datetime import datetime
    for field in ('suggested_time', 'suggested_start_time'):
        raw = str(item.get(field) or '').strip()
        if not raw:
            continue
        for fmt in ('%I:%M %p', '%H:%M'):
            try:
                t = datetime.strptime(raw, fmt)
                return (t.hour, t.minute)
            except ValueError:
                continue
    return (99, 99)  # items with no time sort to end


def _sort_timeline_by_time(days):
    for day in days:
        timeline = day.get('timeline')
        if isinstance(timeline, list):
            day['timeline'] = sorted(timeline, key=_parse_time_key)


def finalize_days(system, itinerary, days, places, start_date=None, start_day_index=0):
    fallback_map = getattr(system, '_place_image_fallback', {}) or {}
    all_place_names = [
        str(item.get('name', '')).strip()
        for day in days
        for item in day.get('timeline', [])
        if item.get('type') == 'place' and str(item.get('name', '')).strip()
    ]
    place_images_db = _img.get_images_for_places(system, all_place_names) if all_place_names else {}
    for day in days:
        for item in day.get('timeline', []):
            if item.get('type') != 'place':
                continue
            place_name = str(item.get('name', '')).strip()
            key = place_name.lower()
            images = place_images_db.get(key, [])[:5]
            if not images:
                images = _img.search_images_by_keywords(system, [place_name], limit=5)
            if not images:
                single = fallback_map.get(key)
                if single and not (not isinstance(single, list) and pd.isna(single)):
                    images = [single]
            if not images:
                images = ['default' + str(random.randint(1, 7)) + '.webp']
            item['images'] = images

    _sort_timeline_by_time(days)

    ctx = {
        'city': itinerary.get('city'),
        'state': itinerary.get('state'),
        'itinerary': days,
        'hotels': itinerary.get('hotels', []),
    }
    attach_lat_long(system, ctx)
    compute_travel_times(system, ctx)
    attach_db_fields(system, ctx)

    if start_date:
        from datetime import datetime, timedelta
        try:
            base = datetime.strptime(start_date, "%Y-%m-%d").date()
            subset_start = (base + timedelta(days=start_day_index)).strftime("%Y-%m-%d")
            attach_weather(system, ctx, subset_start)
            for idx, day in enumerate(days):
                day_date = base + timedelta(days=start_day_index + idx)
                day['date'] = day_date.strftime("%Y-%m-%d")
                day['weekday'] = day_date.strftime("%A")
        except ValueError:
            pass

    threading.Thread(target=_dbp.save_new_places, args=(system, copy.deepcopy(ctx)), daemon=True).start()

    cleaned = json.loads(json.dumps(days, default=lambda x: '' if pd.isna(x) else x))
    return cleaned


def finalize_itinerary(system, itinerary, places, start_date=None, user_preferences=None):
    try:
        from models.recommendation import recommendations as _rec
        places = finalize_trip_level(system, itinerary, places)

        days = itinerary.get('itinerary', []) or []
        itinerary['itinerary'] = finalize_days(
            system, itinerary, days, places, start_date=start_date, start_day_index=0
        )

        try:
            itinerary['total_days'] = int(itinerary['total_days'])
        except (TypeError, ValueError, KeyError):
            itinerary['total_days'] = len(itinerary.get('itinerary', []))

        token_usage = itinerary.pop('_token_usage', None) if isinstance(itinerary, dict) else None

        if user_preferences:
            try:
                itinerary['available_places'] = _rec.get_available_places(
                    system, itinerary, user_preferences, 30, scored_df=places
                )
            except Exception as e:
                print(f"Warning: _get_available_places failed: {e}")
                itinerary['available_places'] = []

        return {
            'status': 'success',
            'token_usage': token_usage,
            'data': {'detailed_itinerary': itinerary},
        }

    except Exception as e:
        import traceback
        print("Error while processing:", e)
        traceback.print_exc()
        return {'status': 'error', 'message': str(e)}


def get_available_places(system, itinerary, user_preferences, count, scored_df=None):
    from models.recommendation.recommendations import get_available_places as _ap
    return _ap(system, itinerary, user_preferences, count, scored_df=scored_df)
