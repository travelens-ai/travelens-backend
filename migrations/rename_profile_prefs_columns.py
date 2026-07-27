"""Rename user profile-preference columns and add `activities` (Azure SQL).

The profile now models travel preferences as group type + food preference +
activities (matching the /configs lookup tables), replacing the older
trip_type / trip_companion pair on the `users` table:

    trip_type       -> group_type
    trip_companion  -> food_preference
    (new)           -> activities        NVARCHAR(MAX) NULL

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/rename_profile_prefs_columns.py

Idempotent: each rename runs only if the old column still exists, and the new
column is added only if missing — so re-running is a no-op.
"""
import os
import struct

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SQL_COPT_SS_ACCESS_TOKEN = 1256

# old column -> new column
RENAMES = [
    ("trip_type", "group_type"),
    ("trip_companion", "food_preference"),
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


def _column_exists(cursor, table, column):
    cursor.execute(
        """
        SELECT COUNT(*) FROM sys.columns
        WHERE object_id = OBJECT_ID(?) AND name = ?
        """,
        (f"dbo.{table}", column),
    )
    return cursor.fetchone()[0] > 0


def main():
    conn = _connect()
    cursor = conn.cursor()
    try:
        for old, new in RENAMES:
            if _column_exists(cursor, "users", old) and not _column_exists(cursor, "users", new):
                # sp_rename can't be parameterized; names are constants above, not user input.
                cursor.execute(f"EXEC sp_rename 'dbo.users.{old}', '{new}', 'COLUMN'")
                conn.commit()
                print(f"Renamed users.{old} -> users.{new}")
            elif _column_exists(cursor, "users", new):
                print(f"users.{new} already exists — skipping rename")
            else:
                print(f"users.{old} not found — skipping")

        if not _column_exists(cursor, "users", "activities"):
            cursor.execute("ALTER TABLE dbo.users ADD activities NVARCHAR(MAX) NULL")
            conn.commit()
            print("Added users.activities")
        else:
            print("users.activities already exists")

        print("Done.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
