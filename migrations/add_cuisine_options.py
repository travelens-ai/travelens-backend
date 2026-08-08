"""Add new cuisine options to the food_preferences lookup table (Azure SQL).

Inserts: Chinese, Mughlai, Italian, Thai, Seafood, Continental.
Idempotent: rows are inserted only when the name is absent.

Run from project root:
    venv/bin/python migrations/add_cuisine_options.py
"""
import os
import struct
import sys

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SQL_COPT_SS_ACCESS_TOKEN = 1256

NEW_CUISINES = [
    ("Chinese",     "🥡"),
    ("Mughlai",     "🍖"),
    ("Italian",     "🍝"),
    ("Thai",        "🌶️"),
    ("Seafood",     "🦐"),
    ("Continental", "🍽️"),
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


def main():
    conn = _connect()
    cursor = conn.cursor()
    inserted = 0
    for name, icon in NEW_CUISINES:
        cursor.execute(
            """
            INSERT INTO dbo.food_preferences (name, icon)
            SELECT ?, ?
            WHERE NOT EXISTS (SELECT 1 FROM dbo.food_preferences WHERE name = ?)
            """,
            (name, icon, name),
        )
        if cursor.rowcount:
            inserted += cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    print(f"food_preferences: inserted {inserted} new cuisine(s) (of {len(NEW_CUISINES)} candidates).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
