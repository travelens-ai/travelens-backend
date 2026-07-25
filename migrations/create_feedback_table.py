"""Create the `feedback` table (Azure SQL).

Schema:
    id            INT IDENTITY PRIMARY KEY
    type          NVARCHAR(50)   NOT NULL   -- feedback category (bug, suggestion, ...)
    message       NVARCHAR(MAX)  NOT NULL
    device_id     NVARCHAR(255)  NOT NULL
    user_id       NVARCHAR(255)  NULL       -- set when a logged-in user submits
    name          NVARCHAR(255)  NULL
    email         NVARCHAR(255)  NULL
    phone         NVARCHAR(50)   NULL
    itinerary_id  INT            NULL
    created_at    DATETIME2      DEFAULT SYSUTCDATETIME()

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/create_feedback_table.py

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
        IF OBJECT_ID('dbo.feedback', 'U') IS NULL
        CREATE TABLE dbo.feedback (
            id           INT IDENTITY(1,1) NOT NULL,
            type         NVARCHAR(50)  NOT NULL,
            message      NVARCHAR(MAX) NOT NULL,
            device_id    NVARCHAR(255) NOT NULL,
            user_id      NVARCHAR(255) NULL,
            name         NVARCHAR(255) NULL,
            email        NVARCHAR(255) NULL,
            phone        NVARCHAR(50)  NULL,
            itinerary_id INT           NULL,
            created_at   DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT PK_feedback PRIMARY KEY (id)
        )
        """
    )
    conn.commit()
    cursor.close()
    conn.close()
    print("Ensured table `feedback`.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
