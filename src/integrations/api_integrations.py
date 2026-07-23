import os
import json
import time
import sqlite3
import threading
import requests
from datetime import date


DB_PATH = os.path.join("cache", "travelens_cache.db")

DAILY_QUOTA_LIMIT = 300  # hard cap — well within the 1,000/day free tier
IMAGES_TTL = 7 * 24 * 3600  # 7 days

# Call 1 — Text Search, IDs Only SKU ($0.00 free).
# ONLY these two fields — adding anything else triggers a paid tier.
_FIELD_MASK_SEARCH = "places.id,places.name"

# Call 2 — Place Details, Enterprise+Atmosphere SKU (~$0.025/place).
# Fetches ALL available fields at the highest tier to maximise value.
# Tier breakdown: Essentials ($0.005) < Pro ($0.017) < Enterprise ($0.020) < Ent+Atm ($0.025)
# We pay Ent+Atm due to `reviews` — every other field here is free at that price.
_FIELD_MASK_DETAILS = (
    # Essentials (IDs Only)
    "id,name,attributions,"
    # Essentials
    "location,formattedAddress,shortFormattedAddress,addressComponents,"
    "addressDescriptor,adrFormatAddress,plusCode,viewport,types,"
    # Pro
    "displayName,primaryType,primaryTypeDisplayName,businessStatus,"
    "googleMapsUri,googleMapsLinks,timeZone,utcOffsetMinutes,accessibilityOptions,"
    "iconMaskBaseUri,iconBackgroundColor,"
    "containingPlaces,subDestinations,pureServiceAreaBusiness,"
    "openingDate,"
    # Enterprise
    "rating,userRatingCount,nationalPhoneNumber,internationalPhoneNumber,"
    "regularOpeningHours,currentOpeningHours,"
    "regularSecondaryOpeningHours,currentSecondaryOpeningHours,"
    "priceLevel,priceRange,websiteUri,"
    # Enterprise + Atmosphere
    "reviews,reviewSummary,photos,"
    "editorialSummary,generativeSummary,neighborhoodSummary,"
    "evChargeOptions,evChargeAmenitySummary,fuelOptions,"
    "allowsDogs,curbsidePickup,delivery,dineIn,goodForChildren,goodForGroups,"
    "goodForWatchingSports,liveMusic,menuForChildren,outdoorSeating,"
    "parkingOptions,paymentOptions,reservable,restroom,"
    "servesBeer,servesBreakfast,servesBrunch,servesCocktails,servesCoffee,"
    "servesDessert,servesDinner,servesLunch,servesVegetarianFood,servesWine,takeout"
)

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

    _last_call_lock = threading.Lock()
    _last_call_time = 0.0

    def _rate_limit(self):
        with NominatimClient._last_call_lock:
            now = time.monotonic()
            wait = 1.1 - (now - NominatimClient._last_call_time)
            if wait > 0:
                time.sleep(wait)
            NominatimClient._last_call_time = time.monotonic()

    def geocode(self, query: str):
        """Resolve a query to {'lat', 'lon', 'full_address'}. Returns None when
        nothing is found or the request fails. Enforces Nominatim's 1 req/sec policy."""
        query = (query or "").strip()
        if not query:
            return None

        self._rate_limit()
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
    NEW_PLACES_URL   = "https://places.googleapis.com/v1/places:searchText"
    PLACE_DETAILS_URL = "https://places.googleapis.com/v1/"

    def __init__(self):
        self.api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")

    def resolve_place_id(self, name: str, city: str) -> str | None:
        """Step 1 — Text Search, IDs Only SKU. Cost: $0.00.
        Returns the resource name (e.g. 'places/ChIJ...') for use in fetch_place_details().
        Does NOT count against the paid quota.
        Tries name+city first; falls back to name-only if city is too obscure for Google."""
        if not self.api_key:
            return None
        import re as _re
        clean_name = _re.sub(r'\s*\(.*?\)', '', name).strip()
        queries = [
            f"{clean_name} {city} India",
            f"{clean_name} India",
        ]
        for query in queries:
            try:
                resp = requests.post(
                    self.NEW_PLACES_URL,
                    headers={
                        "Content-Type": "application/json",
                        "X-Goog-Api-Key": self.api_key,
                        "X-Goog-FieldMask": _FIELD_MASK_SEARCH,
                    },
                    json={"textQuery": query, "maxResultCount": 1},
                    timeout=10,
                )
                resp.raise_for_status()
                places = resp.json().get("places", [])
                if places:
                    return places[0].get("name")
            except Exception as e:
                print(f"[GooglePlacesClient] resolve_place_id error ({query}): {e}")
                return None
        return None

    def fetch_place_details(self, resource_name: str) -> dict | None:
        """Step 2 — Place Details, Enterprise+Atmosphere SKU. Cost: ~$0.025/place.
        resource_name is the full path e.g. 'places/ChIJ...' from resolve_place_id()."""
        if not self.api_key:
            return None

        allowed, count = _quota_check_and_increment()
        if not allowed:
            return None

        try:
            resp = requests.get(
                self.PLACE_DETAILS_URL + resource_name,
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": _FIELD_MASK_DETAILS,
                },
                timeout=10,
            )
            resp.raise_for_status()
            r = resp.json()
            print(f"[GooglePlacesClient] quota: {count}/{DAILY_QUOTA_LIMIT} used today")

            def _extract_text(obj):
                if not obj:
                    return None
                v = obj.get("text")
                if isinstance(v, dict):
                    return v.get("text")
                return v

            loc = r.get("location") or {}
            hours = (r.get("regularOpeningHours") or {}).get("weekdayDescriptions")
            current_hours = (r.get("currentOpeningHours") or {}).get("weekdayDescriptions")
            secondary_hours = r.get("regularSecondaryOpeningHours")
            current_secondary_hours = r.get("currentSecondaryOpeningHours")
            types = r.get("types") or []
            photos = [p.get("name") for p in r.get("photos", [])[:5] if p.get("name")]
            reviews = [
                {
                    "rating": rv.get("rating"),
                    "text": (rv.get("text") or {}).get("text", ""),
                    "author": (rv.get("authorAttribution") or {}).get("displayName", ""),
                }
                for rv in r.get("reviews", [])[:5]
            ]
            price_range_raw = r.get("priceRange") or {}
            price_range = None
            if price_range_raw:
                lo = (price_range_raw.get("startPrice") or {}).get("units")
                hi = (price_range_raw.get("endPrice") or {}).get("units")
                if lo is not None or hi is not None:
                    price_range = f"₹{lo}–₹{hi}" if (lo and hi) else (f"₹{lo}+" if lo else None)
            opening_date_raw = r.get("openingDate") or {}
            opening_date = None
            if opening_date_raw:
                y = opening_date_raw.get("year")
                m = opening_date_raw.get("month")
                d = opening_date_raw.get("day")
                parts = [str(p) for p in [y, m, d] if p]
                opening_date = "-".join(parts) if parts else None
            moved = r.get("movedPlace") or {}
            return {
                # Essentials / IDs Only
                "google_place_id":                r.get("id"),
                "resource_name":                  r.get("name"),
                "attributions":                   r.get("attributions"),
                # Essentials — location
                "full_address":                   r.get("formattedAddress"),
                "short_formatted_address":        r.get("shortFormattedAddress"),
                "adr_format_address":             r.get("adrFormatAddress"),
                "address_components":             r.get("addressComponents"),
                "address_descriptor":             r.get("addressDescriptor"),
                "plus_code":                      r.get("plusCode"),
                "viewport":                       r.get("viewport"),
                "lat":                            loc.get("latitude"),
                "lon":                            loc.get("longitude"),
                "place_types":                    ", ".join(types[:3]) if types else None,
                # Pro — identity
                "display_name":                   (r.get("displayName") or {}).get("text"),
                "primary_type":                   r.get("primaryType"),
                "primary_type_name":              (r.get("primaryTypeDisplayName") or {}).get("text"),
                "icon_mask_base_uri":             r.get("iconMaskBaseUri"),
                "icon_background_color":          r.get("iconBackgroundColor"),
                "google_maps_uri":                r.get("googleMapsUri"),
                "maps_links":                     r.get("googleMapsLinks"),
                "business_status":                r.get("businessStatus"),
                "timezone":                       (r.get("timeZone") or {}).get("id"),
                "utc_offset_minutes":             r.get("utcOffsetMinutes"),
                "accessibility":                  r.get("accessibilityOptions"),
                # Pro — hierarchy
                "containing_places":              r.get("containingPlaces"),
                "sub_destinations":               r.get("subDestinations"),
                "is_service_area_only":           r.get("pureServiceAreaBusiness"),
                "moved_place_id":                 moved.get("name"),
                "opening_date":                   opening_date,
                # Enterprise
                "google_rating":                  r.get("rating"),
                "google_rating_count":            r.get("userRatingCount"),
                "phone_number":                   r.get("nationalPhoneNumber"),
                "international_phone_number":     r.get("internationalPhoneNumber"),
                "opening_hours":                  hours,
                "current_opening_hours":          current_hours,
                "secondary_hours":                secondary_hours,
                "current_secondary_hours":        current_secondary_hours,
                "price_level":                    r.get("priceLevel"),
                "price_range":                    price_range,
                "website_uri":                    r.get("websiteUri"),
                # Enterprise + Atmosphere
                "google_photo_refs":              photos if photos else None,
                "google_reviews":                 reviews if reviews else None,
                "review_summary":                 _extract_text(r.get("reviewSummary")),
                "editorial_summary":              (r.get("editorialSummary") or {}).get("text"),
                "generative_summary":             ((r.get("generativeSummary") or {}).get("overview") or {}).get("text"),
                "neighborhood_summary":           ((r.get("neighborhoodSummary") or {}).get("overview") or {}).get("text"),
                "ev_charge_options":              r.get("evChargeOptions"),
                "ev_summary":                     ((r.get("evChargeAmenitySummary") or {}).get("overview") or {}).get("text"),
                "fuel_options":                   r.get("fuelOptions"),
                "allows_dogs":                    r.get("allowsDogs"),
                "curbside_pickup":                r.get("curbsidePickup"),
                "delivery":                       r.get("delivery"),
                "dine_in":                        r.get("dineIn"),
                "good_for_children":              r.get("goodForChildren"),
                "good_for_groups":                r.get("goodForGroups"),
                "good_for_watching_sports":       r.get("goodForWatchingSports"),
                "live_music":                     r.get("liveMusic"),
                "menu_for_children":              r.get("menuForChildren"),
                "outdoor_seating":                r.get("outdoorSeating"),
                "parking_options":                json.dumps(r["parkingOptions"]) if r.get("parkingOptions") else None,
                "payment_options":                json.dumps(r["paymentOptions"]) if r.get("paymentOptions") else None,
                "reservable":                     r.get("reservable"),
                "restroom":                       r.get("restroom"),
                "serves_beer":                    r.get("servesBeer"),
                "serves_breakfast":               r.get("servesBreakfast"),
                "serves_brunch":                  r.get("servesBrunch"),
                "serves_cocktails":               r.get("servesCocktails"),
                "serves_coffee":                  r.get("servesCoffee"),
                "serves_dessert":                 r.get("servesDessert"),
                "serves_dinner":                  r.get("servesDinner"),
                "serves_lunch":                   r.get("servesLunch"),
                "serves_vegetarian_food":         r.get("servesVegetarianFood"),
                "serves_wine":                    r.get("servesWine"),
                "takeout":                        r.get("takeout"),
            }
        except Exception as e:
            print(f"[GooglePlacesClient] fetch_place_details error: {e}")
            return None

    def resolve_place(self, name: str, city: str, known_place_id: str = None) -> dict | None:
        """Resolve a place using 2-step process:
        Step 1 (free): Text Search → get resource name (skipped if known_place_id supplied).
        Step 2 ($0.025): Place Details → get all Enterprise+Atmosphere fields."""
        raw_id = known_place_id or self.resolve_place_id(name, city)
        if not raw_id:
            return None
        # Normalise: stored IDs may be bare ('ChIJ...') or full path ('places/ChIJ...')
        resource_name = raw_id if raw_id.startswith("places/") else f"places/{raw_id}"
        return self.fetch_place_details(resource_name)

    def _fetch(self, query):
        """Legacy internal fetch for hotels/restaurants search. Uses search field mask."""
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
                    "X-Goog-FieldMask": (
                        "places.id,places.displayName,places.formattedAddress,"
                        "places.location,places.types,places.businessStatus,"
                        "places.googleMapsUri,places.rating,places.priceLevel,"
                        "places.websiteUri,places.nationalPhoneNumber"
                    ),
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

    def get_cached_hotels(self, city: str) -> list:
        """Return cached Google Places hotels without hitting the API. Empty list if not cached."""
        cached = _db_hotels_get(city)
        return cached if cached else []

    def get_cached_restaurants(self, city: str, cuisine: str = "") -> list:
        """Return cached Google Places restaurants without hitting the API. Empty list if not cached."""
        query_cuisine = cuisine.split(",")[0].strip() if cuisine else ""
        cached = _db_restaurants_get(city, query_cuisine or "all")
        return cached if cached else []

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
