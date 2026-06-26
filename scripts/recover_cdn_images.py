"""
Recovery script: write orphaned CDN images into the DB.

Background: fill_missing_images.py uploaded images to CDN but silently failed DB writes.
This script scans generated_images/, HEAD-checks each file on CDN to confirm presence,
then writes to `images` + `place_image_map` for any not already linked.

Run from project root (venv active):
    python3 scripts/recover_cdn_images.py            # live run
    python3 scripts/recover_cdn_images.py --dry-run  # preview only
"""

import os
import re
import struct
import sys
import urllib3
import pyodbc
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

GENERATED_DIR = "generated_images"
IMAGE_BASE_URL = "https://travelens.in/app/generated_images/"
_SQL_COPT_SS_ACCESS_TOKEN = 1256
DRY_RUN = "--dry-run" in sys.argv
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None


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


def _normalise(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text.title().replace(" ", "_"))


def _build_key(name: str, city: str, state: str) -> str:
    return "_".join(_normalise(p) for p in [name, city, state] if p)


def _cdn_exists(filename: str) -> bool:
    try:
        r = requests.head(IMAGE_BASE_URL + filename, timeout=8)
        return r.status_code == 200
    except Exception:
        return False


def _link_place_image(cursor, conn, place_id: int, image_name: str) -> bool:
    try:
        cursor.execute(
            """
            MERGE images AS tgt
            USING (SELECT ? AS image_name) AS src ON tgt.image_name = src.image_name
            WHEN MATCHED AND tgt.id IS NULL THEN
                UPDATE SET tgt.id = (SELECT ISNULL(MAX(id), 0) + 1 FROM images WHERE id IS NOT NULL)
            WHEN NOT MATCHED THEN INSERT (id, image_name)
                VALUES ((SELECT ISNULL(MAX(id), 0) + 1 FROM images), src.image_name);
            """,
            (image_name,),
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
        print(f"  [DB] error for {image_name}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def main():
    if DRY_RUN:
        print("[DRY RUN] No DB writes will be made.\n")

    print("Connecting to Azure SQL...")
    conn = _connect()
    cur = conn.cursor()

    print("Loading places...")
    cur.execute(
        "SELECT p.id, p.name, c.name, s.name "
        "FROM places p "
        "LEFT JOIN cities c ON p.city_id = c.id "
        "LEFT JOIN states s ON c.state_id = s.id"
    )
    key_to_place_id: dict = {}
    for place_id, name, city, state in cur.fetchall():
        key = _build_key(name or "", city or "", state or "")
        key_to_place_id[key] = place_id

    print("Loading already-linked images...")
    cur.execute(
        "SELECT i.image_name FROM images i "
        "JOIN place_image_map pim ON pim.image_id = i.id"
    )
    already_linked: set = {row[0] for row in cur.fetchall()}

    webp_files = sorted(f for f in os.listdir(GENERATED_DIR) if f.endswith(".webp"))
    if LIMIT:
        webp_files = webp_files[:LIMIT]
    total = len(webp_files)
    print(f"\nLoaded {len(key_to_place_id)} places | {len(already_linked)} already linked | {total} local files\n")

    skipped_linked = 0
    skipped_no_match = 0
    skipped_no_cdn = 0
    written = 0

    for fname in webp_files:
        if fname in already_linked:
            skipped_linked += 1
            continue

        stem = re.sub(r"_\d+$", "", fname[:-5])  # strip .webp then trailing _N

        place_id = key_to_place_id.get(stem)
        if place_id is None:
            # fallback: drop last segment (state) in case it was omitted
            stem_no_state = stem.rsplit("_", 1)[0]
            place_id = key_to_place_id.get(stem_no_state)

        if place_id is None:
            skipped_no_match += 1
            continue

        if not _cdn_exists(fname):
            print(f"  [CDN 404] {fname}")
            skipped_no_cdn += 1
            continue

        if DRY_RUN:
            print(f"  [dry-run] {fname} → place_id={place_id}")
            written += 1
            continue

        ok = _link_place_image(cur, conn, place_id, fname)
        if ok:
            print(f"  linked {fname} → place_id={place_id}")
            written += 1
        else:
            skipped_no_match += 1

    cur.close()
    conn.close()

    print(f"\n{'[DRY RUN] ' if DRY_RUN else ''}Done.")
    print(f"  Total local files:       {total}")
    print(f"  Already in DB (skipped): {skipped_linked}")
    print(f"  Written to DB:           {written}")
    print(f"  Not on CDN (skipped):    {skipped_no_cdn}")
    print(f"  No place match / error:  {skipped_no_match}")


if __name__ == "__main__":
    main()
