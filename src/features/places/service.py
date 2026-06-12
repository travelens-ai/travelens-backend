import math
import os
import pickle
import time
import threading

import pandas as pd
import requests as http_requests

_city_coords_cache = {}
_city_coords_loaded = False


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def is_coords_loaded():
    return _city_coords_loaded


def load_city_coords():
    def _do_load():
        global _city_coords_cache, _city_coords_loaded
        cache_file = "city_coords.pkl"
        if os.path.exists(cache_file):
            with open(cache_file, "rb") as f:
                _city_coords_cache = pickle.load(f)
            _city_coords_loaded = True
            print(f"Loaded {len(_city_coords_cache)} city coordinates from cache.")
            return

        places_df = pd.read_csv("indian_travel_places.csv")
        cities = places_df["city"].unique()
        for city in cities:
            try:
                resp = http_requests.get(
                    f"https://nominatim.openstreetmap.org/search?q={city},India&format=json&limit=1",
                    headers={"User-Agent": "Travelens/1.0"},
                    timeout=5,
                )
                if resp.status_code == 200 and resp.json():
                    data = resp.json()[0]
                    _city_coords_cache[city] = (float(data["lat"]), float(data["lon"]))
                time.sleep(1)
            except Exception:
                pass

        with open(cache_file, "wb") as f:
            pickle.dump(_city_coords_cache, f)
        _city_coords_loaded = True
        print(f"Geocoded and cached {len(_city_coords_cache)} cities.")

    threading.Thread(target=_do_load, daemon=True).start()


def query_popular():
    from features.itinerary.service import recommender
    return recommender.get_popular_destination() or []


def query_trending():
    places_df = pd.read_csv("indian_travel_places.csv")
    places_df["no of rating"] = pd.to_numeric(places_df["no of rating"], errors="coerce").fillna(0)
    trending_df = places_df.nlargest(10, "no of rating")
    return trending_df.fillna("").to_dict(orient="records")


def query_nearby(lat, lon):
    places_df = pd.read_csv("indian_travel_places.csv")
    places_df["rating"] = pd.to_numeric(places_df["rating"], errors="coerce").fillna(0)

    nearby_with_dist = []
    for _, row in places_df.iterrows():
        city = row["city"]
        if city in _city_coords_cache:
            dist = haversine(lat, lon, _city_coords_cache[city][0], _city_coords_cache[city][1])
            nearby_with_dist.append((dist, row.fillna("").to_dict()))

    nearby_with_dist.sort(key=lambda x: x[0])
    return [place for _, place in nearby_with_dist[:10]]


def query_weekend(lat, lon):
    places_df = pd.read_csv("indian_travel_places.csv")
    places_df["rating"] = pd.to_numeric(places_df["rating"], errors="coerce").fillna(0)

    nearby_with_dist = []
    for _, row in places_df.iterrows():
        city = row["city"]
        if city in _city_coords_cache:
            dist = haversine(lat, lon, _city_coords_cache[city][0], _city_coords_cache[city][1])
            nearby_with_dist.append((dist, row.fillna("").to_dict()))

    weekend_candidates = [(d, p) for d, p in nearby_with_dist if d <= 300]
    weekend_candidates.sort(key=lambda x: float(x[1].get("rating", 0) or 0), reverse=True)
    return [place for _, place in weekend_candidates[:10]]
