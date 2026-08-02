"""Create and seed the `accommodation_preferences` lookup table (Azure SQL).

Holds the preferred stay type — hotel, hostel, resort, homestay, apartment, etc.

Schema:
    id          INT IDENTITY PRIMARY KEY
    name        NVARCHAR(100)  NOT NULL UNIQUE
    created_at  DATETIME2      DEFAULT SYSUTCDATETIME()

Mirrors the other lookup tables so it plugs into the registry-driven admin CRUD
and the /configs lookup loader with no new query code.

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/create_accommodation_preferences_table.py

Idempotent: table guarded by IF NOT EXISTS, and rows are seeded only when the
name is absent, so re-running is a no-op.
"""
import os
import struct
import sys

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SQL_COPT_SS_ACCESS_TOKEN = 1256

ACCOMMODATION_PREFERENCES = [
    "Hotel",
    "Hostel",
    "Resort",
    "Homestay",
    "Apartment",
    "Guesthouse",
    "Villa",
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
    cursor.execute(
        """
        IF OBJECT_ID('dbo.accommodation_preferences', 'U') IS NULL
        CREATE TABLE dbo.accommodation_preferences (
            id         INT IDENTITY(1,1) NOT NULL,
            name       NVARCHAR(100) NOT NULL,
            created_at DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT PK_accommodation_preferences PRIMARY KEY (id),
            CONSTRAINT UQ_accommodation_preferences_name UNIQUE (name)
        )
        """
    )
    # Seed only names that aren't already present (idempotent).
    cursor.executemany(
        """
        INSERT INTO dbo.accommodation_preferences (name)
        SELECT ?
        WHERE NOT EXISTS (SELECT 1 FROM dbo.accommodation_preferences WHERE name = ?)
        """,
        [(name, name) for name in ACCOMMODATION_PREFERENCES],
    )
    conn.commit()
    cursor.close()
    conn.close()
    print(f"Ensured table `accommodation_preferences` and seeded {len(ACCOMMODATION_PREFERENCES)} candidate rows.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
