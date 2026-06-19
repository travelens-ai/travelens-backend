"""Add a `full_address` column to places, hotels and restaurants.

lat/lon already exist on these tables; this adds full_address (the OSM
display_name) so a geocoded entity's human-readable address can be stored
alongside its coordinates.

Run from project root:
    venv/bin/python migrations/add_full_address_columns.py

Idempotent: each column is added only if it doesn't already exist.
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

TABLES = ["places", "hotels", "restaurants"]


def column_exists(cursor, table, column):
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.columns
           WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s""",
        (table, column),
    )
    return cursor.fetchone()[0] > 0


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    for table in TABLES:
        if not column_exists(cursor, table, "full_address"):
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN full_address TEXT")
            conn.commit()
            print(f"Added {table}.full_address")
        else:
            print(f"{table}.full_address already exists")

    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
