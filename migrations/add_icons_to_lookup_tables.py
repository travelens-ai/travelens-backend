"""Add an `icon` column to trip_types, food_types and accommodation_preferences
(Azure SQL) and backfill emoji icons — matching the `activities` table shape.

Schema change (per table):
    icon  NVARCHAR(20)  NULL

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/add_icons_to_lookup_tables.py

Idempotent: the column is added only if absent, and icons are set only where
the name matches a known seed (existing custom rows are left untouched).
"""
import os
import struct
import sys

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SQL_COPT_SS_ACCESS_TOKEN = 1256

# name -> emoji icon, per table.
ICONS = {
    "trip_types": {
        "Leisure": "🌴",
        "Honeymoon": "💑",
        "Workation": "💻",
        "Business": "💼",
        "Family": "👨‍👩‍👧‍👦",
        "Adventure": "🧗",
        "Pilgrimage": "🛕",
        "Wellness": "🧘",
        "Backpacking": "🎒",
        "Weekend Getaway": "🏖️",
    },
    "food_types": {
        "Veg": "🥗",
        "Non-Veg": "🍗",
        "Vegan": "🌱",
        "Eggetarian": "🥚",
        "Jain": "🍲",
    },
    "accommodation_preferences": {
        "Hotel": "🏨",
        "Hostel": "🛏️",
        "Resort": "🏝️",
        "Homestay": "🏠",
        "Apartment": "🏢",
        "Guesthouse": "🏡",
        "Villa": "🏘️",
    },
}


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


def main():
    conn = _connect()
    cursor = conn.cursor()
    for table, icon_map in ICONS.items():
        # Add the column only if it doesn't already exist.
        cursor.execute(
            f"""
            IF NOT EXISTS (
                SELECT 1 FROM sys.columns
                WHERE object_id = OBJECT_ID('dbo.{table}') AND name = 'icon'
            )
            ALTER TABLE dbo.{table} ADD icon NVARCHAR(20) NULL
            """
        )
        conn.commit()  # ALTER must commit before the UPDATE references the column

        cursor.executemany(
            f"UPDATE dbo.{table} SET icon = ? WHERE name = ?",
            [(icon, name) for name, icon in icon_map.items()],
        )
        conn.commit()
        print(f"{table}: ensured icon column and backfilled {len(icon_map)} icons.")

    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
