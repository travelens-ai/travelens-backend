"""Quick accuracy check for restaurant food_type + cuisine filters.

Run from project root:
    venv/bin/python scripts/test_restaurant_accuracy.py

Prints meal slots from 4 test itineraries so you can visually verify:
- Non-Veg filter (no pure-veg restaurants slipping through)
- Veg filter (no non-veg restaurants slipping through)
- New cuisine options (Chinese, Seafood) returning real DB data
"""
import hashlib
import hmac
import json
import os
import base64
import time
import sys

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:4000")
SECRET = os.getenv("DEVICE_JWT_SECRET", "OQ2Igc1oi3iAHUdUSjRE4h3UadqfNnC2iVZm0i7uLQHsEQpZ05oEaApZ_0_Jw-0a")


def _device_token():
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    pl = base64.urlsafe_b64encode(json.dumps({"device_id": "test-script", "iat": int(time.time())}).encode()).decode().rstrip("=")
    sig = hmac.HMAC(SECRET.encode(), f"{header}.{pl}".encode(), hashlib.sha256).hexdigest()
    return f"{header}.{pl}.{sig}"


TESTS = [
    {
        "label": "Goa | Non-Veg | Seafood",
        "body": {
            "preferred_activities": ["Beach", "Sightseeing"],
            "places_of_interest": ["Goa"],
            "number_of_people": 2,
            "travel_group_type": "couples",
            "food_preferences": ["Seafood"],
            "food_type": "Non-Veg",
            "user_location": "Mumbai",
            "current_month": "August",
            "trip_type": "Beach",
            "trip_duration": 2,
            "budget": "20000",
            "hotel_preference": "mid",
        },
    },
    {
        "label": "Bangalore | Veg | South Indian",
        "body": {
            "preferred_activities": ["Sightseeing", "Culture"],
            "places_of_interest": ["Bangalore"],
            "number_of_people": 2,
            "travel_group_type": "friends",
            "food_preferences": ["South Indian"],
            "food_type": "Veg",
            "user_location": "Delhi",
            "current_month": "August",
            "trip_type": "City",
            "trip_duration": 2,
            "budget": "15000",
            "hotel_preference": "budget",
        },
    },
    {
        "label": "Mumbai | Non-Veg | Chinese",
        "body": {
            "preferred_activities": ["Sightseeing", "Street Food"],
            "places_of_interest": ["Mumbai"],
            "number_of_people": 3,
            "travel_group_type": "friends",
            "food_preferences": ["Chinese"],
            "food_type": "Non-Veg",
            "user_location": "Delhi",
            "current_month": "August",
            "trip_type": "City",
            "trip_duration": 2,
            "budget": "25000",
            "hotel_preference": "mid",
        },
    },
    {
        "label": "Delhi | Veg | North Indian",
        "body": {
            "preferred_activities": ["Heritage", "Culture"],
            "places_of_interest": ["Delhi"],
            "number_of_people": 4,
            "travel_group_type": "family-with-children",
            "food_preferences": ["North Indian"],
            "food_type": "Veg",
            "user_location": "Bangalore",
            "current_month": "August",
            "trip_type": "Heritage",
            "trip_duration": 3,
            "budget": "30000",
            "hotel_preference": "mid",
        },
    },
]


def run():
    token = _device_token()
    headers = {"Content-Type": "application/json", "X-Device-Token": token}

    for test in TESTS:
        print(f"\n{'='*60}")
        print(f"TEST: {test['label']}")
        print(f"{'='*60}")
        try:
            resp = requests.post(f"{BASE_URL}/generate-itinerary", json=test["body"], headers=headers, timeout=120)
            data = resp.json()
            itin = data.get("data", {}).get("detailed_itinerary", {})
            if not itin:
                print(f"  !! ERROR: {data.get('message', str(data)[:200])}")
                continue

            meals = []
            for day in itin.get("itinerary", []):
                for item in day.get("timeline", []):
                    if item.get("type") == "meal":
                        meals.append((day.get("day"), item.get("slot"), item.get("name"), item.get("cuisine"), item.get("approx_cost"), item.get("rating")))

            print(f"  Meals in timeline ({len(meals)} total):")
            for d, slot, name, cuisine, cost, rating in meals:
                print(f"    Day{d} [{slot:9}] {(name or '')[:35]:<35} | {(cuisine or '')[:30]:<30} | {cost or '':<15} | ⭐{rating}")

        except Exception as e:
            print(f"  !! EXCEPTION: {e}")

    print(f"\n{'='*60}")
    print("Done. Verify:")
    print("  - Veg tests: NO 'non-veg' only restaurants (look for names like 'Chicken', 'Biryani House')")
    print("  - Non-Veg tests: should include meat/seafood options")
    print("  - Chinese/Seafood tests: should show relevant cuisine in the Cuisine column")
    print(f"{'='*60}")


if __name__ == "__main__":
    run()
