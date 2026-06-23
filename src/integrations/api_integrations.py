import os
import json
import time
import sqlite3
import requests
from datetime import date


DB_PATH = os.path.join("cache", "travelens_cache.db")

DAILY_QUOTA_LIMIT = 200  # hard cap — well within the 1,000/day free tier
IMAGES_TTL = 7 * 24 * 3600  # 7 days

# Essentials SKU only — do NOT add places.photos (that triggers Pro SKU billing)
_FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.priceLevel,places.googleMapsUri"

PRICE_LEVEL_MAP = {
    "PRICE_LEVEL_FREE": 1,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

def _get_db():
    os.makedirs("cache", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")  # safe for concurrent Flask threads
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hotels (
            city        TEXT PRIMARY KEY,
            data        TEXT NOT NULL,
            fetched_at  REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS restaurants (
            city        TEXT NOT NULL,
            cuisine     TEXT NOT NULL,
            data        TEXT NOT NULL,
            fetched_at  REAL NOT NULL,
            PRIMARY KEY (city, cuisine)
        );
        CREATE TABLE IF NOT EXISTS images (
            query       TEXT PRIMARY KEY,
            url         TEXT NOT NULL,
            expires_at  REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quota (
            day         TEXT PRIMARY KEY,
            count       INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Hotels
# ---------------------------------------------------------------------------

def _db_hotels_get(city: str):
    with _get_db() as conn:
        row = conn.execute("SELECT data FROM hotels WHERE city = ?", (city.lower(),)).fetchone()
    if row:
        return json.loads(row[0])
    return None


def _db_hotels_set(city: str, data: list):
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO hotels (city, data, fetched_at) VALUES (?, ?, ?)",
            (city.lower(), json.dumps(data), time.time()),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Restaurants
# ---------------------------------------------------------------------------

def _db_restaurants_get(city: str, cuisine: str):
    with _get_db() as conn:
        row = conn.execute(
            "SELECT data FROM restaurants WHERE city = ? AND cuisine = ?",
            (city.lower(), cuisine.lower()),
        ).fetchone()
    if row:
        return json.loads(row[0])
    return None


def _db_restaurants_set(city: str, cuisine: str, data: list):
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO restaurants (city, cuisine, data, fetched_at) VALUES (?, ?, ?, ?)",
            (city.lower(), cuisine.lower(), json.dumps(data), time.time()),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def _db_image_get(query: str):
    with _get_db() as conn:
        row = conn.execute(
            "SELECT url, expires_at FROM images WHERE query = ?", (query.lower(),)
        ).fetchone()
    if row and time.time() < row[1]:
        return row[0]
    return None


def _db_image_set(query: str, url: str):
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO images (query, url, expires_at) VALUES (?, ?, ?)",
            (query.lower(), url, time.time() + IMAGES_TTL),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# OpenStreetMap Nominatim geocoder
# ---------------------------------------------------------------------------

class NominatimClient:
    """Free geocoding via OpenStreetMap Nominatim. Stateless — callers cache
    results by writing lat/lon/full_address back onto their own DB rows
    (places/hotels/restaurants). Nominatim's usage policy asks for max ~1 req/sec
    and a valid User-Agent."""

    SEARCH_URL = "https://nominatim.openstreetmap.org/search"

    def __init__(self):
        self.user_agent = os.getenv("NOMINATIM_USER_AGENT", "travelens-backend/1.0")

    def geocode(self, query: str):
        """Resolve a query to {'lat', 'lon', 'full_address'}. Returns None when
        nothing is found or the request fails."""
        query = (query or "").strip()
        if not query:
            return None

        try:
            resp = requests.get(
                self.SEARCH_URL,
                params={"q": query, "format": "jsonv2", "limit": 1},
                headers={"User-Agent": self.user_agent},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
            if results:
                top = results[0]
                return {
                    "lat": float(top["lat"]),
                    "lon": float(top["lon"]),
                    "full_address": top.get("display_name", ""),
                }
        except Exception as e:
            print(f"[NominatimClient] geocode error for {query!r}: {e}")
        return None


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------

def _quota_check_and_increment():
    """Returns (allowed: bool, current_count: int). Increments counter if allowed."""
    today = date.today().isoformat()
    with _get_db() as conn:
        row = conn.execute("SELECT count FROM quota WHERE day = ?", (today,)).fetchone()
        count = row[0] if row else 0

        if count >= DAILY_QUOTA_LIMIT:
            print(f"[GooglePlacesClient] Daily quota cap ({DAILY_QUOTA_LIMIT}) reached — falling back to CSV")
            return False, count

        count += 1
        conn.execute(
            "INSERT OR REPLACE INTO quota (day, count) VALUES (?, ?)", (today, count)
        )
        conn.commit()
    return True, count


# ---------------------------------------------------------------------------
# Google Places API client
# ---------------------------------------------------------------------------

class GooglePlacesClient:
    NEW_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

    def __init__(self):
        self.api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")

    def _fetch(self, query):
        if not self.api_key:
            return []

        allowed, count = _quota_check_and_increment()
        if not allowed:
            return []

        try:
            resp = requests.post(
                self.NEW_PLACES_URL,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": _FIELD_MASK,
                },
                json={"textQuery": query, "maxResultCount": 20},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json().get("places", [])
            print(f"[GooglePlacesClient] quota: {count}/{DAILY_QUOTA_LIMIT} used today")
            return results
        except Exception as e:
            print(f"[GooglePlacesClient] fetch error: {e}")
            return []

    def resolve_place(self, name: str, city: str) -> dict | None:
        """Resolve a place name to Google Place ID, rating, review count, and Maps URI.
        Returns None when no result is found or the daily quota is exhausted.
        One call per place — uses the Essentials SKU, no extra billing."""
        results = self._fetch(f"{name} {city} India")
        if not results:
            return None
        r = results[0]
        return {
            "google_place_id":     r.get("id"),
            "google_rating":       r.get("rating"),
            "google_rating_count": r.get("userRatingCount"),
            "google_maps_uri":     r.get("googleMapsUri"),
        }

    def search_hotels(self, city: str) -> list:
        cached = _db_hotels_get(city)
        if cached is not None:
            return cached

        raw = self._fetch(f"hotels in {city} India")
        hotels = []
        for r in raw:
            price_str = r.get("priceLevel", "PRICE_LEVEL_MODERATE")
            price_level = PRICE_LEVEL_MAP.get(price_str, 2)
            price_label = {1: "Budget", 2: "Mid-range", 3: "Upscale", 4: "Luxury"}.get(price_level, "Mid-range")
            hotels.append({
                "property_name": r.get("displayName", {}).get("text", ""),
                "address": r.get("formattedAddress", ""),
                "city": city,
                "state": "",
                "country": "India",
                "hotel_star_rating": price_level,
                "property_type": price_label,
                "site_review_rating": r.get("rating", 0),
                "pageurl": r.get("googleMapsUri", ""),
            })

        _db_hotels_set(city, hotels)
        print(f"[GooglePlacesClient] fetched {len(hotels)} hotels for '{city}'")
        return hotels

    def search_restaurants(self, city: str, cuisine: str = "") -> list:
        query_cuisine = cuisine.split(",")[0].strip() if cuisine else ""
        cached = _db_restaurants_get(city, query_cuisine or "all")
        if cached is not None:
            return cached

        query = f"{query_cuisine} restaurants in {city} India" if query_cuisine else f"restaurants in {city} India"
        raw = self._fetch(query)
        restaurants = []
        for r in raw:
            restaurants.append({
                "Name": r.get("displayName", {}).get("text", ""),
                "Location": r.get("formattedAddress", ""),
                "Locality": city,
                "City": city,
                "Cuisine": query_cuisine or "Multi-cuisine",
                "Rating": r.get("rating", 0),
                "Votes": 0,
                "Cost": "",
            })

        _db_restaurants_set(city, query_cuisine or "all", restaurants)
        print(f"[GooglePlacesClient] fetched {len(restaurants)} restaurants for '{city}' / '{query_cuisine}'")
        return restaurants


# ---------------------------------------------------------------------------
# Image search client (Pexels + Unsplash)
# ---------------------------------------------------------------------------

class ImageSearchClient:
    PEXELS_URL = "https://api.pexels.com/v1/search"
    UNSPLASH_URL = "https://api.unsplash.com/search/photos"

    def __init__(self):
        self.pexels_key = os.getenv("PEXELS_API_KEY", "")
        self.unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY", "")

    def get_place_image(self, place_name: str, city: str = "") -> str:
        query = f"{place_name} {city}".strip()
        cached = _db_image_get(query)
        if cached:
            return cached

        url = self._fetch_pexels(query) or self._fetch_unsplash(query)
        if url:
            _db_image_set(query, url)
        return url or ""

    def _fetch_pexels(self, query: str) -> str:
        if not self.pexels_key:
            return ""
        try:
            resp = requests.get(
                self.PEXELS_URL,
                headers={"Authorization": self.pexels_key},
                params={"query": query, "per_page": 1, "orientation": "landscape"},
                timeout=8,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
            if photos:
                return photos[0].get("src", {}).get("large", "")
        except Exception as e:
            print(f"[ImageSearchClient] Pexels error: {e}")
        return ""

    def _fetch_unsplash(self, query: str) -> str:
        if not self.unsplash_key:
            return ""
        try:
            resp = requests.get(
                self.UNSPLASH_URL,
                headers={"Authorization": f"Client-ID {self.unsplash_key}"},
                params={"query": query, "per_page": 1, "orientation": "landscape"},
                timeout=8,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                return results[0].get("urls", {}).get("regular", "")
        except Exception as e:
            print(f"[ImageSearchClient] Unsplash error: {e}")
        return ""
