"""Create the `moderated_destinations` table (Azure SQL) — the admin-curated
sets of destinations served for GET /places?type=popular|trending.

Each (type, stage) pair holds an ordered set of cities:
    stage='draft'     -> the admin's working copy while moderating
    stage='published' -> the set actually served to the client

The client serves the published set (fixed order) when one exists; otherwise it
falls back to the legacy random curated pool. Admin edits stay in the draft
until submitted, which copies draft -> published.

Schema:
    id          INT IDENTITY PRIMARY KEY
    type        NVARCHAR(20)   NOT NULL   -- 'popular' | 'trending'
    stage       NVARCHAR(20)   NOT NULL   -- 'draft' | 'published'
    city        NVARCHAR(255)  NOT NULL   -- city name (matches cities.name)
    region      NVARCHAR(255)  NULL       -- state/region label
    position    INT            NOT NULL   -- order within (type, stage)
    created_at  DATETIME2      DEFAULT SYSUTCDATETIME()
    updated_at  DATETIME2      DEFAULT SYSUTCDATETIME()

Unique index on (type, stage, city) prevents a city appearing twice in a set.

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/create_moderated_destinations_table.py

Idempotent: guarded by IF OBJECT_ID / IF NOT EXISTS, so re-running is a no-op.
"""
import os
import struct
import sys

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
    cursor.execute(
        """
        IF OBJECT_ID('dbo.moderated_destinations', 'U') IS NULL
        CREATE TABLE dbo.moderated_destinations (
            id          INT IDENTITY(1,1) NOT NULL,
            type        NVARCHAR(20)  NOT NULL,
            stage       NVARCHAR(20)  NOT NULL,
            city        NVARCHAR(255) NOT NULL,
            region      NVARCHAR(255) NULL,
            position    INT           NOT NULL,
            created_at  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
            updated_at  DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT PK_moderated_destinations PRIMARY KEY (id)
        )
        """
    )
    cursor.execute(
        """
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = 'UX_moderated_destinations_type_stage_city'
              AND object_id = OBJECT_ID('dbo.moderated_destinations')
        )
        CREATE UNIQUE INDEX UX_moderated_destinations_type_stage_city
            ON dbo.moderated_destinations (type, stage, city)
        """
    )
    conn.commit()
    cursor.close()
    conn.close()
    print("Ensured table `moderated_destinations`.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
