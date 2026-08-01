"""Add a `status` column to `admin_users` (Azure SQL) — admin tier.

Two tiers: 'admin' (default) and 'super admin'. Only super admins may add or
delete other admins (enforced in src/auth/admin). The seeded owner account
(travelens.ai@gmail.com, override via ADMIN_SEED_EMAIL) is promoted to
'super admin'; every other/existing row defaults to 'admin'.

Schema addition:
    status  NVARCHAR(20)  NOT NULL  DEFAULT 'admin'

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/add_status_to_admin_users.py

Idempotent: the column is added only if missing; the super-admin promotion is a
plain UPDATE that's safe to re-run.
"""
import os
import struct

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SQL_COPT_SS_ACCESS_TOKEN = 1256

SUPER_ADMIN_EMAIL = os.getenv("ADMIN_SEED_EMAIL", "travelens.ai@gmail.com")


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
            "IF COL_LENGTH('dbo.admin_users', 'status') IS NULL "
            "ALTER TABLE dbo.admin_users ADD status NVARCHAR(20) NOT NULL DEFAULT 'admin'"
        )
        conn.commit()
        print("Ensured admin_users.status (NVARCHAR(20) NOT NULL DEFAULT 'admin')")

        cursor.execute(
            "UPDATE admin_users SET status = 'super admin' WHERE email = ?",
            (SUPER_ADMIN_EMAIL,),
        )
        conn.commit()
        print(f"Promoted {SUPER_ADMIN_EMAIL!r} to 'super admin' ({cursor.rowcount} row)")
    finally:
        cursor.close()
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
