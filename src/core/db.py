import os
import struct
import threading
import time

import pyodbc
from azure.identity import DefaultAzureCredential
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

_SQL_COPT_SS_ACCESS_TOKEN = 1256

_credential = DefaultAzureCredential()
_db_initialized = False


def _get_token_bytes():
    token = _credential.get_token("https://database.windows.net//.default")
    token_bytes = token.token.encode("utf-16-le")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


def _make_connection():
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={os.getenv('AZURE_SQL_SERVER')};"
        f"DATABASE={os.getenv('AZURE_SQL_DATABASE')};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: _get_token_bytes()})


_engine = create_engine(
    "mssql+pyodbc://",
    creator=_make_connection,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,  # recycle before Azure AD token expires (1 hr)
)


def get_connection():
    """Borrow a connection from the pool. Caller MUST call conn.close() to return it."""
    return _engine.raw_connection()


def new_connection():
    """Alias for get_connection() — pool replaces the old per-request bare connection."""
    return _engine.raw_connection()


def fetch_dicts(query, params=None):
    """Run a read query and return rows as a list of dicts."""
    conn = _engine.raw_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
        cursor.close()
        return rows
    finally:
        conn.close()


def execute_write(query, params=None):
    """Run a single INSERT and return the new row's identity value."""
    conn = _engine.raw_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query + "; SELECT SCOPE_IDENTITY()", params or ())
        cursor.nextset()
        row = cursor.fetchone()
        last_id = int(row[0]) if row and row[0] is not None else None
        cursor.close()
        conn.commit()
        return last_id
    finally:
        conn.close()


def is_db_ready():
    return _db_initialized


def init_db():
    global _db_initialized
    print(f"[DB] Connecting to {os.getenv('AZURE_SQL_SERVER')}/{os.getenv('AZURE_SQL_DATABASE')}...")
    for attempt in range(5):
        try:
            print(f"[DB] Connection attempt {attempt + 1}/5...")
            conn = _engine.raw_connection()
            conn.cursor().execute("SELECT 1")
            conn.close()
            _db_initialized = True
            print("[DB] Pool connected successfully. Database is ready.")
            return
        except Exception as e:
            print(f"[DB] Attempt {attempt + 1}/5 failed: {e}")
            if attempt < 4:
                print("[DB] Retrying in 5 seconds...")
                time.sleep(5)
    print("[DB] All connection attempts failed. App will continue without database.")


def init_db_async():
    threading.Thread(target=init_db, daemon=True).start()
