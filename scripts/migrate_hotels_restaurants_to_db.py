"""
One-time migration: CSV → MySQL `hotels` and `restaurants` tables.

Loads indian_hotels.csv and indian_restaurants.csv into the tables created by
migrations/create_hotels_restaurants_add_latlon.py. The `lat`/`lon` columns are
left NULL (to be backfilled later via geocoding).

Run from project root:
    venv/bin/python3 scripts/migrate_hotels_restaurants_to_db.py
"""

import os
import sys

import pandas as pd
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "193.203.184.43"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "u574280806_travelens"),
    "password": os.getenv("DB_PASSWORD", "Travelens@123"),
    "database": os.getenv("DB_NAME", "u574280806_travelens"),
    "ssl_disabled": True,
    "connection_timeout": 30,
}

HOTELS_CSV = "indian_hotels.csv"
RESTAURANTS_CSV = "indian_restaurants.csv"
BATCH = 500


def str_or_none(v):
    import math
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v).strip()
    return s if s else None


def float_or_none(v):
    try:
        f = float(v)
        import math
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def int_or_none(v):
    try:
        f = float(v)
        import math
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def confirm_reload(cursor, conn, table):
    """Return True if we should proceed to insert. Clears the table on a 'y'."""
    cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
    existing = cursor.fetchone()[0]
    if existing > 0:
        answer = input(f"  `{table}` already has {existing} rows. Re-insert? [y/N] ").strip().lower()
        if answer != "y":
            print(f"  Skipping {table}.")
            return False
        print(f"  Clearing existing {table}...")
        cursor.execute(f"DELETE FROM `{table}`")
        conn.commit()
    return True


def migrate_hotels(cursor, conn):
    print(f"\nLoading {HOTELS_CSV}...")
    df = pd.read_csv(HOTELS_CSV)
    print(f"  {len(df)} rows found.")

    if not confirm_reload(cursor, conn, "hotels"):
        return

    rows = [
        (
            str_or_none(r.get("address")),
            str_or_none(r.get("area")),
            str_or_none(r.get("city")),
            str_or_none(r.get("state")),
            str_or_none(r.get("country")),
            int_or_none(r.get("hotel_star_rating")),
            str_or_none(r.get("pageurl")),
            str_or_none(r.get("property_name")),
            str_or_none(r.get("property_type")),
            float_or_none(r.get("site_review_rating")),
        )
        for _, r in df.iterrows()
    ]

    print("Inserting hotels (batch of 500)...")
    for i in range(0, len(rows), BATCH):
        cursor.executemany(
            """INSERT INTO hotels
               (address, area, city, state, country, hotel_star_rating,
                pageurl, property_name, property_type, site_review_rating)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            rows[i:i + BATCH],
        )
        conn.commit()
        print(f"  Inserted {min(i + BATCH, len(rows))}/{len(rows)}...")
    print(f"Done. {len(rows)} hotels inserted.")


def migrate_restaurants(cursor, conn):
    print(f"\nLoading {RESTAURANTS_CSV}...")
    df = pd.read_csv(RESTAURANTS_CSV)
    print(f"  {len(df)} rows found.")

    if not confirm_reload(cursor, conn, "restaurants"):
        return

    rows = [
        (
            str_or_none(r.get("Name")),
            str_or_none(r.get("Location")),
            str_or_none(r.get("Locality")),
            str_or_none(r.get("City")),
            str_or_none(r.get("Cuisine")),
            float_or_none(r.get("Rating")),
            int_or_none(r.get("Votes")),
            int_or_none(r.get("Cost")),
        )
        for _, r in df.iterrows()
    ]

    print("Inserting restaurants (batch of 500)...")
    for i in range(0, len(rows), BATCH):
        cursor.executemany(
            """INSERT INTO restaurants
               (name, location, locality, city, cuisine, rating, votes, cost)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            rows[i:i + BATCH],
        )
        conn.commit()
        print(f"  Inserted {min(i + BATCH, len(rows))}/{len(rows)}...")
    print(f"Done. {len(rows)} restaurants inserted.")


def main():
    print("Connecting to DB...")
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    print("Connected.")

    migrate_hotels(cursor, conn)
    migrate_restaurants(cursor, conn)

    cursor.close()
    conn.close()
    print("\nAll done.")


if __name__ == "__main__":
    for f in (HOTELS_CSV, RESTAURANTS_CSV):
        if not os.path.exists(f):
            print(f"ERROR: {f} not found. Run from project root.")
            sys.exit(1)
    main()
