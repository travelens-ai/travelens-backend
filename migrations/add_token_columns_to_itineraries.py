"""Add `input_token` and `output_token` columns to the `itineraries` table.

These store the Azure OpenAI token usage (prompt vs completion) for the AI
generation behind each stored itinerary, so cost/usage can be tracked per row.
Both are nullable INTs — existing rows and any itinerary generated while the DB
is down simply carry NULL.

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/add_token_columns_to_itineraries.py

Idempotent: each column is added only if it doesn't already exist.
"""
import os
import struct

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SQL_COPT_SS_ACCESS_TOKEN = 1256
TABLE = "itineraries"
COLUMNS = ["input_token", "output_token"]


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
    for col in COLUMNS:
        if column_exists(cursor, TABLE, col):
            print(f"dbo.{TABLE}.{col} already exists")
            continue
        cursor.execute(f"ALTER TABLE dbo.{TABLE} ADD {col} INT NULL")
        conn.commit()
        print(f"Added dbo.{TABLE}.{col}")
    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
