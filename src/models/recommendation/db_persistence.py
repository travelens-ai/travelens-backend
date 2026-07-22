import os
import re
import time

import requests
import urllib3
from io import BytesIO
from core.db import new_connection

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_OUTPUT_DIR = "generated_images"
_WIKIMEDIA_URL = "https://commons.wikimedia.org/w/api.php"
_WIKIMEDIA_HEADERS = {
    "User-Agent": "TravelensImageBot/1.0 (info@travelens.in; https://travelens.in) Python-Requests"
}
_SKIP_WORDS = {"map", "logo", "icon", "flag", "svg", "diagram", "coat", "plan", "stamp", "chart"}
_TARGET_IMAGES = 5


def _wikimedia_search(query: str, count: int, offset: int) -> list:
    try:
        resp = requests.get(
            _WIKIMEDIA_URL,
            headers=_WIKIMEDIA_HEADERS,
            params={
                "action": "query", "generator": "search",
                "gsrsearch": query, "gsrnamespace": 6,
                "gsrlimit": max((offset + count) * 3, 15),
                "prop": "imageinfo", "iiprop": "url|size|mime|thumburl",
                "iiurlwidth": 1200, "format": "json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        valid = []
        for page in sorted(pages.values(), key=lambda x: x.get("index", 99)):
            ii = page.get("imageinfo", [{}])[0]
            mime = ii.get("mime", "")
            w, h = ii.get("width", 0), ii.get("height", 0)
            title_lower = page.get("title", "").lower()
            if mime != "image/jpeg" or h > w * 1.5:
                continue
            if any(s in title_lower for s in _SKIP_WORDS):
                continue
            url = ii.get("thumburl") or ii.get("url", "")
            if url:
                valid.append((url, "wikimedia"))
        return valid[offset: offset + count]
    except Exception as e:
        print(f"[image_fetch] Wikimedia error: {e}")
    return []


def _fetch_image_urls(query: str, count: int, offset: int = 0) -> list:
    """Collect up to `count` (url, source) tuples: Wikimedia → Pexels → Unsplash."""
    results = _wikimedia_search(query, count, offset)
    if not results:
        short_query = " ".join(query.split()[:2])
        if short_query != query:
            results = _wikimedia_search(short_query, count, offset)

    if len(results) < count:
        pexels_key = os.getenv("PEXELS_API_KEY", "")
        if pexels_key:
            try:
                existing = {u for u, _ in results}
                resp = requests.get(
                    "https://api.pexels.com/v1/search",
                    headers={"Authorization": pexels_key},
                    params={"query": query, "per_page": count - len(results), "orientation": "landscape"},
                    timeout=10,
                )
                resp.raise_for_status()
                for photo in resp.json().get("photos", []):
                    url = photo.get("src", {}).get("large", "")
                    if url and url not in existing:
                        results.append((url, "pexels"))
                        existing.add(url)
            except Exception as e:
                print(f"[image_fetch] Pexels error: {e}")

    if len(results) < count:
        unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY", "")
        if unsplash_key:
            try:
                existing = {u for u, _ in results}
                resp = requests.get(
                    "https://api.unsplash.com/search/photos",
                    headers={"Authorization": f"Client-ID {unsplash_key}"},
                    params={"query": query, "per_page": count - len(results), "orientation": "landscape"},
                    timeout=10,
                )
                resp.raise_for_status()
                for r in resp.json().get("results", []):
                    url = r.get("urls", {}).get("regular", "")
                    if url and url not in existing:
                        results.append((url, "unsplash"))
                        existing.add(url)
            except Exception as e:
                print(f"[image_fetch] Unsplash error: {e}")

    return results[:count]


def _download_as_webp(url: str, filename: str) -> bool:
    try:
        from PIL import Image
        time.sleep(3.0)
        resp = requests.get(url, headers=_WIKIMEDIA_HEADERS, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        img = img.resize((640, 360))
        os.makedirs(_OUTPUT_DIR, exist_ok=True)
        img.save(os.path.join(_OUTPUT_DIR, filename), format="WEBP", quality=85, optimize=True)
        return True
    except Exception as e:
        print(f"[image_fetch] download error: {e}")
        return False


def _upload_to_cdn(filepath: str) -> str:
    cdn_url = f"https://{os.getenv('CDN_HOSTINGER_IP')}/app/upload.php"
    try:
        with open(filepath, "rb") as f:
            resp = requests.post(
                cdn_url,
                files={"file": f},
                headers={"Host": "travelens.in"},
                timeout=30,
                verify=False,
            )
        if resp.status_code == 200:
            return os.path.basename(resp.json().get("path", ""))
    except Exception as e:
        print(f"[image_fetch] CDN upload error: {e}")
    return ""


def _link_place_image(place_id: int, image_name: str, source: str = None) -> bool:
    """Fresh connection per call — Azure SQL drops idle connections during download/upload waits."""
    conn = None
    try:
        conn = new_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            MERGE images AS tgt
            USING (SELECT ? AS image_name) AS src ON tgt.image_name = src.image_name
            WHEN NOT MATCHED THEN INSERT (id, image_name, source)
                VALUES ((SELECT ISNULL(MAX(id), 0) + 1 FROM images), src.image_name, ?);
            """,
            (image_name, source),
        )
        cursor.execute("SELECT id FROM images WHERE image_name = ?", (image_name,))
        row = cursor.fetchone()
        image_id = row[0] if row else None
        if image_id is None:
            return False
        cursor.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM place_image_map WHERE place_id = ? AND image_id = ?)
                INSERT INTO place_image_map (place_id, image_id) VALUES (?, ?)
            """,
            (place_id, image_id, place_id, image_id),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"[image_fetch] DB link error for place {place_id}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        if conn:
            conn.close()


def _fetch_and_link_images(place_rows: list):
    """Fetch up to 5 images per newly inserted place and link them. Runs in background."""
    if not place_rows:
        return
    print(f"[image_fetch] fetching images for {len(place_rows)} new place(s)")
    for place_id, display_name, city, state in place_rows:
        query = f"{display_name} {city} {state} India".strip()
        base = re.sub(r"[^\w\-]", "_",
                      "_".join(p for p in [display_name, city, state] if p).replace(" ", "_"))
        print(f"[image_fetch] {display_name} ({city}, {state})")

        url_tuples = _fetch_image_urls(query, count=_TARGET_IMAGES)
        if not url_tuples:
            print(f"[image_fetch] no images found for '{display_name}' — skipped")
            continue

        uploaded = 0
        for idx, (url, source) in enumerate(url_tuples, 1):
            filename = f"{base}_{idx}.webp"
            filepath = os.path.join(_OUTPUT_DIR, filename)
            if not os.path.exists(filepath):
                if not _download_as_webp(url, filename):
                    continue
            cdn_name = _upload_to_cdn(filepath)
            if not cdn_name:
                continue
            if _link_place_image(place_id, cdn_name, source):
                print(f"[image_fetch] [{idx}/{_TARGET_IMAGES}] linked {cdn_name} -> place {place_id}")
                uploaded += 1
            time.sleep(1.0)

        print(f"[image_fetch] {display_name}: {uploaded} image(s) linked")


def save_new_places(system, itinerary):
    """Persist new entities from the itinerary to the DB (background thread)."""
    try:
        conn = new_connection()
        cursor = conn.cursor()
    except Exception as e:
        print(f"[save_new_places] DB connection failed: {e}")
        return

    new_place_rows = []
    try:
        new_place_rows = _save_new_places_to_db(system, cursor, itinerary)
        _save_new_hotels_to_db(system, cursor, itinerary)
        _save_new_restaurants_to_db(system, cursor, itinerary)
        conn.commit()
    except Exception as e:
        print(f"[save_new_places] error: {e}")
        conn.rollback()
        new_place_rows = []
    finally:
        cursor.close()
        conn.close()

    _fetch_and_link_images(new_place_rows)


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
                                   item.get('lat'), item.get('lon'), item.get('full_address'),
                                   item.get('rating'), item.get('opening_hours')))
    if not candidates:
        return []

    cursor.execute("SELECT LOWER(name) FROM places")
    existing = {row[0] for row in cursor.fetchall()}
    inserted_rows, seen = [], set()
    for name, location, activities, lat, lon, full_address, rating, opening_hours in candidates:
        key = name.lower()
        if key in existing or key in seen:
            continue
        seen.add(key)
        try:
            city, state = _parse_city_state(location)
            city_id = _resolve_city_id(cursor, city, state)
            famous = ", ".join(activities) if isinstance(activities, list) and activities else None
            cursor.execute(
                "INSERT INTO places (name, display_name, city_id, famous_activities, lat, lon, full_address, rating, opening_hours) "
                "OUTPUT INSERTED.id "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, name, city_id, famous, lat, lon, full_address or None,
                 _to_decimal(rating), opening_hours or None),
            )
            row = cursor.fetchone()
            if row:
                place_id = int(row[0])
                inserted_rows.append((place_id, name, city or "", state or ""))
        except Exception as e:
            print(f"[save_new_places] failed to insert place '{name}': {e}")
    print(f"[save_new_places] inserted {len(inserted_rows)} new place(s).")
    return inserted_rows


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
