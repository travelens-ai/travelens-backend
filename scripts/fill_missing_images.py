"""
Fill missing images for places that have no image in DB.

Fetches from Pexels (primary) → Unsplash (fallback), downloads as .webp,
saves to generated_images/, updates places.image in DB.

Run from project root:
    .venv/bin/python3 scripts/fill_missing_images.py
"""

import os
import sys
import time
import requests
import mysql.connector
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = "generated_images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

PEXELS_URL = "https://api.pexels.com/v1/search"
UNSPLASH_URL = "https://api.unsplash.com/search/photos"

PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
UNSPLASH_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "ssl_disabled": True,
    "connection_timeout": 30,
}


def fetch_image_url(query: str) -> str:
    """Try Pexels first, fall back to Unsplash. Returns direct image URL or ''."""
    if PEXELS_KEY:
        try:
            resp = requests.get(
                PEXELS_URL,
                headers={"Authorization": PEXELS_KEY},
                params={"query": query, "per_page": 1, "orientation": "landscape"},
                timeout=10,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
            if photos:
                return photos[0].get("src", {}).get("large", "")
        except Exception as e:
            print(f"  [Pexels] error: {e}")

    if UNSPLASH_KEY:
        try:
            resp = requests.get(
                UNSPLASH_URL,
                headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"},
                params={"query": query, "per_page": 1, "orientation": "landscape"},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                return results[0].get("urls", {}).get("regular", "")
        except Exception as e:
            print(f"  [Unsplash] error: {e}")

    return ""


def download_as_webp(url: str, filename: str) -> bool:
    """Download image from URL, convert to webp, save to generated_images/."""
    try:
        from PIL import Image
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        img = img.resize((640, 360))
        filepath = os.path.join(OUTPUT_DIR, filename)
        img.save(filepath, format="WEBP", quality=85, optimize=True)
        return True
    except Exception as e:
        print(f"  [download] error: {e}")
        return False


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    read_cursor = conn.cursor(dictionary=True)
    write_cursor = conn.cursor()

    read_cursor.execute(
        "SELECT id, name, city, state FROM places WHERE image IS NULL OR image = ''"
    )
    rows = read_cursor.fetchall()
    total = len(rows)
    print(f"Found {total} places with no image.\n")

    done = 0
    failed = 0

    for i, row in enumerate(rows, 1):
        name = row["name"].title()
        city = (row["city"] or "").title()
        state = (row["state"] or "").title()

        query = f"{name} {city} India"
        parts = [p for p in [name, city, state] if p]
        filename = "_".join(parts).replace(" ", "_") + ".webp"
        filepath = os.path.join(OUTPUT_DIR, filename)

        print(f"[{i}/{total}] {name} ({city}) ...", end=" ", flush=True)

        # skip if file already exists on disk
        if os.path.exists(filepath):
            write_cursor.execute(
                "UPDATE places SET image = %s WHERE id = %s", (filename, row["id"])
            )
            conn.commit()
            print(f"already on disk, updated DB.")
            done += 1
            continue

        url = fetch_image_url(query)
        if not url:
            print("no image found.")
            failed += 1
            continue

        if download_as_webp(url, filename):
            write_cursor.execute(
                "UPDATE places SET image = %s WHERE id = %s", (filename, row["id"])
            )
            conn.commit()
            size_kb = os.path.getsize(filepath) / 1024
            print(f"saved ({size_kb:.0f} KB)")
            done += 1
        else:
            print("download failed.")
            failed += 1

        time.sleep(0.3)  # be polite to APIs

    read_cursor.close()
    write_cursor.close()
    conn.close()

    print(f"\nDone. {done} images saved, {failed} failed out of {total}.")


if __name__ == "__main__":
    if not PEXELS_KEY and not UNSPLASH_KEY:
        print("ERROR: Set PEXELS_API_KEY or UNSPLASH_ACCESS_KEY in .env")
        sys.exit(1)
    main()
