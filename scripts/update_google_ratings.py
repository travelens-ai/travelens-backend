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
import struct
import sys
import time

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

# Load .env from project root (script lives in scripts/, so go one level up)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# Add src/ to path so we can import the existing GooglePlacesClient
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from integrations.api_integrations import GooglePlacesClient

_SQL_COPT_SS_ACCESS_TOKEN = 1256

STALE_DAYS = 90  # re-fetch places not synced in this many days


def _connect():
    credential = DefaultAzureCredential()
    token = credential.get_token("https://database.windows.net//.default")
    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={os.getenv('AZURE_SQL_SERVER')};"
        f"DATABASE={os.getenv('AZURE_SQL_DATABASE')};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def _rows_as_dicts(cursor) -> list:
    cols = [col[0] for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def get_places(cursor, batch: int, force_refresh: bool) -> list:
    if force_refresh:
        cursor.execute(
            """
            SELECT TOP (?) p.id, p.name, c.name AS city
            FROM places p
            JOIN cities c ON p.city_id = c.id
            """,
            (batch,),
        )
    else:
        cursor.execute(
            """
            SELECT TOP (?) p.id, p.name, c.name AS city
            FROM places p
            JOIN cities c ON p.city_id = c.id
            WHERE p.google_place_id IS NULL
               OR p.google_synced_at < DATEADD(day, -?, GETDATE())
            ORDER BY CASE WHEN p.google_place_id IS NULL THEN 0 ELSE 1 END
            """,
            (batch, STALE_DAYS),
        )
    return _rows_as_dicts(cursor)


def main():
    parser = argparse.ArgumentParser(description="Update Google ratings for places")
    parser.add_argument("--batch", type=int, default=50, help="Max places to process (default 50)")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing to DB")
    parser.add_argument("--force-refresh", action="store_true", help="Re-fetch all places ignoring google_synced_at")
    args = parser.parse_args()

    client = GooglePlacesClient()
    conn = _connect()
    cursor = conn.cursor()

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
                SET google_place_id     = ?,
                    google_rating       = ?,
                    google_rating_count = ?,
                    google_maps_uri     = ?,
                    google_synced_at    = GETDATE(),
                    lat                 = COALESCE(lat, ?),
                    lon                 = COALESCE(lon, ?),
                    website_uri         = ?,
                    phone_number        = ?,
                    opening_hours       = ?,
                    place_types         = ?,
                    business_status     = ?,
                    price_level         = ?,
                    price_range         = ?,
                    timezone            = ?,
                    accessibility       = ?
                WHERE id = ?
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
