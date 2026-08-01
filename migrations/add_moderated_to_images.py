"""Add a `moderated` flag to the `images` table (Azure SQL).

The admin place-images review workflow lists only *un*-moderated images, so an
admin can work through the queue: once an image is marked moderated (BIT = 1),
it drops out of GET /admin/place-images. Existing rows default to 0 (unreviewed)
so the whole backlog shows up until reviewed.

Schema addition:
    moderated  BIT  NOT NULL  DEFAULT 0

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/add_moderated_to_images.py

Idempotent: the column is added only if it doesn't already exist.
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
            "IF COL_LENGTH('dbo.images', 'moderated') IS NULL "
            "ALTER TABLE dbo.images ADD moderated BIT NOT NULL DEFAULT 0"
        )
        conn.commit()
        print("Ensured images.moderated (BIT NOT NULL DEFAULT 0)")
    finally:
        cursor.close()
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
