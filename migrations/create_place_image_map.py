"""Normalize place images: replace the denormalized `places.image` column with a
many-to-many join table `place_image_map (place_id, image_id)`.

Steps (order matters — backfill reads `places.image` before it is dropped):
  1. Create `place_image_map` with composite PK (place_id, image_id) and CASCADE
     FKs into `places` and `images`.
  2. Backfill it by matching `places.image` to `images.image_name`.
  3. Drop the now-redundant `places.image` column.

Run from project root:
    venv/bin/python migrations/create_place_image_map.py

Idempotent: table creation uses IF NOT EXISTS, backfill uses INSERT IGNORE, and
the column drop is guarded so re-running is a no-op.
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

    # ── 1. create the join table ──────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS place_image_map (
            place_id INT NOT NULL,
            image_id INT NOT NULL,
            PRIMARY KEY (place_id, image_id),
            INDEX idx_pim_image (image_id),
            CONSTRAINT fk_pim_place FOREIGN KEY (place_id)
                REFERENCES places (id) ON DELETE CASCADE,
            CONSTRAINT fk_pim_image FOREIGN KEY (image_id)
                REFERENCES images (id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    print("Ensured table `place_image_map`")

    # ── 2. backfill from places.image ↔ images.image_name ─────────────────────
    if column_exists(cursor, "places", "image"):
        cursor.execute(
            """INSERT IGNORE INTO place_image_map (place_id, image_id)
               SELECT p.id, i.id
               FROM places p
               JOIN images i ON p.image = i.image_name
               WHERE p.image IS NOT NULL AND p.image <> ''"""
        )
        conn.commit()
        print(f"Backfilled {cursor.rowcount} place_image_map rows")
    else:
        print("places.image already dropped — skipping backfill")

    cursor.execute("SELECT COUNT(*) FROM place_image_map")
    print(f"`place_image_map` now has {cursor.fetchone()[0]} rows")

    # ── 3. drop the redundant column ──────────────────────────────────────────
    if column_exists(cursor, "places", "image"):
        cursor.execute("ALTER TABLE places DROP COLUMN image")
        conn.commit()
        print("Dropped places.image")
    else:
        print("places.image already dropped")

    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
