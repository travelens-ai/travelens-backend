"""Create the `sent_notifications` table (Azure SQL) — an audit log of every
push notification sent via /send-notification.

Schema:
    id            INT IDENTITY PRIMARY KEY
    title         NVARCHAR(255)   NULL          -- notification title
    body          NVARCHAR(MAX)   NULL          -- notification body/text
    image         NVARCHAR(MAX)   NULL          -- banner image URL
    link          NVARCHAR(MAX)   NULL          -- deep-link opened on tap
    data          NVARCHAR(MAX)   NULL          -- extra data payload (JSON string)
    target_type   NVARCHAR(20)    NOT NULL      -- 'token' | 'device' | 'user' | 'broadcast'
    target_value  NVARCHAR(MAX)   NULL          -- the token/device_id/user_id targeted (null for broadcast)
    targeted      INT             NULL          -- number of tokens targeted
    success_count INT             NULL          -- FCM-reported successes
    failure_count INT             NULL          -- FCM-reported failures
    pruned        INT             NULL          -- invalid tokens removed
    status        NVARCHAR(20)    NOT NULL      -- 'success' | 'error'
    error         NVARCHAR(MAX)   NULL          -- error message when status='error'
    created_at    DATETIME2       DEFAULT SYSUTCDATETIME()

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/create_sent_notifications_table.py

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
        IF OBJECT_ID('dbo.sent_notifications', 'U') IS NULL
        CREATE TABLE dbo.sent_notifications (
            id            INT IDENTITY(1,1) NOT NULL,
            title         NVARCHAR(255) NULL,
            body          NVARCHAR(MAX) NULL,
            image         NVARCHAR(MAX) NULL,
            link          NVARCHAR(MAX) NULL,
            data          NVARCHAR(MAX) NULL,
            target_type   NVARCHAR(20)  NOT NULL,
            target_value  NVARCHAR(MAX) NULL,
            targeted      INT           NULL,
            success_count INT           NULL,
            failure_count INT           NULL,
            pruned        INT           NULL,
            status        NVARCHAR(20)  NOT NULL,
            error         NVARCHAR(MAX) NULL,
            created_at    DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT PK_sent_notifications PRIMARY KEY (id)
        )
        """
    )
    conn.commit()
    cursor.close()
    conn.close()
    print("Ensured table `sent_notifications`.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
