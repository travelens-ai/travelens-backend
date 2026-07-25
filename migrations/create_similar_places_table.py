"""Create `similar_places` table and seed it from similar_places.csv.

Replaces the flat-file approach (similar_places.csv / .pkl) with a proper DB
table keyed on city_id so LLM-generated near-duplicate names like
"Jaipur", "Jaipur Heritage Circuit", "Jaipur (heritage + forts quick loop)"
all collapse to a single row for the jaipur city.

Schema:
    similar_places (
        id          INT IDENTITY PK,
        city_id     INT NOT NULL UNIQUE FK -> cities(id),
        description NVARCHAR(500),
        price_range NVARCHAR(100),
        created_at  DATETIME2 DEFAULT GETDATE()
    )

Run from project root (needs `az login` or managed identity):
    venv/bin/python migrations/create_similar_places_table.py            # apply
    venv/bin/python migrations/create_similar_places_table.py --dry-run  # preview only

Idempotent: table creation uses IF NOT EXISTS; seeding uses MERGE so
re-running is always a no-op.
"""

import argparse
import os
import struct
import sys

import pandas as pd
import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SQL_COPT_SS_ACCESS_TOKEN = 1256
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

sys.path.insert(0, os.path.join(_PROJECT_ROOT, "src"))
from models.recommendation.image_helpers import _candidate_city_keys


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
    return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def _resolve_city_id(cursor, placename: str):
    """Return cities.id for the given placename, or None if not found."""
    for key in _candidate_city_keys(placename):
        cursor.execute("SELECT id FROM cities WHERE LOWER(name) = LOWER(?)", (key,))
        row = cursor.fetchone()
        if row:
            return row[0]
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing")
    args = parser.parse_args()

    conn = _connect()
    conn.autocommit = False
    cursor = conn.cursor()

    # --- Step 1: create table ---
    cursor.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'similar_places'"
    )
    table_exists = cursor.fetchone() is not None

    if not table_exists:
        if args.dry_run:
            print("[dry-run] Would CREATE TABLE similar_places")
        else:
            cursor.execute("""
                CREATE TABLE similar_places (
                    id          INT IDENTITY(1,1) PRIMARY KEY,
                    city_id     INT NOT NULL REFERENCES cities(id),
                    description NVARCHAR(500) NULL,
                    price_range NVARCHAR(100) NULL,
                    created_at  DATETIME2 DEFAULT GETDATE(),
                    CONSTRAINT uq_similar_places_city UNIQUE (city_id)
                )
            """)
            conn.commit()
            print("Created table similar_places.")
    else:
        print("Table similar_places already exists — skipping CREATE.")

    # --- Step 2: seed from CSV ---
    csv_path = os.path.join(_PROJECT_ROOT, "similar_places.csv")
    if not os.path.exists(csv_path):
        print(f"CSV not found at {csv_path} — skipping seed.")
        cursor.close()
        conn.close()
        return

    df = pd.read_csv(csv_path)
    inserted = skipped_no_city = skipped_exists = 0

    for _, row in df.iterrows():
        placename = str(row.get("placename", "")).strip()
        description = str(row.get("description", "") or "").strip() or None
        price_range = str(row.get("price_estimated_range", "") or "").strip() or None

        if not placename:
            continue

        city_id = _resolve_city_id(cursor, placename)
        if city_id is None:
            print(f"  [skip] '{placename}' — no matching city in DB")
            skipped_no_city += 1
            continue

        # Check existing
        cursor.execute("SELECT 1 FROM similar_places WHERE city_id = ?", (city_id,))
        already_exists = cursor.fetchone() is not None

        if already_exists:
            skipped_exists += 1
            continue

        if args.dry_run:
            print(f"  [dry-run] Would INSERT city_id={city_id} for '{placename}'")
            inserted += 1
        else:
            cursor.execute(
                "INSERT INTO similar_places (city_id, description, price_range) "
                "VALUES (?, ?, ?)",
                (city_id, description, price_range),
            )
            inserted += 1

    if not args.dry_run:
        conn.commit()

    print(
        f"\nSeed summary: inserted={inserted}, "
        f"skipped_no_city={skipped_no_city}, skipped_already_exists={skipped_exists}"
    )
    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
