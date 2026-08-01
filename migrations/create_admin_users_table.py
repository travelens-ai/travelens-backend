"""Create the `admin_users` table (Azure SQL) and seed one admin.

This is the ONLY table the admin panel introduces — every admin resource CRUD
operates on the app's existing tables. Admin auth is deliberately separate from
the regular `users` table so app users can never authenticate into the panel.

Admin login is **OTP-based** (email one-time code), so there is no password.
`password_hash` is kept NULLABLE only for possible future use; the seeded admin
has none.

Schema:
    id            INT IDENTITY PRIMARY KEY
    name          NVARCHAR(255)  NOT NULL
    email         NVARCHAR(255)  NOT NULL  UNIQUE
    password_hash NVARCHAR(500)  NULL                  -- unused for OTP login
    role          NVARCHAR(50)   NOT NULL DEFAULT 'admin'
    is_active     BIT            NOT NULL DEFAULT 1
    created_at    DATETIME2      DEFAULT SYSUTCDATETIME()
    updated_at    DATETIME2      DEFAULT SYSUTCDATETIME()

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/create_admin_users_table.py

Idempotent: the table is guarded by IF NOT EXISTS, the password_hash column is
made nullable if an older run created it NOT NULL, and the seed only inserts
when the seed email is absent — so re-running is a no-op.
"""
import os
import struct
import sys

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SQL_COPT_SS_ACCESS_TOKEN = 1256

# OTP-login admin — no password. Override via env if desired.
SEED_EMAIL = os.getenv("ADMIN_SEED_EMAIL", "travelens.ai@gmail.com")
SEED_NAME = os.getenv("ADMIN_SEED_NAME", "TraveLens Admin")


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
        IF OBJECT_ID('dbo.admin_users', 'U') IS NULL
        CREATE TABLE dbo.admin_users (
            id            INT IDENTITY(1,1) NOT NULL,
            name          NVARCHAR(255) NOT NULL,
            email         NVARCHAR(255) NOT NULL,
            password_hash NVARCHAR(500) NULL,
            role          NVARCHAR(50)  NOT NULL DEFAULT 'admin',
            is_active     BIT           NOT NULL DEFAULT 1,
            created_at    DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
            updated_at    DATETIME2     NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT PK_admin_users PRIMARY KEY (id),
            CONSTRAINT UQ_admin_users_email UNIQUE (email)
        )
        """
    )
    conn.commit()

    # If an earlier run created password_hash as NOT NULL, relax it (OTP login
    # stores no password).
    cursor.execute(
        """
        IF EXISTS (
            SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'admin_users' AND COLUMN_NAME = 'password_hash'
              AND IS_NULLABLE = 'NO'
        )
        ALTER TABLE dbo.admin_users ALTER COLUMN password_hash NVARCHAR(500) NULL
        """
    )
    conn.commit()

    # Seed the OTP-login admin (only if the seed email doesn't already exist).
    cursor.execute("SELECT id FROM admin_users WHERE email = ?", (SEED_EMAIL,))
    if cursor.fetchone() is None:
        cursor.execute(
            "INSERT INTO admin_users (name, email, password_hash, role) VALUES (?, ?, NULL, 'admin')",
            (SEED_NAME, SEED_EMAIL),
        )
        conn.commit()
        print(f"Seeded admin `{SEED_EMAIL}` (OTP login, no password).")
    else:
        print(f"Admin `{SEED_EMAIL}` already exists — skipped seed.")

    cursor.close()
    conn.close()
    print("Ensured table `admin_users`.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)
