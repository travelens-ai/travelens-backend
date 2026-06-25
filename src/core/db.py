import os
import struct
import threading
import time

import pyodbc
from azure.identity import DefaultAzureCredential

_SQL_COPT_SS_ACCESS_TOKEN = 1256

_connection = None
_db_initialized = False
_db_error = None
_lock = threading.Lock()

_credential = DefaultAzureCredential()


def _get_token_bytes():
    token = _credential.get_token("https://database.windows.net//.default")
    token_bytes = token.token.encode("utf-16-le")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


def _build_conn_str():
    server = os.getenv("AZURE_SQL_SERVER")
    database = os.getenv("AZURE_SQL_DATABASE")
    return (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )


def _ping(conn):
    try:
        conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def get_connection():
    global _connection
    with _lock:
        if _connection is None or not _ping(_connection):
            print(f"[DB] Connecting to {os.getenv('AZURE_SQL_SERVER')}/{os.getenv('AZURE_SQL_DATABASE')}")
            _connection = pyodbc.connect(
                _build_conn_str(),
                attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: _get_token_bytes()},
            )
            _connection.autocommit = False
        return _connection


def new_connection():
    """Return a fresh dedicated connection — use when a query may conflict with the shared connection."""
    conn = pyodbc.connect(
        _build_conn_str(),
        attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: _get_token_bytes()},
    )
    conn.autocommit = True
    return conn


def _rows_to_dicts(cursor):
    cols = [col[0] for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetch_dicts(query, params=None):
    """Run a read-only query on a fresh dedicated connection and return rows as
    a list of dicts. Used for bulk data loads so they don't contend with the
    shared connection across threads."""
    conn = new_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        rows = _rows_to_dicts(cursor)
        cursor.close()
        return rows
    finally:
        conn.close()


def execute_write(query, params=None):
    """Run a single write (INSERT) on a fresh dedicated connection and return
    the new row's identity value."""
    conn = new_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query + "; SELECT SCOPE_IDENTITY()", params or ())
        cursor.nextset()
        row = cursor.fetchone()
        last_id = int(row[0]) if row and row[0] is not None else None
        cursor.close()
        return last_id
    finally:
        conn.close()


def is_db_ready():
    return _db_initialized


def init_db():
    global _db_initialized, _db_error
    max_retries = 5
    print(f"[DB] Connecting to {os.getenv('AZURE_SQL_SERVER')}/{os.getenv('AZURE_SQL_DATABASE')}...")
    for attempt in range(max_retries):
        try:
            print(f"[DB] Connection attempt {attempt + 1}/{max_retries}...")
            get_connection()
            _db_initialized = True
            print("[DB] Connected successfully. Database is ready.")
            return
        except Exception as e:
            _db_error = str(e)
            print(f"[DB] Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                print("[DB] Retrying in 5 seconds...")
                time.sleep(5)
            else:
                print("[DB] All connection attempts failed. App will continue without database.")


def init_db_async():
    threading.Thread(target=init_db, daemon=True).start()
