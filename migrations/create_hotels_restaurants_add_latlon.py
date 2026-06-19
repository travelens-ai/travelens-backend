"""Create `hotels` and `restaurants` tables, and add nullable `lat`/`lon`
columns to `places`, `hotels`, and `restaurants`.

Column layouts mirror the source CSVs (indian_hotels.csv, indian_restaurants.csv)
so the existing recommender field names line up. `lat`/`lon` are nullable and
added with NULL values — they are meant to be backfilled later (e.g. via the
Google Maps geocoding the app already uses for cities).

Run from project root:
    venv/bin/python migrations/create_hotels_restaurants_add_latlon.py

Idempotent: creating tables uses IF NOT EXISTS, and each column add is skipped
if the column already exists.
"""
import os

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


def column_exists(cursor, table, column):
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.columns
           WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s""",
        (table, column),
    )
    return cursor.fetchone()[0] > 0


def add_lat_lon(cursor, table):
    """Add nullable lat/lon to `table` if missing. Values default to NULL."""
    if not column_exists(cursor, table, "lat"):
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN lat DECIMAL(9,6) NULL")
        print(f"Added {table}.lat (NULL)")
    else:
        print(f"{table}.lat already exists")

    if not column_exists(cursor, table, "lon"):
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN lon DECIMAL(9,6) NULL")
        print(f"Added {table}.lon (NULL)")
    else:
        print(f"{table}.lon already exists")


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # ── hotels (mirrors indian_hotels.csv) ────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hotels (
            id INT AUTO_INCREMENT PRIMARY KEY,
            address TEXT,
            area VARCHAR(255),
            city VARCHAR(100),
            state VARCHAR(100),
            country VARCHAR(100),
            hotel_star_rating TINYINT,
            pageurl VARCHAR(1000),
            property_name VARCHAR(255),
            property_type VARCHAR(100),
            site_review_rating DECIMAL(3,1),
            lat DECIMAL(9,6) NULL,
            lon DECIMAL(9,6) NULL,
            INDEX idx_hotels_city (city),
            INDEX idx_hotels_state (state)
        )
    """)
    print("Ensured table `hotels`")

    # ── restaurants (mirrors indian_restaurants.csv) ──────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255),
            location VARCHAR(500),
            locality VARCHAR(255),
            city VARCHAR(100),
            cuisine VARCHAR(500),
            rating DECIMAL(3,1),
            votes INT,
            cost INT,
            lat DECIMAL(9,6) NULL,
            lon DECIMAL(9,6) NULL,
            INDEX idx_restaurants_city (city),
            INDEX idx_restaurants_locality (locality)
        )
    """)
    print("Ensured table `restaurants`")

    conn.commit()

    # ── lat/lon on all three tables (NULL by default) ─────────────────────────
    for table in ("places", "hotels", "restaurants"):
        add_lat_lon(cursor, table)
    conn.commit()

    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
