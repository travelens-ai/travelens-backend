"""Create and seed the `trip_types` lookup table (Azure SQL).

Schema:
    id          INT IDENTITY PRIMARY KEY
    name        NVARCHAR(100)  NOT NULL UNIQUE
    created_at  DATETIME2      DEFAULT SYSUTCDATETIME()

Mirrors the other lookup tables (group_types, food_preferences) so it plugs
into the registry-driven admin CRUD and the /configs lookup loader with no
new query code.

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/create_trip_types_table.py

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

TRIP_TYPES = [
    "Leisure",
    "Honeymoon",
    "Workation",
    "Business",
    "Family",
    "Adventure",
    "Pilgrimage",
    "Wellness",
    "Backpacking",
    "Weekend Getaway",
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
        IF OBJECT_ID('dbo.trip_types', 'U') IS NULL
        CREATE TABLE dbo.trip_types (
            id         INT IDENTITY(1,1) NOT NULL,
            name       NVARCHAR(100) NOT NULL,
            created_at DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT PK_trip_types PRIMARY KEY (id),
            CONSTRAINT UQ_trip_types_name UNIQUE (name)
        )
        """
    )
    # Seed only names that aren't already present (idempotent).
    cursor.executemany(
        """
        INSERT INTO dbo.trip_types (name)
        SELECT ?
        WHERE NOT EXISTS (SELECT 1 FROM dbo.trip_types WHERE name = ?)
        """,
        [(name, name) for name in TRIP_TYPES],
    )
    conn.commit()
    cursor.close()
    conn.close()
    print(f"Ensured table `trip_types` and seeded {len(TRIP_TYPES)} candidate rows.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
