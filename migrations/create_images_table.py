"""Create an `images` table and populate it with every image referenced by the
`places` table.

Schema:
    id          INT AUTO_INCREMENT PRIMARY KEY
    image_name  VARCHAR(255) UNIQUE   -- the value from places.image (e.g. Foo_City_State.webp)
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP

Run from project root:
    venv/bin/python migrations/create_images_table.py

Idempotent: CREATE TABLE IF NOT EXISTS, and rows are inserted with
INSERT IGNORE on the UNIQUE image_name, so re-running only adds new images.
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


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # ── create table ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INT AUTO_INCREMENT PRIMARY KEY,
            image_name VARCHAR(255) NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    print("Ensured table `images`")

    # ── populate from places.image ────────────────────────────────────────────
    cursor.execute(
        "SELECT DISTINCT image FROM places WHERE image IS NOT NULL AND image <> ''"
    )
    image_rows = [(row[0],) for row in cursor.fetchall()]
    print(f"Found {len(image_rows)} distinct images in `places`")

    cursor.executemany(
        "INSERT IGNORE INTO images (image_name) VALUES (%s)",
        image_rows,
    )
    conn.commit()
    print(f"Inserted {cursor.rowcount} new images (existing ones skipped)")

    cursor.execute("SELECT COUNT(*) FROM images")
    print(f"`images` now has {cursor.fetchone()[0]} rows")

    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
