"""
Fill missing images for places that have no entry in place_image_map.

For each such place, fetches up to 5 images from:
  Wikimedia Commons (primary) → Pexels → Unsplash (fallbacks)

Each image is:
  1. Downloaded and converted to .webp
  2. Uploaded to CDN via direct IP (bypasses Hostinger firewall on Azure outbound IPs)
  3. Inserted into `images` table (filename only)
  4. Linked in `place_image_map`

Run from project root:
    .venv/bin/python3 scripts/fill_missing_images.py          # all missing
    .venv/bin/python3 scripts/fill_missing_images.py --limit 10  # test first N
"""

import json
import os
import re
import struct
import sys
import time
import warnings
import requests
import urllib3
import pyodbc

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from azure.identity import DefaultAzureCredential
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = "generated_images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

PEXELS_URL = "https://api.pexels.com/v1/search"
UNSPLASH_URL = "https://api.unsplash.com/search/photos"
CDN_UPLOAD_URL = f"https://{os.getenv('CDN_HOSTINGER_IP')}/app/upload.php"

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
    "User-Agent": "TravelensImageBot/1.0 (info@travelens.in; https://travelens.in) Python-Requests"
}


def _wikimedia_search(query: str, count: int, offset: int) -> list:
    """Run one Wikimedia search, return filtered (url, 'wikimedia') list."""
    resp = requests.get(
        WIKIMEDIA_URL,
        headers=_WIKIMEDIA_HEADERS,
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": max((offset + count) * 3, 15),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|thumburl",
            "iiurlwidth": 1200,
            "format": "json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", {})
    candidates = sorted(pages.values(), key=lambda x: x.get("index", 99))
    print(f"  [Wikimedia] {len(candidates)} candidates for '{query}'")
    valid = []
    for page in candidates:
        ii = page.get("imageinfo", [{}])[0]
        mime = ii.get("mime", "")
        w = ii.get("width", 0)
        h = ii.get("height", 0)
        title = page.get("title", "")
        title_lower = title.lower()
        if mime != "image/jpeg":
            print(f"    skip [{title}] mime={mime}")
            continue
        if h > w * 1.5:
            print(f"    skip [{title}] too tall portrait ({w}x{h})")
            continue
        skip_word = next((s for s in _SKIP_WORDS if s in title_lower), None)
        if skip_word:
            print(f"    skip [{title}] contains '{skip_word}'")
            continue
        url = ii.get("thumburl") or ii.get("url", "")
        if url:
            print(f"    ok   [{title}] {w}x{h}")
            valid.append((url, "wikimedia"))
    return valid


def fetch_wikimedia_urls(query: str, count: int, offset: int = 0) -> list:
    """Return up to `count` (url, 'wikimedia') tuples from Wikimedia Commons.

    Tries the full query first. If it returns 0 candidates, retries with just
    the first two words (e.g. 'Kaina Temple') to find broader matches.
    offset skips the first N valid results so re-runs don't repeat already-used images.
    """
    try:
        valid = _wikimedia_search(query, count, offset)
        if not valid:
            # Retry with shorter query — drop city/state/India suffix
            short_query = " ".join(query.split()[:2])
            if short_query != query:
                print(f"  [Wikimedia] retrying with shorter query: '{short_query}'")
                valid = _wikimedia_search(short_query, count, offset)
        result = valid[offset: offset + count]
        print(f"  [Wikimedia] {len(valid)} valid, returning {len(result)}")
        return result
    except Exception as e:
        print(f"  [Wikimedia] error: {e}")
    return []


def fetch_image_urls(query: str, count: int = 2, offset: int = 0) -> list:
    """Collect up to `count` (url, source) tuples from Wikimedia → Pexels → Unsplash."""
    results = []

    results.extend(fetch_wikimedia_urls(query, count, offset=offset))

    if len(results) < count and PEXELS_KEY:
        needed = count - len(results)
        existing_urls = {u for u, _ in results}
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
                if url and url not in existing_urls:
                    results.append((url, "pexels"))
                    existing_urls.add(url)
        except Exception as e:
            print(f"  [Pexels] error: {e}")

    if len(results) < count and UNSPLASH_KEY:
        needed = count - len(results)
        existing_urls = {u for u, _ in results}
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
                if url and url not in existing_urls:
                    results.append((url, "unsplash"))
                    existing_urls.add(url)
        except Exception as e:
            print(f"  [Unsplash] error: {e}")

    return results[:count]


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
            resp = requests.post(
                CDN_UPLOAD_URL,
                files={"file": f},
                headers={"Host": "travelens.in"},
                timeout=30,
                verify=False,
            )
        if resp.status_code == 200:
            path = resp.json().get("path", "")
            return os.path.basename(path)
    except Exception as e:
        print(f"  [CDN upload] error: {e}")
    return ""


def link_place_image(place_id: int, image_name: str, source: str = None) -> bool:
    """Insert image_name into `images`, link to place via `place_image_map`. Returns True on success.

    Opens a fresh connection per call — Azure SQL drops idle connections after ~30s when the
    main loop is busy with downloads/CDN uploads, so sharing a long-lived connection is unreliable.
    DefaultAzureCredential caches the token so reconnecting is just a TCP handshake (~200ms).
    """
    conn = None
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            MERGE images AS tgt
            USING (SELECT ? AS image_name) AS src ON tgt.image_name = src.image_name
            WHEN NOT MATCHED THEN INSERT (id, image_name, source)
                VALUES ((SELECT ISNULL(MAX(id), 0) + 1 FROM images), src.image_name, ?);
            """,
            (image_name, source),
        )
        read_cur = conn.cursor()
        read_cur.execute("SELECT id FROM images WHERE image_name = ?", (image_name,))
        row = read_cur.fetchone()
        read_cur.close()
        image_id = row[0] if row else None
        if image_id is None:
            print(f"  [DB] could not get id for {image_name}")
            return False
        cursor.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM place_image_map WHERE place_id = ? AND image_id = ?)
                INSERT INTO place_image_map (place_id, image_id) VALUES (?, ?)
            """,
            (place_id, image_id, place_id, image_id),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"  [DB] error writing {image_name}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        if conn:
            conn.close()


def main(limit=None):
    TARGET = 5
    conn = _connect()
    read_cursor = conn.cursor()

    read_cursor.execute(
        "SELECT p.id, p.name, p.display_name, p.address_components, "
        "c.name AS city, s.name AS state, p.type, "
        "COUNT(pim.image_id) AS img_count "
        "FROM places p "
        "LEFT JOIN cities c ON p.city_id = c.id "
        "LEFT JOIN states s ON c.state_id = s.id "
        "LEFT JOIN place_image_map pim ON pim.place_id = p.id "
        "GROUP BY p.id, p.name, p.display_name, p.address_components, c.name, s.name, p.type "
        "HAVING COUNT(pim.image_id) < 5 "
        "ORDER BY COUNT(pim.image_id) ASC"
    )
    cols = [col[0] for col in read_cursor.description]
    rows = [dict(zip(cols, row)) for row in read_cursor.fetchall()]
    if limit:
        rows = rows[:limit]
    total = len(rows)
    print(f"Processing {total} places with fewer than {TARGET} images.\n")

    done = 0
    failed = 0

    for i, row in enumerate(rows, 1):
        # Resolve name/city/state: Google data first, fall back to local DB values
        addr_comp = row.get("address_components")
        g_city = g_state = None
        if addr_comp:
            try:
                for comp in json.loads(addr_comp):
                    types = comp.get("types", [])
                    if g_city is None and any(t in types for t in ("locality", "administrative_area_level_2", "administrative_area_level_3")):
                        g_city = comp.get("longText")
                    if g_state is None and "administrative_area_level_1" in types:
                        g_state = comp.get("longText")
            except Exception:
                pass

        name = (row.get("display_name") or row["name"]).strip()
        city = (g_city or row.get("city") or "").strip()
        state = (g_state or row.get("state") or "").strip()
        place_type = (row["type"] or "").title()
        img_count = row.get("img_count", 0)
        needed = TARGET - img_count
        # offset = how many top Wikimedia results to skip.
        # 0 or 1 image: take from the top (offset 0).
        # 2+ images: skip the results already used in prior runs (offset = img_count - 1).
        offset = max(0, img_count - 1)

        # Specific query first; Wikimedia fallback uses a broader query if specific returns 0
        query = f"{name} {city} {state} India".strip()
        base_filename = re.sub(r"[^\w\-]", "_", "_".join(p for p in [name, city, state] if p).replace(" ", "_"))

        print(f"[{i}/{total}] {name} ({city}) [{place_type}] (has {img_count}, fetching {needed} more, offset={offset})")

        url_tuples = fetch_image_urls(query, count=needed, offset=offset)
        if not url_tuples:
            print(f"  no images found.")
            failed += 1
            continue

        uploaded = 0
        for idx, (url, source) in enumerate(url_tuples, img_count + 1):
            filename = f"{base_filename}_{idx}.webp"
            filepath = os.path.join(OUTPUT_DIR, filename)

            if not os.path.exists(filepath):
                if not download_as_webp(url, filename):
                    print(f"  [{idx}/{TARGET}] download failed.")
                    continue

            cdn_name = upload_to_cdn(filepath)
            if not cdn_name:
                print(f"  [{idx}/{TARGET}] CDN upload failed.")
                continue

            if not link_place_image(row["id"], cdn_name, source):
                print(f"  [{idx}/{TARGET}] DB write failed for {cdn_name} (file is on CDN).")
                continue
            size_kb = os.path.getsize(filepath) / 1024
            print(f"  [{idx}/{TARGET}] {cdn_name} ({size_kb:.0f} KB) [{source}]")
            uploaded += 1

        if uploaded > 0:
            print(f"  => {uploaded} image(s) linked.")
            done += 1
        else:
            print(f"  => all uploads failed.")
            failed += 1

        time.sleep(1.0)

    read_cursor.close()
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
