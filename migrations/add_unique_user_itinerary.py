"""Enforce one row per (user_id, itinerary_id) in `history` and `favorites`.

`favorites` already ships with UNIQUE KEY `unique_favorite (user_id, itinerary_id)`
(see core/db.py), so this migration mainly fixes `history`, which had no such
constraint and may contain duplicate pairs.

Steps:
  1. De-duplicate `history`: keep the lowest `id` per (user_id, itinerary_id),
     delete the rest. (Required — adding a UNIQUE key fails if dupes exist.)
  2. Add UNIQUE KEY `unique_history (user_id, itinerary_id)` if missing.
  3. Add UNIQUE KEY `unique_favorite (user_id, itinerary_id)` to favorites if a
     fresh/legacy DB happens to lack it (no-op on the standard schema).

Run from project root:
    venv/bin/python migrations/add_unique_user_itinerary.py

Idempotent: dedupe is safe to re-run, and each index is added only if absent.
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


def index_exists(cursor, table, index_name):
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.statistics
           WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s""",
        (table, index_name),
    )
    return cursor.fetchone()[0] > 0


def dedupe(cursor, conn, table):
    """Delete duplicate (user_id, itinerary_id) rows, keeping the lowest id."""
    cursor.execute(
        f"""DELETE t1 FROM {table} t1
            JOIN {table} t2
              ON t1.user_id = t2.user_id
             AND t1.itinerary_id = t2.itinerary_id
             AND t1.id > t2.id"""
    )
    conn.commit()
    print(f"  Removed {cursor.rowcount} duplicate rows from {table}")


def add_unique(cursor, conn, table, index_name):
    if index_exists(cursor, table, index_name):
        print(f"  {table}.{index_name} already exists")
        return
    cursor.execute(
        f"ALTER TABLE {table} ADD UNIQUE KEY {index_name} (user_id, itinerary_id)"
    )
    conn.commit()
    print(f"  Added UNIQUE KEY {index_name} on {table} (user_id, itinerary_id)")


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    print("history:")
    dedupe(cursor, conn, "history")
    add_unique(cursor, conn, "history", "unique_history")

    print("favorites:")
    dedupe(cursor, conn, "favorites")
    add_unique(cursor, conn, "favorites", "unique_favorite")

    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
