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


def main():
    limit = parse_limit(sys.argv)
    print(f"Updating lat/lon for up to {limit} place(s) without coordinates...")

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

    if not rows:
        print("No places need updating. Done.")
        conn.close()
        return

    update_cursor = conn.cursor()
    updated = skipped = 0

    for i, row in enumerate(rows, 1):
        parts = [str(row.get("name") or "").strip(),
                 str(row.get("city") or "").strip(),
                 str(row.get("state") or "").strip()]
        query = ", ".join(p for p in parts if p)
        print(f"[{i}/{len(rows)}] id={row['id']} :: {query}")

        result = geocode(query)
        # Fall back to just the name if the full query found nothing.
        if result is None and parts[0]:
            result = geocode(parts[0])

        if result is None:
            print("    no match — skipped")
            skipped += 1
        else:
            lat, lon, full_address = result
            update_cursor.execute(
                "UPDATE places SET lat = %s, lon = %s, full_address = %s WHERE id = %s",
                (lat, lon, full_address, row["id"]),
            )
            conn.commit()
            print(f"    -> {lat}, {lon}")
            updated += 1

        if i < len(rows):
            time.sleep(REQUEST_DELAY)

    update_cursor.close()
    conn.close()
    print(f"\nDone. Updated {updated}, skipped {skipped}.")


if __name__ == "__main__":
    main()
