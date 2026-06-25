"""Pre-warm the Google Places SQLite cache for popular cities.

Reads popular_destination.pkl (top-10 destinations), then calls
search_hotels() and search_restaurants() for each city via GooglePlacesClient.
Results are stored in the SQLite cache at cache/travelens_cache.db — so the
first real user request for these cities skips the 2-5s Google Places API call.

Usage:
    venv/bin/python scripts/warm_places_cache.py
"""
import os
import sys
import pickle

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from integrations.api_integrations import GooglePlacesClient

POPULAR_PKL = os.path.join(os.path.dirname(__file__), "..", "popular_destination.pkl")
CUISINES = ["Indian", ""]  # warm both a generic and cuisine-specific search


def main():
    if not os.path.exists(POPULAR_PKL):
        print("[warm_cache] popular_destination.pkl not found — skipping")
        return

    with open(POPULAR_PKL, "rb") as f:
        popular = pickle.load(f)

    # popular_destination.pkl is a dict {place_name: {..., 'state': ..., ...}}
    # or a DataFrame — handle both
    try:
        import pandas as pd
        if isinstance(popular, pd.DataFrame):
            cities = popular['city'].dropna().unique().tolist() if 'city' in popular.columns else []
        elif isinstance(popular, dict):
            cities = list(popular.keys())
        else:
            cities = []
    except Exception as e:
        print(f"[warm_cache] Could not parse popular_destination.pkl: {e}")
        return

    if not cities:
        print("[warm_cache] No cities found in popular_destination.pkl — skipping")
        return

    client = GooglePlacesClient()
    warmed = 0
    for city in cities:
        city = str(city).strip()
        if not city:
            continue
        try:
            client.search_hotels(city)
            print(f"[warm_cache] hotels: {city}")
            warmed += 1
        except Exception as e:
            print(f"[warm_cache] hotels failed for {city}: {e}")

        for cuisine in CUISINES:
            try:
                client.search_restaurants(city, cuisine)
                print(f"[warm_cache] restaurants ({cuisine or 'any'}): {city}")
                warmed += 1
            except Exception as e:
                print(f"[warm_cache] restaurants failed for {city}/{cuisine}: {e}")

    print(f"[warm_cache] Done — {warmed} cache entries warmed for {len(cities)} cities")


if __name__ == "__main__":
    main()
