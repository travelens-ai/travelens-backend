"""Drop the sender columns from `shared_itineraries` (Azure SQL).

Removes `shared_user_id` and `shared_device_id` — a share is now identified only
by its receiver (`receiver_user_id` / `receiver_device_id`) and `itinerary_id`.
`receiver_user_id` stays nullable (optional).

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/drop_sender_from_shared_itineraries.py

Idempotent: each column drop is guarded by IF COL_LENGTH(...) IS NOT NULL.
"""
import os
import struct

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

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
    return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def main():
    conn = _connect()
    cursor = conn.cursor()
    try:
        for col in ("shared_user_id", "shared_device_id"):
            cursor.execute(
                f"IF COL_LENGTH('dbo.shared_itineraries', '{col}') IS NOT NULL "
                f"ALTER TABLE dbo.shared_itineraries DROP COLUMN {col}"
            )
            conn.commit()
            print(f"Ensured column {col} dropped")
    finally:
        cursor.close()
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
