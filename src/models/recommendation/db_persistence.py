import re
from core.db import new_connection


def save_new_places(system, itinerary):
    """Persist new entities from the itinerary to the DB (background thread)."""
    try:
        conn = new_connection()
        cursor = conn.cursor()
    except Exception as e:
        print(f"[save_new_places] DB connection failed: {e}")
        return

    try:
        _save_new_places_to_db(system, cursor, itinerary)
        _save_new_hotels_to_db(system, cursor, itinerary)
        _save_new_restaurants_to_db(system, cursor, itinerary)
        conn.commit()
    except Exception as e:
        print(f"[save_new_places] error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


def _parse_city_state(location):
    city = state = None
    if location:
        parts = [p.strip() for p in str(location).split(',')]
        if parts and parts[0]:
            city = parts[0].lower()
        if len(parts) > 1 and parts[1]:
            state = parts[1].strip()
    return city, state


def _to_decimal(value):
    if value is None:
        return None
    m = re.search(r'\d+(?:\.\d+)?', str(value).replace(',', ''))
    return float(m.group()) if m else None


def _save_new_places_to_db(system, cursor, itinerary):
    candidates = []
    for day in itinerary.get('itinerary', []):
        for item in day.get('timeline', []):
            if item.get('type') != 'place':
                continue
            name = str(item.get('name', '')).strip()
            if name:
                candidates.append((name, item.get('location', ''), item.get('activities'),
                                   item.get('lat'), item.get('lon'), item.get('full_address')))
    if not candidates:
        return

    cursor.execute("SELECT LOWER(name) FROM places")
    existing = {row[0] for row in cursor.fetchall()}
    inserted, seen = 0, set()
    for name, location, activities, lat, lon, full_address in candidates:
        key = name.lower()
        if key in existing or key in seen:
            continue
        seen.add(key)
        try:
            city, state = _parse_city_state(location)
            city_id = _resolve_city_id(cursor, city, state)
            famous = ", ".join(activities) if isinstance(activities, list) and activities else None
            cursor.execute(
                "INSERT INTO places (name, display_name, city_id, famous_activities, lat, lon, full_address) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, name, city_id, famous, lat, lon, full_address or None),
            )
            inserted += 1
        except Exception as e:
            print(f"[save_new_places] failed to insert place '{name}': {e}")
    print(f"[save_new_places] inserted {inserted} new place(s).")


def _save_new_hotels_to_db(system, cursor, itinerary):
    candidates = []
    for group in itinerary.get('hotels', []):
        if not isinstance(group, dict):
            continue
        h = group.get('selected')
        if isinstance(h, dict) and str(h.get('name', '')).strip():
            candidates.append(h)
        for h in (group.get('alternatives') or []):
            if isinstance(h, dict) and str(h.get('name', '')).strip():
                candidates.append(h)
    if not candidates:
        return

    cursor.execute("SELECT LOWER(property_name) FROM hotels WHERE property_name IS NOT NULL")
    existing = {row[0] for row in cursor.fetchall()}
    inserted, seen = 0, set()
    for hotel in candidates:
        name = str(hotel.get('name', '')).strip()
        key = name.lower()
        if key in existing or key in seen:
            continue
        seen.add(key)
        try:
            city, state = _parse_city_state(hotel.get('location', ''))
            cursor.execute(
                """INSERT INTO hotels
                   (property_name, property_type, city, state, site_review_rating, pageurl, lat, lon, full_address)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, str(hotel.get('type', '')).strip() or None,
                 city, state, _to_decimal(hotel.get('rating')),
                 str(hotel.get('link', '')).strip() or None,
                 hotel.get('lat'), hotel.get('lon'),
                 hotel.get('full_address') or None),
            )
            inserted += 1
        except Exception as e:
            print(f"[save_new_places] failed to insert hotel '{name}': {e}")
    print(f"[save_new_places] inserted {inserted} new hotel(s).")


def _save_new_restaurants_to_db(system, cursor, itinerary):
    candidates = []
    for day in itinerary.get('itinerary', []):
        for item in day.get('timeline', []):
            if item.get('type') != 'meal':
                continue
            name = str(item.get('name', '')).strip()
            if name:
                candidates.append(item)
    if not candidates:
        return

    cursor.execute("SELECT LOWER(name) FROM restaurants WHERE name IS NOT NULL")
    existing = {row[0] for row in cursor.fetchall()}
    inserted, seen = 0, set()
    for r in candidates:
        name = str(r.get('name', '')).strip()
        key = name.lower()
        if key in existing or key in seen:
            continue
        seen.add(key)
        try:
            city, _ = _parse_city_state(r.get('location', ''))
            cost = _to_decimal(r.get('approx_cost'))
            cursor.execute(
                """INSERT INTO restaurants
                   (name, locality, city, cuisine, rating, cost, lat, lon, full_address)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, str(r.get('location', '')).strip() or None, city,
                 str(r.get('cuisine', '')).strip() or None,
                 _to_decimal(r.get('rating')),
                 int(cost) if cost is not None else None,
                 r.get('lat'), r.get('lon'), r.get('full_address') or None),
            )
            inserted += 1
        except Exception as e:
            print(f"[save_new_places] failed to insert restaurant '{name}': {e}")
    print(f"[save_new_places] inserted {inserted} new restaurant(s).")


def _resolve_city_id(cursor, city, state):
    if not city:
        return None
    cursor.execute("SELECT id FROM cities WHERE name = ?", (city,))
    row = cursor.fetchone()
    if row:
        return row[0]
    state_id = None
    if state:
        cursor.execute("SELECT id FROM states WHERE LOWER(name) = ?", (state.lower(),))
        srow = cursor.fetchone()
        if srow:
            state_id = srow[0]
    cursor.execute(
        "INSERT INTO cities (name, state_id) OUTPUT INSERTED.id VALUES (?, ?)", (city, state_id)
    )
    return int(cursor.fetchone()[0])
