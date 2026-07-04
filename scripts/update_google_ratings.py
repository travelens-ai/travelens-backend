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
            SELECT TOP (?) p.id, p.name, c.name AS city, p.google_place_id
            FROM places p
            JOIN cities c ON p.city_id = c.id
            """,
            (batch,),
        )
    else:
        cursor.execute(
            """
            SELECT TOP (?) p.id, p.name, c.name AS city, p.google_place_id
            FROM places p
            JOIN cities c ON p.city_id = c.id
            WHERE p.google_place_id IS NOT NULL
              AND (p.google_synced_at IS NULL
                   OR p.google_synced_at < DATEADD(day, -?, GETDATE()))
            ORDER BY p.google_synced_at ASC
            """,
            (batch, STALE_DAYS),
        )
    return _rows_as_dicts(cursor)


def resolve_ids_only(cursor, conn, client, batch: int, dry_run: bool):
    """Pass 1 (free): resolve google_place_id for places that don't have one yet."""
    cursor.execute(
        "SELECT TOP (?) p.id, p.name, c.name AS city FROM places p JOIN cities c ON p.city_id = c.id WHERE p.google_place_id IS NULL",
        (batch,),
    )
    places = _rows_as_dicts(cursor)
    print(f"[update_google_ratings] resolve-ids: {len(places)} place(s) missing google_place_id (batch={batch}, dry_run={dry_run})")

    resolved = 0
    skipped = 0
    for place in places:
        place_id = place["id"]
        name = place["name"]
        city = place["city"] or ""
        resource_name = client.resolve_place_id(name, city)
        if not resource_name:
            print(f"  SKIP  [{place_id}] {name}, {city} — not found")
            skipped += 1
            continue
        bare_id = resource_name.replace("places/", "") if resource_name.startswith("places/") else resource_name
        tag = "DRY " if dry_run else ""
        print(f"  {tag}RESOLVE [{place_id}] {name}, {city}  →  {bare_id}")
        if not dry_run:
            cursor.execute("UPDATE places SET google_place_id = ? WHERE id = ?", (bare_id, place_id))
            conn.commit()
        resolved += 1
        time.sleep(0.1)

    print(f"\n[update_google_ratings] resolve-ids done — resolved: {resolved}, skipped: {skipped}"
          + (" [DRY RUN]" if dry_run else ""))


def main():
    parser = argparse.ArgumentParser(description="Update Google ratings for places")
    parser.add_argument("--batch", type=int, default=50, help="Max places to process (default 50)")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing to DB")
    parser.add_argument("--force-refresh", action="store_true", help="Re-fetch all places ignoring google_synced_at")
    parser.add_argument("--resolve-ids", action="store_true", help="Pass 1 (free): only resolve google_place_id for places missing it")
    args = parser.parse_args()

    client = GooglePlacesClient()
    conn = _connect()
    cursor = conn.cursor()

    if args.resolve_ids:
        resolve_ids_only(cursor, conn, client, args.batch, args.dry_run)
        cursor.close()
        conn.close()
        return

    places = get_places(cursor, args.batch, args.force_refresh)
    print(f"[update_google_ratings] {len(places)} place(s) to process (batch={args.batch}, dry_run={args.dry_run})")

    updated = 0
    skipped = 0

    for place in places:
        place_id = place["id"]
        name = place["name"]
        city = place["city"] or ""

        gid = place["google_place_id"]
        resource_name = gid if gid.startswith("places/") else f"places/{gid}"
        result = client.fetch_place_details(resource_name)

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

        photo_refs = result.get("google_photo_refs")
        reviews = result.get("google_reviews")

        tag = "DRY " if args.dry_run else ""
        print(f"  {tag}UPDATE [{place_id}] {name}, {city}")
        print(f"    rating={result['google_rating']}  count={result['google_rating_count']}")
        print(f"    display_name={result.get('display_name') or '-'}")
        print(f"    primary_type={result.get('primary_type') or '-'}  primary_type_name={result.get('primary_type_name') or '-'}")
        print(f"    types={result.get('place_types') or '-'}  status={result.get('business_status') or '-'}")
        print(f"    phone={result.get('phone_number') or '-'}  intl={result.get('international_phone_number') or '-'}")
        print(f"    website={result.get('website_uri') or '-'}")
        print(f"    short_address={result.get('short_formatted_address') or '-'}")
        print(f"    full_address={result.get('full_address') or '-'}")
        print(f"    lat={result.get('lat')}  lon={result.get('lon')}")
        print(f"    timezone={result.get('timezone') or '-'}  utc_offset={result.get('utc_offset_minutes') or '-'}")
        print(f"    price_level={result.get('price_level') or '-'}  price_range={result.get('price_range') or '-'}")
        print(f"    editorial={result.get('editorial_summary') or '-'}")
        print(f"    review_summary={result.get('review_summary') or '-'}")
        print(f"    generative_summary={result.get('generative_summary') or '-'}")
        print(f"    neighborhood_summary={result.get('neighborhood_summary') or '-'}")
        print(f"    photos={len(photo_refs) if photo_refs else 0} refs  reviews={len(reviews) if reviews else 0}")
        print(f"    accessibility={result.get('accessibility') or '-'}")
        print(f"    parking={result.get('parking_options') or '-'}")
        print(f"    payment={result.get('payment_options') or '-'}")
        _bool = lambda v: '-' if v is None else str(v)
        print(f"    allows_dogs={_bool(result.get('allows_dogs'))}  outdoor_seating={_bool(result.get('outdoor_seating'))}  reservable={_bool(result.get('reservable'))}  restroom={_bool(result.get('restroom'))}")
        print(f"    good_for_children={_bool(result.get('good_for_children'))}  good_for_groups={_bool(result.get('good_for_groups'))}  good_for_watching_sports={_bool(result.get('good_for_watching_sports'))}")
        print(f"    live_music={_bool(result.get('live_music'))}  menu_for_children={_bool(result.get('menu_for_children'))}")
        print(f"    serves: breakfast={_bool(result.get('serves_breakfast'))} lunch={_bool(result.get('serves_lunch'))} dinner={_bool(result.get('serves_dinner'))} brunch={_bool(result.get('serves_brunch'))}")
        print(f"    serves: coffee={_bool(result.get('serves_coffee'))} beer={_bool(result.get('serves_beer'))} wine={_bool(result.get('serves_wine'))} cocktails={_bool(result.get('serves_cocktails'))} dessert={_bool(result.get('serves_dessert'))}")
        print(f"    serves: vegetarian={_bool(result.get('serves_vegetarian_food'))}  takeout={_bool(result.get('takeout'))}  delivery={_bool(result.get('delivery'))}  dine_in={_bool(result.get('dine_in'))}  curbside={_bool(result.get('curbside_pickup'))}")
        if oh:
            print("    hours:")
            for line in oh:
                print(f"      {_clean(line)}")
        else:
            print("    hours: -")

        if not args.dry_run:
            def _j(v):
                return _json.dumps(v) if v else None

            cursor.execute(
                """
                UPDATE places
                SET google_place_id              = ?,
                    resource_name                = ?,
                    google_rating                = ?,
                    google_rating_count          = ?,
                    google_maps_uri              = ?,
                    maps_links                   = ?,
                    google_synced_at             = GETDATE(),
                    display_name                 = ?,
                    primary_type                 = ?,
                    primary_type_name            = ?,
                    icon_mask_base_uri           = ?,
                    icon_background_color        = ?,
                    lat                          = ?,
                    lon                          = ?,
                    full_address                 = ?,
                    short_formatted_address      = ?,
                    adr_format_address           = ?,
                    address_components           = ?,
                    address_descriptor           = ?,
                    plus_code                    = ?,
                    viewport                     = ?,
                    place_types                  = ?,
                    business_status              = ?,
                    timezone                     = ?,
                    utc_offset_minutes           = ?,
                    accessibility                = ?,
                    containing_places            = ?,
                    sub_destinations             = ?,
                    is_service_area_only         = ?,
                    moved_place_id               = ?,
                    opening_date                 = ?,
                    website_uri                  = ?,
                    phone_number                 = ?,
                    international_phone_number   = ?,
                    opening_hours                = ?,
                    current_opening_hours        = ?,
                    secondary_hours              = ?,
                    current_secondary_hours      = ?,
                    price_level                  = ?,
                    price_range                  = ?,
                    attributions                 = ?,
                    google_photo_refs            = ?,
                    google_reviews               = ?,
                    review_summary               = ?,
                    editorial_summary            = ?,
                    generative_summary           = ?,
                    neighborhood_summary         = ?,
                    ev_charge_options            = ?,
                    ev_summary                   = ?,
                    fuel_options                 = ?,
                    allows_dogs                  = ?,
                    curbside_pickup              = ?,
                    delivery                     = ?,
                    dine_in                      = ?,
                    good_for_children            = ?,
                    good_for_groups              = ?,
                    good_for_watching_sports     = ?,
                    live_music                   = ?,
                    menu_for_children            = ?,
                    outdoor_seating              = ?,
                    parking_options              = ?,
                    payment_options              = ?,
                    reservable                   = ?,
                    restroom                     = ?,
                    serves_beer                  = ?,
                    serves_breakfast             = ?,
                    serves_brunch                = ?,
                    serves_cocktails             = ?,
                    serves_coffee                = ?,
                    serves_dessert               = ?,
                    serves_dinner                = ?,
                    serves_lunch                 = ?,
                    serves_vegetarian_food       = ?,
                    serves_wine                  = ?,
                    takeout                      = ?
                WHERE id = ?
                """,
                (
                    result["google_place_id"],
                    result.get("resource_name"),
                    result["google_rating"],
                    result["google_rating_count"],
                    result["google_maps_uri"],
                    _j(result.get("maps_links")),
                    result.get("display_name"),
                    result.get("primary_type"),
                    result.get("primary_type_name"),
                    result.get("icon_mask_base_uri"),
                    result.get("icon_background_color"),
                    result.get("lat"),
                    result.get("lon"),
                    result.get("full_address"),
                    result.get("short_formatted_address"),
                    result.get("adr_format_address"),
                    _j(result.get("address_components")),
                    _j(result.get("address_descriptor")),
                    _j(result.get("plus_code")),
                    _j(result.get("viewport")),
                    result.get("place_types"),
                    result.get("business_status"),
                    result.get("timezone"),
                    result.get("utc_offset_minutes"),
                    _j(result.get("accessibility")),
                    _j(result.get("containing_places")),
                    _j(result.get("sub_destinations")),
                    result.get("is_service_area_only"),
                    result.get("moved_place_id"),
                    result.get("opening_date"),
                    result.get("website_uri"),
                    result.get("phone_number"),
                    result.get("international_phone_number"),
                    _j(oh),
                    _j(result.get("current_opening_hours")),
                    _j(result.get("secondary_hours")),
                    _j(result.get("current_secondary_hours")),
                    result.get("price_level"),
                    result.get("price_range"),
                    _j(result.get("attributions")),
                    _j(photo_refs),
                    _j(reviews),
                    result.get("review_summary"),
                    result.get("editorial_summary"),
                    result.get("generative_summary"),
                    result.get("neighborhood_summary"),
                    _j(result.get("ev_charge_options")),
                    result.get("ev_summary"),
                    _j(result.get("fuel_options")),
                    result.get("allows_dogs"),
                    result.get("curbside_pickup"),
                    result.get("delivery"),
                    result.get("dine_in"),
                    result.get("good_for_children"),
                    result.get("good_for_groups"),
                    result.get("good_for_watching_sports"),
                    result.get("live_music"),
                    result.get("menu_for_children"),
                    result.get("outdoor_seating"),
                    _j(result.get("parking_options")),
                    _j(result.get("payment_options")),
                    result.get("reservable"),
                    result.get("restroom"),
                    result.get("serves_beer"),
                    result.get("serves_breakfast"),
                    result.get("serves_brunch"),
                    result.get("serves_cocktails"),
                    result.get("serves_coffee"),
                    result.get("serves_dessert"),
                    result.get("serves_dinner"),
                    result.get("serves_lunch"),
                    result.get("serves_vegetarian_food"),
                    result.get("serves_wine"),
                    result.get("takeout"),
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
