"""
Fill missing images for places that have no entry in place_image_map.

For each such place, fetches up to 5 images from:
  Wikimedia Commons (primary) → Pexels → Unsplash (fallbacks)

Each image is:
  1. Downloaded and converted to .webp
  2. Uploaded to CDN via https://travelens.in/app/upload.php
  3. Inserted into `images` table (filename only)
  4. Linked in `place_image_map`

Run from project root:
    .venv/bin/python3 scripts/fill_missing_images.py          # all missing
    .venv/bin/python3 scripts/fill_missing_images.py --limit 10  # test first N
"""

import os
import re
import struct
import sys
import time
import requests
import pyodbc
from azure.identity import DefaultAzureCredential
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = "generated_images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

PEXELS_URL = "https://api.pexels.com/v1/search"
UNSPLASH_URL = "https://api.unsplash.com/search/photos"
CDN_UPLOAD_URL = "https://travelens.in/app/upload.php"

PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
UNSPLASH_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

_SQL_COPT_SS_ACCESS_TOKEN = 1256


def _connect():
    credential = DefaultAzureCredential()
    token = credential.get_token("https://database.windows.net//.default")
    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={os.getenv('AZURE_SQL_SERVER')};"
        f"DATABASE={os.getenv('AZURE_SQL_DATABASE')};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    )
    conn = pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})
    conn.autocommit = False
    return conn

WIKIMEDIA_URL = "https://commons.wikimedia.org/w/api.php"
_SKIP_WORDS = {"map", "logo", "icon", "flag", "svg", "diagram", "coat", "plan", "stamp", "chart"}
_WIKIMEDIA_HEADERS = {
    "User-Agent": "TravelensImageBot/1.0 (travelens-backend; image-fill script)"
}


def fetch_wikimedia_urls(query: str, count: int) -> list:
    """Return up to `count` valid landscape JPG candidates from Wikimedia Commons."""
    try:
        resp = requests.get(
            WIKIMEDIA_URL,
            headers=_WIKIMEDIA_HEADERS,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": count * 3,
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
            if len(urls) >= count:
                break
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


def fetch_image_urls(query: str, count: int = 2) -> list:
    """Collect up to `count` image URLs from Wikimedia → Pexels → Unsplash."""
    urls = []

    urls.extend(fetch_wikimedia_urls(query, count))

    if len(urls) < count and PEXELS_KEY:
        needed = count - len(urls)
        try:
            resp = requests.get(
                PEXELS_URL,
                headers={"Authorization": PEXELS_KEY},
                params={"query": query, "per_page": needed, "orientation": "landscape"},
                timeout=10,
            )
            resp.raise_for_status()
            for photo in resp.json().get("photos", []):
                url = photo.get("src", {}).get("large", "")
                if url and url not in urls:
                    urls.append(url)
        except Exception as e:
            print(f"  [Pexels] error: {e}")

    if len(urls) < count and UNSPLASH_KEY:
        needed = count - len(urls)
        try:
            resp = requests.get(
                UNSPLASH_URL,
                headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"},
                params={"query": query, "per_page": needed, "orientation": "landscape"},
                timeout=10,
            )
            resp.raise_for_status()
            for result in resp.json().get("results", []):
                url = result.get("urls", {}).get("regular", "")
                if url and url not in urls:
                    urls.append(url)
        except Exception as e:
            print(f"  [Unsplash] error: {e}")

    return urls[:count]


def download_as_webp(url: str, filename: str) -> bool:
    """Download image from URL, convert to webp, save to generated_images/."""
    try:
        from PIL import Image
        time.sleep(3.0)  # respect Wikimedia rate limits
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


def upload_to_cdn(filepath: str) -> str:
    """Upload file to CDN, return filename only (not full URL)."""
    try:
        with open(filepath, "rb") as f:
            resp = requests.post(CDN_UPLOAD_URL, files={"file": f}, timeout=30)
        if resp.status_code == 200:
            path = resp.json().get("path", "")
            return os.path.basename(path)
    except Exception as e:
        print(f"  [CDN upload] error: {e}")
    return ""


def link_place_image(cursor, conn, place_id: int, image_name: str):
    """Insert image_name into `images`, link to place via `place_image_map`."""
    # Upsert image row and retrieve its id
    cursor.execute(
        """
        MERGE images AS tgt
        USING (SELECT ? AS image_name) AS src ON tgt.image_name = src.image_name
        WHEN NOT MATCHED THEN INSERT (image_name) VALUES (src.image_name);
        """,
        (image_name,),
    )
    cursor.execute("SELECT id FROM images WHERE image_name = ?", (image_name,))
    row = cursor.fetchone()
    image_id = row[0] if row else None
    if image_id is None:
        return
    cursor.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM place_image_map WHERE place_id = ? AND image_id = ?)
            INSERT INTO place_image_map (place_id, image_id) VALUES (?, ?)
        """,
        (place_id, image_id, place_id, image_id),
    )
    conn.commit()


def main(limit=None):
    conn = _connect()
    read_cursor = conn.cursor()
    write_cursor = conn.cursor()

    read_cursor.execute(
        "SELECT p.id, p.name, c.name AS city, s.name AS state, p.type, "
        "COUNT(pim.image_id) AS img_count "
        "FROM places p "
        "LEFT JOIN cities c ON p.city_id = c.id "
        "LEFT JOIN states s ON c.state_id = s.id "
        "LEFT JOIN place_image_map pim ON pim.place_id = p.id "
        "GROUP BY p.id, p.name, c.name, s.name, p.type "
        "HAVING COUNT(pim.image_id) < 2"
    )
    cols = [col[0] for col in read_cursor.description]
    rows = [dict(zip(cols, row)) for row in read_cursor.fetchall()]
    if limit:
        rows = rows[:limit]
    total = len(rows)
    print(f"Processing {total} places with 0 or 1 images.\n")

    done = 0
    failed = 0

    for i, row in enumerate(rows, 1):
        name = row["name"].title()
        city = (row["city"] or "").title()
        state = (row["state"] or "").title()
        place_type = (row["type"] or "").title()
        img_count = row.get("img_count", 0)
        needed = 2 - img_count

        query = f"{name} {place_type} {city} India".strip()
        base_filename = re.sub(r"[^\w\-]", "_", "_".join(p for p in [name, city, state] if p).replace(" ", "_"))

        print(f"[{i}/{total}] {name} ({city}) [{place_type}] (has {img_count}, fetching {needed} more)")

        urls = fetch_image_urls(query, count=needed)
        if not urls:
            print(f"  no images found.")
            failed += 1
            continue

        uploaded = 0
        for idx, url in enumerate(urls, img_count + 1):
            filename = f"{base_filename}_{idx}.webp"
            filepath = os.path.join(OUTPUT_DIR, filename)

            if not os.path.exists(filepath):
                if not download_as_webp(url, filename):
                    print(f"  [{idx}/5] download failed.")
                    continue

            cdn_name = upload_to_cdn(filepath)
            if not cdn_name:
                print(f"  [{idx}/5] CDN upload failed.")
                continue

            link_place_image(write_cursor, conn, row["id"], cdn_name)
            size_kb = os.path.getsize(filepath) / 1024
            print(f"  [{idx}/5] {cdn_name} ({size_kb:.0f} KB)")
            uploaded += 1

        if uploaded > 0:
            print(f"  => {uploaded} image(s) linked.")
            done += 1
        else:
            print(f"  => all uploads failed.")
            failed += 1

        time.sleep(1.0)

    read_cursor.close()
    write_cursor.close()
    conn.close()

    print(f"\nDone. {done} places processed, {failed} failed out of {total}.")


if __name__ == "__main__":
    if not PEXELS_KEY and not UNSPLASH_KEY:
        print("ERROR: Set PEXELS_API_KEY or UNSPLASH_ACCESS_KEY in .env")
        sys.exit(1)
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])
    main(limit=limit)
