"""Add Google rating fields to the `places` table.

New columns:
  google_place_id   — Google's unique place ID (used for Maps deep-link and refreshes)
  google_rating     — Live rating from Google (0.0–5.0)
  google_rating_count — Number of Google reviews
  google_maps_uri   — Direct Google Maps URL; frontend uses this for "View on Google"
  google_synced_at  — Timestamp of last sync (cron skips rows synced within 90 days)

Run from project root:
    venv/bin/python migrations/add_google_rating_fields.py

Idempotent: each column is added only if it does not already exist.
"""
import os

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "ssl_disabled": os.getenv("DB_SSL_DISABLED", "true").lower() == "true",
    "connection_timeout": 30,
}

COLUMNS = [
    ("google_place_id",     "VARCHAR(255) DEFAULT NULL"),
    ("google_rating",       "DECIMAL(3,1) DEFAULT NULL"),
    ("google_rating_count", "INT DEFAULT NULL"),
    ("google_maps_uri",     "VARCHAR(500) DEFAULT NULL"),
    ("google_synced_at",    "TIMESTAMP NULL DEFAULT NULL"),
]


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

    for col_name, col_def in COLUMNS:
        if not column_exists(cursor, "places", col_name):
            cursor.execute(f"ALTER TABLE places ADD COLUMN {col_name} {col_def}")
            conn.commit()
            print(f"Added places.{col_name}")
        else:
            print(f"places.{col_name} already exists — skipping")

    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
