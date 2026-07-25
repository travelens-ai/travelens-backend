"""Create the `device_tokens` table (Azure SQL) for FCM push tokens.

Schema:
    id          INT IDENTITY PRIMARY KEY
    device_id   NVARCHAR(255)  NOT NULL
    user_id     NVARCHAR(255)  NULL       -- null for device-only (not logged in)
    fcm_token   NVARCHAR(MAX)  NOT NULL
    created_at  DATETIME2      DEFAULT SYSUTCDATETIME()
    updated_at  DATETIME2      DEFAULT SYSUTCDATETIME()

Uniqueness / "combined primary key":
    A SQL Server PRIMARY KEY cannot contain a nullable column, and user_id must
    allow NULL. So the (device_id, user_id) pair is enforced by a UNIQUE index
    instead of a PK. SQL Server treats NULLs as equal in a unique index, so a
    device can have at most one device-only row (user_id NULL) plus one row per
    logged-in user — exactly the intended "one token per device+user" rule.

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/create_device_tokens_table.py

Idempotent: guarded by IF NOT EXISTS, so re-running is a no-op.
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
        IF OBJECT_ID('dbo.device_tokens', 'U') IS NULL
        CREATE TABLE dbo.device_tokens (
            id         INT IDENTITY(1,1) NOT NULL,
            device_id  NVARCHAR(255) NOT NULL,
            user_id    NVARCHAR(255) NULL,
            fcm_token  NVARCHAR(MAX) NOT NULL,
            created_at DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
            updated_at DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT PK_device_tokens PRIMARY KEY (id)
        )
        """
    )
    cursor.execute(
        """
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = 'UQ_device_tokens_device_user'
              AND object_id = OBJECT_ID('dbo.device_tokens')
        )
        CREATE UNIQUE INDEX UQ_device_tokens_device_user
            ON dbo.device_tokens (device_id, user_id)
        """
    )
    conn.commit()
    cursor.close()
    conn.close()
    print("Ensured table `device_tokens` and unique index on (device_id, user_id).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
