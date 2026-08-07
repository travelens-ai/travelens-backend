"""Add suitable_trip_types and suitable_group_types columns to the places table.

suitable_trip_types  — JSON array e.g. '["honeymoon","leisure","adventure"]'
suitable_group_types — JSON array e.g. '["couples","friends"]'

Populated at itinerary generation time (LLM tags each place inline) and
backfilled for existing rows via scripts/backfill_place_tags.py.

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/add_place_trip_group_tags.py

Idempotent: each column is added only if it doesn't already exist.
"""
import os
import struct

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SQL_COPT_SS_ACCESS_TOKEN = 1256
TABLE = "places"
COLUMNS = [
    ("suitable_trip_types",  "NVARCHAR(500) NULL"),
    ("suitable_group_types", "NVARCHAR(500) NULL"),
]


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


def column_exists(cursor, table, column):
    cursor.execute(
        "SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(?) AND name = ?",
        (f"dbo.{table}", column),
    )
    return cursor.fetchone() is not None


def main():
    conn = _connect()
    cursor = conn.cursor()
    for col_name, col_def in COLUMNS:
        if column_exists(cursor, TABLE, col_name):
            print(f"dbo.{TABLE}.{col_name} already exists — skipping")
            continue
        cursor.execute(f"ALTER TABLE dbo.{TABLE} ADD {col_name} {col_def}")
        conn.commit()
        print(f"Added dbo.{TABLE}.{col_name}")
    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
