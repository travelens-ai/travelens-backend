"""Fetch live Google ratings for places and store them in the DB.

Each run processes up to --batch places (default 50), prioritising rows where
google_place_id is NULL (not yet resolved) over rows that are simply stale.

Cost: ~$0.032 per place (Essentials SKU Text Search). At 50/day that is ~$1.60/day,
well within the $200/month Google free credit.

Usage:
    venv/bin/python scripts/update_google_ratings.py              # process 50
    venv/bin/python scripts/update_google_ratings.py --batch 10   # smaller test
    venv/bin/python scripts/update_google_ratings.py --batch 3 --dry-run
    venv/bin/python scripts/update_google_ratings.py --force-refresh  # ignore synced_at
"""
import argparse
import os
import sys
import time

import mysql.connector
from dotenv import load_dotenv

# Load .env from project root (script lives in scripts/, so go one level up)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# Add src/ to path so we can import the existing GooglePlacesClient
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from integrations.api_integrations import GooglePlacesClient

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "ssl_disabled": os.getenv("DB_SSL_DISABLED", "true").lower() == "true",
    "connection_timeout": 30,
}

STALE_DAYS = 90  # re-fetch places not synced in this many days


def get_places(cursor, batch: int, force_refresh: bool) -> list:
    if force_refresh:
        cursor.execute(
            """
            SELECT p.id, p.name, c.name AS city
            FROM places p
            JOIN cities c ON p.city_id = c.id
            LIMIT %s
            """,
            (batch,),
        )
    else:
        cursor.execute(
            """
            SELECT p.id, p.name, c.name AS city
            FROM places p
            JOIN cities c ON p.city_id = c.id
            WHERE p.google_place_id IS NULL
               OR p.google_synced_at < DATE_SUB(NOW(), INTERVAL %s DAY)
            ORDER BY p.google_place_id IS NULL DESC
            LIMIT %s
            """,
            (STALE_DAYS, batch),
        )
    return cursor.fetchall()


def main():
    parser = argparse.ArgumentParser(description="Update Google ratings for places")
    parser.add_argument("--batch", type=int, default=50, help="Max places to process (default 50)")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing to DB")
    parser.add_argument("--force-refresh", action="store_true", help="Re-fetch all places ignoring google_synced_at")
    args = parser.parse_args()

    client = GooglePlacesClient()
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    places = get_places(cursor, args.batch, args.force_refresh)
    print(f"[update_google_ratings] {len(places)} place(s) to process (batch={args.batch}, dry_run={args.dry_run})")

    updated = 0
    skipped = 0

    for place in places:
        place_id = place["id"]
        name = place["name"]
        city = place["city"] or ""

        result = client.resolve_place(name, city)

        if result is None:
            print(f"  SKIP  [{place_id}] {name}, {city} — no Google result")
            skipped += 1
            continue

        import json as _json
        import unicodedata as _ud
        oh = result.get("opening_hours")
        acc = result.get("accessibility")

        def _clean(s):
            return "".join(
                " " if _ud.category(c) in ("Zs", "Cf") else
                "-" if _ud.category(c) == "Pd" else c
                for c in s
            )

        tag = "DRY " if args.dry_run else ""
        print(f"  {tag}UPDATE [{place_id}] {name}, {city}")
        print(f"    rating={result['google_rating']}  count={result['google_rating_count']}")
        print(f"    phone={result.get('phone_number') or '-'}  website={result.get('website_uri') or '-'}")
        print(f"    types={result.get('place_types') or '-'}  status={result.get('business_status') or '-'}")
        print(f"    lat={result.get('lat')}  lon={result.get('lon')}")
        if oh:
            print("    hours:")
            for line in oh:
                print(f"      {_clean(line)}")
        else:
            print("    hours: -")

        if not args.dry_run:
            cursor.execute(
                """
                UPDATE places
                SET google_place_id     = %s,
                    google_rating       = %s,
                    google_rating_count = %s,
                    google_maps_uri     = %s,
                    google_synced_at    = NOW(),
                    lat                 = COALESCE(lat, %s),
                    lon                 = COALESCE(lon, %s),
                    website_uri         = %s,
                    phone_number        = %s,
                    opening_hours       = %s,
                    place_types         = %s,
                    business_status     = %s,
                    price_level         = %s,
                    price_range         = %s,
                    timezone            = %s,
                    accessibility       = %s
                WHERE id = %s
                """,
                (
                    result["google_place_id"],
                    result["google_rating"],
                    result["google_rating_count"],
                    result["google_maps_uri"],
                    result.get("lat"),
                    result.get("lon"),
                    result.get("website_uri"),
                    result.get("phone_number"),
                    _json.dumps(oh) if oh else None,
                    result.get("place_types"),
                    result.get("business_status"),
                    result.get("price_level"),
                    result.get("price_range"),
                    result.get("timezone"),
                    _json.dumps(acc) if acc else None,
                    place_id,
                ),
            )
            conn.commit()

        updated += 1
        time.sleep(0.2)  # gentle rate limiting

    cursor.close()
    conn.close()

    print(
        f"\n[update_google_ratings] Done — updated: {updated}, skipped (no result): {skipped}"
        + (" [DRY RUN — no DB writes]" if args.dry_run else "")
    )


if __name__ == "__main__":
    main()
