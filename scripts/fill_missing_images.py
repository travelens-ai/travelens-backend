"""
Fill missing images for places that have no image in DB.

Fetches from Pexels (primary) → Unsplash (fallback), downloads as .webp,
saves to generated_images/, updates places.image in DB.

Run from project root:
    .venv/bin/python3 scripts/fill_missing_images.py          # all missing
    .venv/bin/python3 scripts/fill_missing_images.py --limit 10  # test first N
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


WIKIMEDIA_URL = "https://commons.wikimedia.org/w/api.php"
_SKIP_WORDS = {"map", "logo", "icon", "flag", "svg", "diagram", "coat", "plan", "stamp", "chart"}


_WIKIMEDIA_HEADERS = {
    "User-Agent": "TravelensImageBot/1.0 (travelens-backend; image-fill script)"
}


def fetch_wikimedia_urls(query: str) -> list:
    """Return all valid landscape JPG candidates from Wikimedia Commons."""
    try:
        resp = requests.get(
            WIKIMEDIA_URL,
            headers=_WIKIMEDIA_HEADERS,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": 15,
                "prop": "imageinfo",
                "iiprop": "url|size|mime",
                "format": "json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        candidates = sorted(pages.values(), key=lambda x: x.get("index", 99))
        urls = []
        for page in candidates:
            ii = page.get("imageinfo", [{}])[0]
            mime = ii.get("mime", "")
            url = ii.get("url", "")
            w = ii.get("width", 0)
            h = ii.get("height", 0)
            title = page.get("title", "").lower()
            if mime != "image/jpeg":
                continue
            if w <= h:
                continue
            if any(word in title for word in _SKIP_WORDS):
                continue
            urls.append(url)
        return urls
    except Exception as e:
        print(f"  [Wikimedia] error: {e}")
    return []


def fetch_image_url(query: str) -> str:
    """Try Wikimedia first (exact landmark), fall back to Pexels then Unsplash."""
    # Try each Wikimedia candidate until one downloads successfully
    for url in fetch_wikimedia_urls(query):
        try:
            r = requests.head(url, headers=_WIKIMEDIA_HEADERS, timeout=5)
            if r.status_code == 200:
                return url
        except Exception:
            continue

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
        resp = requests.get(url, headers=_WIKIMEDIA_HEADERS, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        img = img.resize((640, 360))
        filepath = os.path.join(OUTPUT_DIR, filename)
        img.save(filepath, format="WEBP", quality=85, optimize=True)
        return True
    except Exception as e:
        print(f"  [download] error: {e}")
        return False


def main(limit=None):
    conn = mysql.connector.connect(**DB_CONFIG)
    read_cursor = conn.cursor(dictionary=True)
    write_cursor = conn.cursor()

    read_cursor.execute(
        "SELECT p.id, p.name, c.name AS city, s.name AS state, p.type "
        "FROM places p "
        "LEFT JOIN cities c ON p.city_id = c.id "
        "LEFT JOIN states s ON c.state_id = s.id "
        "WHERE p.image IS NULL OR p.image = ''"
    )
    rows = read_cursor.fetchall()
    if limit:
        rows = rows[:limit]
    total = len(rows)
    print(f"Processing {total} places with no image.\n")

    done = 0
    failed = 0

    for i, row in enumerate(rows, 1):
        name = row["name"].title()
        city = (row["city"] or "").title()
        state = (row["state"] or "").title()
        place_type = (row["type"] or "").title()

        # Include type for relevance: "Gavi Ecotourism Pathanamthitta India"
        query = f"{name} {place_type} {city} India".strip()
        parts = [p for p in [name, city, state] if p]
        filename = "_".join(parts).replace(" ", "_") + ".webp"
        filepath = os.path.join(OUTPUT_DIR, filename)

        print(f"[{i}/{total}] {name} ({city}) [{place_type}]", end=" ... ", flush=True)

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
            print(f"saved ({size_kb:.0f} KB)  query: '{query}'")
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
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])
    main(limit=limit)
    
