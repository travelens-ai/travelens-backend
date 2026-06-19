"""Add a `created_at` timestamp column to the `places` table.

Existing rows get the current time as their created_at (MySQL/MariaDB backfills
the DEFAULT for existing rows when adding a NOT NULL DEFAULT CURRENT_TIMESTAMP
column). New rows default to the insert time.

Run from project root:
    venv/bin/python migrations/add_created_at_to_places.py

Idempotent: the column is added only if it doesn't already exist.
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


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    if not column_exists(cursor, "places", "created_at"):
        cursor.execute(
            "ALTER TABLE places ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )
        conn.commit()
        print("Added places.created_at")
    else:
        print("places.created_at already exists")

    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
