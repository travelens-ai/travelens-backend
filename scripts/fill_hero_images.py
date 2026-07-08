"""
Add a _0 hero image for every place in the DB.

Search strategy per place:
  1. Wikimedia: display_name only (most precise)
  2. Wikimedia: display_name + google_city + google_state + India (fallback)
  3. Pexels: display_name + city (last resort)

Image saved as {base_filename}_0.webp, uploaded to CDN, inserted into
`images` and linked in `place_image_map`. Re-run safe — skips places
that already have a _0 image linked.

Run from project root:
    .venv/bin/python3 scripts/fill_hero_images.py           # all places
    .venv/bin/python3 scripts/fill_hero_images.py --limit 10  # test N
"""

import json
import os
import re
import struct
import sys
import time
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
CDN_UPLOAD_URL = f"https://{os.getenv('CDN_HOSTINGER_IP')}/app/upload.php"
PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")

_SQL_COPT_SS_ACCESS_TOKEN = 1256
_WIKIMEDIA_URL = "https://commons.wikimedia.org/w/api.php"
_WIKIMEDIA_HEADERS = {
    "User-Agent": "TravelensImageBot/1.0 (info@travelens.in; https://travelens.in) Python-Requests"
}
_SKIP_WORDS = {"map", "logo", "icon", "flag", "svg", "diagram", "coat", "plan", "stamp", "chart"}


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


def _wikimedia_search(query: str) -> list:
    """Return up to 1 best (url, 'wikimedia') tuple from Wikimedia Commons."""
    try:
        resp = requests.get(
            _WIKIMEDIA_URL,
            headers=_WIKIMEDIA_HEADERS,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": 15,
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
        for page in candidates:
            ii = page.get("imageinfo", [{}])[0]
            mime = ii.get("mime", "")
            w = ii.get("width", 0)
            h = ii.get("height", 0)
            title = page.get("title", "")
            title_lower = title.lower()
            if mime != "image/jpeg":
                continue
            if w <= h:
                continue
            if any(s in title_lower for s in _SKIP_WORDS):
                continue
            url = ii.get("thumburl") or ii.get("url", "")
            if url:
                print(f"    wiki ok [{title}] {w}x{h}")
                return [(url, "wikimedia")]
    except Exception as e:
        print(f"  [Wikimedia] error: {e}")
    return []


def fetch_hero_url(display_name: str, city: str, state: str) -> tuple | None:
    """Return (url, source) for the best hero image, or None.

    Search order:
      1. Wikimedia: display_name only
      2. Wikimedia: display_name + city + state
      3. Pexels:    display_name + city + state + India
    """
    # 1. Wikimedia: display_name only
    print(f"  [Wikimedia] searching '{display_name}'")
    result = _wikimedia_search(display_name)
    if result:
        return result[0]

    # 2. Wikimedia: display_name + city + state
    wiki_full = " ".join(p for p in [display_name, city, state] if p)
    if wiki_full != display_name:
        print(f"  [Wikimedia] retrying '{wiki_full}'")
        result = _wikimedia_search(wiki_full)
        if result:
            return result[0]

    # 3. Pexels: display_name + city + state + India
    if PEXELS_KEY:
        pexels_query = " ".join(p for p in [display_name, city, state, "India"] if p)
        print(f"  [Pexels] searching '{pexels_query}'")
        try:
            resp = requests.get(
                PEXELS_URL,
                headers={"Authorization": PEXELS_KEY},
                params={"query": pexels_query, "per_page": 1, "orientation": "landscape"},
                timeout=10,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
            if photos:
                url = photos[0].get("src", {}).get("large", "")
                if url:
                    return (url, "pexels")
        except Exception as e:
            print(f"  [Pexels] error: {e}")

    return None


def download_as_webp(url: str, filename: str) -> bool:
    """Download image from URL, convert to webp, save to generated_images/."""
    try:
        from PIL import Image
        time.sleep(3.0)
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
    """Upload file to CDN, return filename only."""
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
    """Insert image into `images` and link via `place_image_map`. Fresh connection per call."""
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
    conn = _connect()
    cur = conn.cursor()

    # All places; skip those that already have a _0 image linked
    cur.execute(
        """
        SELECT p.id, p.name, p.display_name, p.address_components,
               c.name AS city, s.name AS state
        FROM places p
        LEFT JOIN cities c ON p.city_id = c.id
        LEFT JOIN states s ON c.state_id = s.id
        WHERE NOT EXISTS (
            SELECT 1 FROM place_image_map pim
            JOIN images i ON i.id = pim.image_id
            WHERE pim.place_id = p.id
              AND i.image_name LIKE '%!_0.webp' ESCAPE '!'
        )
        ORDER BY p.id
        """
    )
    cols = [col[0] for col in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()

    if limit:
        rows = rows[:limit]

    total = len(rows)
    print(f"Processing {total} places needing a _0 hero image.\n")

    done = failed = 0

    for i, row in enumerate(rows, 1):
        # Resolve from Google data first, fall back to local DB
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

        display_name = (row.get("display_name") or row["name"]).strip()
        city = (g_city or row.get("city") or "").strip()
        state = (g_state or row.get("state") or "").strip()

        base_filename = re.sub(
            r"[^\w\-]", "_",
            "_".join(p for p in [display_name, city, state] if p).replace(" ", "_")
        )
        filename = f"{base_filename}_0.webp"
        filepath = os.path.join(OUTPUT_DIR, filename)

        print(f"[{i}/{total}] {display_name} ({city}, {state})")

        result = fetch_hero_url(display_name, city, state)
        if not result:
            print(f"  no image found — skipped.")
            failed += 1
            time.sleep(1.0)
            continue

        url, source = result

        if not os.path.exists(filepath):
            if not download_as_webp(url, filename):
                print(f"  download failed — skipped.")
                failed += 1
                time.sleep(1.0)
                continue

        cdn_name = upload_to_cdn(filepath)
        if not cdn_name:
            print(f"  CDN upload failed — skipped.")
            failed += 1
            time.sleep(1.0)
            continue

        if not link_place_image(row["id"], cdn_name, source):
            print(f"  DB write failed for {cdn_name}.")
            failed += 1
            time.sleep(1.0)
            continue

        size_kb = os.path.getsize(filepath) / 1024
        print(f"  => {cdn_name} ({size_kb:.0f} KB) [{source}]")
        done += 1
        time.sleep(1.0)

    print(f"\nDone. {done} hero images added, {failed} failed out of {total}.")


if __name__ == "__main__":
    if not PEXELS_KEY:
        print("WARNING: PEXELS_API_KEY not set — no Pexels fallback available.")
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])
    main(limit=limit)
