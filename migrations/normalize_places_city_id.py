"""Normalize `places`: replace the `city`/`state` name columns with a numeric
`city_id` foreign key into `cities`.

This also gives `cities` a numeric `id` primary key (it previously used `name`
as the PK), keeping `name` as a UNIQUE key.

Run from project root:
    venv/bin/python migrations/normalize_places_city_id.py

Idempotent and guarded: every step is a no-op if already applied. `city_id` is
nullable, so places with no resolvable city (e.g. parks/reserves identified only
by state) are kept with city_id = NULL rather than dropped.
"""
import os

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "193.203.184.43"),
    "port": 3306,
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


def fk_exists(cursor, table, constraint):
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.table_constraints
           WHERE table_schema = DATABASE() AND table_name = %s AND constraint_name = %s""",
        (table, constraint),
    )
    return cursor.fetchone()[0] > 0


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # ── 1. cities.id ─────────────────────────────────────────────────────────
    # Give cities a numeric AUTO_INCREMENT PK; demote name to a UNIQUE key.
    if not column_exists(cursor, "cities", "id"):
        cursor.execute(
            """ALTER TABLE cities
                 DROP PRIMARY KEY,
                 ADD COLUMN id INT AUTO_INCREMENT PRIMARY KEY FIRST,
                 ADD UNIQUE KEY uq_city_name (name)"""
        )
        conn.commit()
        print("Added cities.id PK (name -> UNIQUE)")
    else:
        print("cities.id already exists")

    # ── 2. places.city_id (nullable for backfill) ─────────────────────────────
    if not column_exists(cursor, "places", "city_id"):
        cursor.execute("ALTER TABLE places ADD COLUMN city_id INT NULL AFTER id")
        conn.commit()
        print("Added places.city_id (nullable)")
    else:
        print("places.city_id already exists")

    # ── 3. Backfill city_id from the existing `city` name column ──────────────
    if column_exists(cursor, "places", "city"):
        cursor.execute(
            """UPDATE places p
               JOIN cities c ON p.city = c.name
               SET p.city_id = c.id
               WHERE p.city_id IS NULL"""
        )
        conn.commit()
        print(f"Backfilled city_id for {cursor.rowcount} places")

        # ── 4. Fill gaps: create any cities referenced by places but missing ──
        cursor.execute(
            """INSERT IGNORE INTO cities (name)
               SELECT DISTINCT city FROM places
               WHERE city_id IS NULL AND city IS NOT NULL AND city <> ''"""
        )
        if cursor.rowcount:
            print(f"Inserted {cursor.rowcount} missing cities")
        conn.commit()

        cursor.execute(
            """UPDATE places p
               JOIN cities c ON p.city = c.name
               SET p.city_id = c.id
               WHERE p.city_id IS NULL"""
        )
        conn.commit()
        if cursor.rowcount:
            print(f"Backfilled city_id for {cursor.rowcount} more places")
    else:
        print("places.city already dropped — skipping backfill")

    # Report any places we couldn't resolve. city_id is nullable, so these
    # rows are kept with city_id = NULL (e.g. parks/reserves with no city).
    cursor.execute("SELECT COUNT(*) FROM places WHERE city_id IS NULL")
    null_count = cursor.fetchone()[0]
    if null_count:
        print(f"Note: {null_count} places have no resolvable city — keeping city_id = NULL")

    # ── 5. Constrain: index + FK (city_id stays nullable) ─────────────────────
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.statistics
           WHERE table_schema = DATABASE() AND table_name = 'places'
             AND index_name = 'idx_city_id'"""
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE places ADD INDEX idx_city_id (city_id)")
        conn.commit()
        print("Added index idx_city_id")

    if not fk_exists(cursor, "places", "fk_places_city"):
        cursor.execute(
            """ALTER TABLE places
               ADD CONSTRAINT fk_places_city FOREIGN KEY (city_id)
               REFERENCES cities (id)"""
        )
        conn.commit()
        print("Added FK places.city_id -> cities.id")
    else:
        print("FK fk_places_city already exists")

    # ── 6. Drop the now-redundant name columns ────────────────────────────────
    if column_exists(cursor, "places", "state"):
        cursor.execute("ALTER TABLE places DROP COLUMN state")
        conn.commit()
        print("Dropped places.state")
    if column_exists(cursor, "places", "city"):
        # Dropping `city` also drops its idx_city index automatically.
        cursor.execute("ALTER TABLE places DROP COLUMN city")
        conn.commit()
        print("Dropped places.city")

    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
