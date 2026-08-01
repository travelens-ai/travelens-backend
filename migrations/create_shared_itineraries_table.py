"""Create the `shared_itineraries` table (Azure SQL).

Records one itinerary shared with a receiver. A receiver is either a logged-in
user (int user id, optional) OR an anonymous device (string device id); at least
one is set.

Schema:
    id                   INT IDENTITY PRIMARY KEY
    receiver_user_id     INT            NULL          -- receiver, if logged-in user
    receiver_device_id   NVARCHAR(255)  NULL          -- receiver, if device
    itinerary_id         INT            NOT NULL      -- FK-ish to itineraries.id
    created_at           DATETIME2      DEFAULT SYSUTCDATETIME()

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/create_shared_itineraries_table.py

Idempotent: guarded by IF OBJECT_ID(...) IS NULL, plus a helpful index on the
receiver columns for the "itineraries shared with me" lookup.
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
        cursor.execute(
            "IF OBJECT_ID('dbo.shared_itineraries', 'U') IS NULL "
            "CREATE TABLE dbo.shared_itineraries ("
            "  id                 INT IDENTITY(1,1) PRIMARY KEY,"
            "  receiver_user_id   INT            NULL,"
            "  receiver_device_id NVARCHAR(255)  NULL,"
            "  itinerary_id       INT            NOT NULL,"
            "  created_at         DATETIME2      NOT NULL DEFAULT SYSUTCDATETIME()"
            ")"
        )
        conn.commit()
        print("Ensured table dbo.shared_itineraries")

        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM sys.indexes "
            "WHERE name = 'IX_shared_itineraries_receiver') "
            "CREATE INDEX IX_shared_itineraries_receiver "
            "ON dbo.shared_itineraries (receiver_user_id, receiver_device_id)"
        )
        conn.commit()
        print("Ensured index IX_shared_itineraries_receiver")
    finally:
        cursor.close()
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
