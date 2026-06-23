"""
Idempotent migration: add contact/detail fields to the places table.
These columns store data fetched from the Google Places API (Pro + Enterprise
fields, no extra cost since we're already at Enterprise SKU).

Run from project root:
    venv/bin/python migrations/add_place_contact_fields.py
"""

import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host":       os.getenv("DB_HOST"),
    "port":       int(os.getenv("DB_PORT", 3306)),
    "user":       os.getenv("DB_USER"),
    "password":   os.getenv("DB_PASSWORD"),
    "database":   os.getenv("DB_NAME"),
    "ssl_disabled": True,
}

NEW_COLUMNS = [
    ("website_uri",     "ALTER TABLE places ADD COLUMN website_uri VARCHAR(500) DEFAULT NULL"),
    ("phone_number",    "ALTER TABLE places ADD COLUMN phone_number VARCHAR(50) DEFAULT NULL"),
    ("opening_hours",   "ALTER TABLE places ADD COLUMN opening_hours JSON DEFAULT NULL"),
    ("place_types",     "ALTER TABLE places ADD COLUMN place_types VARCHAR(255) DEFAULT NULL"),
    ("business_status", "ALTER TABLE places ADD COLUMN business_status VARCHAR(50) DEFAULT NULL"),
    ("price_level",     "ALTER TABLE places ADD COLUMN price_level VARCHAR(50) DEFAULT NULL"),
    ("price_range",     "ALTER TABLE places ADD COLUMN price_range VARCHAR(100) DEFAULT NULL"),
    ("timezone",        "ALTER TABLE places ADD COLUMN timezone VARCHAR(100) DEFAULT NULL"),
    ("accessibility",   "ALTER TABLE places ADD COLUMN accessibility JSON DEFAULT NULL"),
]


def column_exists(cursor, table, column):
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
        (table, column),
    )
    return cursor.fetchone()[0] > 0


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    added = []
    skipped = []
    for col, ddl in NEW_COLUMNS:
        if column_exists(cursor, "places", col):
            skipped.append(col)
        else:
            cursor.execute(ddl)
            conn.commit()
            added.append(col)

    cursor.close()
    conn.close()

    if added:
        print(f"Added columns: {', '.join(added)}")
    if skipped:
        print(f"Already existed (skipped): {', '.join(skipped)}")
    print("Done.")


if __name__ == "__main__":
    main()
