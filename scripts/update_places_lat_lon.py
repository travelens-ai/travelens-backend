"""
Backfill lat/lon/full_address for rows in the `places` table using the free
OpenStreetMap Nominatim geocoder.

Processes places that don't yet have coordinates (lat IS NULL OR lon IS NULL),
one at a time. For each place it builds a query of "place name, city, state"
(city/state resolved via the cities/states tables) and saves the first match's
latitude, longitude and display_name (as full_address) back to the row.

Nominatim's usage policy asks for at most ~1 request/second and a valid
User-Agent, so the loop sleeps between calls.

Run from project root:
    venv/bin/python scripts/update_places_lat_lon.py            # update 10 (default)
    venv/bin/python scripts/update_places_lat_lon.py 50         # update 50
    venv/bin/python scripts/update_places_lat_lon.py --limit 50 # same as above
"""

import os
import sys
import time

import requests
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "ssl_disabled": True,
    "connection_timeout": 30,
}

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = os.getenv("NOMINATIM_USER_AGENT", "travelens-backend/1.0")
REQUEST_DELAY = 1.1  # seconds between Nominatim calls (be a good citizen)


def parse_limit(argv):
    """Number of places to update. Accepts a bare number or `--limit N`.
    Defaults to 10."""
    args = argv[1:]
    for i, a in enumerate(args):
        if a == "--limit" and i + 1 < len(args):
            return int(args[i + 1])
        if a.isdigit():
            return int(a)
    return 10


def geocode(query: str):
    """Return (lat, lon, full_address) for the first Nominatim match, or None."""
    query = (query or "").strip()
    if not query:
        return None
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            top = results[0]
            return (float(top["lat"]), float(top["lon"]), top.get("display_name", ""))
    except Exception as e:
        print(f"  geocode error for {query!r}: {e}")
    return None


def build_queries(name, city, state):
    """Return a list of query strings from most to least specific.
    More variants = higher OSM hit rate for Indian place names."""
    n = name.strip()
    c = city.strip()
    s = state.strip()
    seen, queries = set(), []
    for q in [
        ", ".join(p for p in [n, c, s, "India"] if p),  # full + India
        ", ".join(p for p in [n, c, "India"] if p),      # name + city + India
        ", ".join(p for p in [n, s, "India"] if p),      # name + state + India
        f"{n} India",                                      # name + India
        n,                                                 # name only
    ]:
        if q and q not in seen:
            seen.add(q)
            queries.append(q)
    return queries


def main():
    limit = parse_limit(sys.argv)
    print(f"Updating lat/lon for up to {limit} place(s) without coordinates...")

    # Fetch rows on a short-lived read connection
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """SELECT p.id, p.name, c.name AS city, s.name AS state
           FROM places p
           LEFT JOIN cities c ON p.city_id = c.id
           LEFT JOIN states s ON c.state_id = s.id
           WHERE p.lat IS NULL OR p.lon IS NULL
           ORDER BY p.id
           LIMIT %s""",
        (limit,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        print("No places need updating. Done.")
        return

    updated = skipped = 0

    for i, row in enumerate(rows, 1):
        name  = str(row.get("name")  or "").strip()
        city  = str(row.get("city")  or "").strip()
        state = str(row.get("state") or "").strip()
        queries = build_queries(name, city, state)
        print(f"[{i}/{len(rows)}] id={row['id']} :: {name}, {city}, {state}")

        result = None
        for q in queries:
            result = geocode(q)
            if result is not None:
                break

        if result is None:
            print("    no match — skipped")
            skipped += 1
        else:
            lat, lon, full_address = result
            # Fresh connection per write — avoids dropped-connection errors on
            # long-running batches where the idle connection times out remotely.
            wconn = mysql.connector.connect(**DB_CONFIG)
            wcursor = wconn.cursor()
            wcursor.execute(
                "UPDATE places SET lat = %s, lon = %s, full_address = %s WHERE id = %s",
                (lat, lon, full_address, row["id"]),
            )
            wconn.commit()
            wcursor.close()
            wconn.close()
            print(f"    -> {lat}, {lon}")
            updated += 1

        if i < len(rows):
            time.sleep(REQUEST_DELAY)

    print(f"\nDone. Updated {updated}, skipped {skipped}.")


if __name__ == "__main__":
    main()
