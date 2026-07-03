"""Make the `id` column an IDENTITY (auto-increment) on tables that lack it.

Why: several tables were created / bulk-loaded with a plain INT `id` instead of
an IDENTITY column, so inserts don't auto-generate ids and `SCOPE_IDENTITY()` /
`OUTPUT INSERTED.id` return nothing. This rebuilds each named table with an
IDENTITY id + primary key, preserving existing rows and their ids, then reseeds
the identity past the current max.

SQL Server can't ALTER a column to add IDENTITY in place, so each table is
rebuilt: create a tmp table with IDENTITY id → copy rows with IDENTITY_INSERT ON
(preserving ids) → drop old + rename tmp → reseed. Schema is introspected live so
every column/type/nullability is reproduced faithfully; only `id` changes.

There are currently NO foreign keys in this database, so rebuilds are
self-contained. The script asserts this and aborts if any FK is found (a rebuild
would silently drop it) — handle FKs explicitly before re-running if that changes.

A table can only be migrated if its `id` values are non-null and unique (a PRIMARY
KEY requires this). Pass --delete-null-ids to first delete rows whose id IS NULL.
Tables with duplicate ids are reported and skipped (needs a manual decision).

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/add_identity_to_tables.py --dry-run
    venv/bin/python migrations/add_identity_to_tables.py --delete-null-ids

Idempotent: tables whose id is already IDENTITY are skipped.
"""
import argparse
import os
import struct
import sys

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_SQL_COPT_SS_ACCESS_TOKEN = 1256

# Tables to migrate. `images` is intentionally excluded: it has duplicate ids
# and a 1:1 place_image_map that would need untangling first.
TARGET_TABLES = [
    "activities",
    "cities",
    "country",
    "favorites",
    "food_preferences",
    "group_types",
    "history",
    "hotels",
    "otp_verifications",
    "places",
    "restaurants",
    "states",
    "users",
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


def any_foreign_keys(cursor):
    cursor.execute("SELECT COUNT(*) FROM sys.foreign_keys")
    return cursor.fetchone()[0] > 0


def is_identity(cursor, table):
    cursor.execute(
        "SELECT c.is_identity FROM sys.columns c "
        "WHERE c.object_id = OBJECT_ID(?) AND c.name = 'id'",
        (f"dbo.{table}",),
    )
    row = cursor.fetchone()
    if row is None:
        return None  # no id column
    return bool(row[0])


def id_health(cursor, table):
    """Return (total, nonnull, distinct) for the id column."""
    cursor.execute(
        f"SELECT COUNT(*), COUNT(id), COUNT(DISTINCT id) FROM dbo.{table}"
    )
    return cursor.fetchone()


def get_columns(cursor, table):
    cursor.execute(
        """
        SELECT c.name, t.name AS type_name, c.max_length, c.precision,
               c.scale, c.is_nullable
        FROM sys.columns c
        JOIN sys.types t ON c.user_type_id = t.user_type_id
        WHERE c.object_id = OBJECT_ID(?)
        ORDER BY c.column_id
        """,
        (f"dbo.{table}",),
    )
    cols = []
    for name, type_name, max_len, precision, scale, is_nullable in cursor.fetchall():
        cols.append((name, _type_ddl(type_name, max_len, precision, scale), bool(is_nullable)))
    return cols


def _type_ddl(type_name, max_len, precision, scale):
    t = type_name.lower()
    if t in ("nvarchar", "nchar"):
        return f"{type_name.upper()}(MAX)" if max_len == -1 else f"{type_name.upper()}({max_len // 2})"
    if t in ("varchar", "char", "varbinary", "binary"):
        return f"{type_name.upper()}(MAX)" if max_len == -1 else f"{type_name.upper()}({max_len})"
    if t in ("decimal", "numeric"):
        return f"{type_name.upper()}({precision},{scale})"
    if t in ("datetime2", "time", "datetimeoffset") and scale is not None:
        return f"{type_name.upper()}({scale})"
    return type_name.upper()


def get_pk_name(cursor, table):
    cursor.execute(
        "SELECT name FROM sys.key_constraints "
        "WHERE parent_object_id = OBJECT_ID(?) AND type = 'PK'",
        (f"dbo.{table}",),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def build_statements(cursor, table):
    cols = get_columns(cursor, table)
    pk_name = get_pk_name(cursor, table)
    col_names = [c[0] for c in cols]
    tmp = f"{table}_identity_tmp"

    col_defs = []
    for name, type_ddl, nullable in cols:
        if name.lower() == "id":
            col_defs.append(f"    [{name}] {type_ddl} IDENTITY(1,1) NOT NULL")
        else:
            col_defs.append(f"    [{name}] {type_ddl} {'NULL' if nullable else 'NOT NULL'}")
    create_sql = (
        f"CREATE TABLE dbo.{tmp} (\n" + ",\n".join(col_defs) + ",\n"
        f"    CONSTRAINT PK_{tmp} PRIMARY KEY ([id])\n);"
    )

    col_list = ", ".join(f"[{c}]" for c in col_names)
    copy_sql = (
        f"SET IDENTITY_INSERT dbo.{tmp} ON;\n"
        f"INSERT INTO dbo.{tmp} ({col_list})\n"
        f"SELECT {col_list} FROM dbo.{table};\n"
        f"SET IDENTITY_INSERT dbo.{tmp} OFF;"
    )
    swap_sql = (
        f"DROP TABLE dbo.{table};\n"
        f"EXEC sp_rename 'dbo.{tmp}', '{table}';\n"
        f"EXEC sp_rename 'dbo.PK_{tmp}', '{pk_name or ('PK_' + table)}';"
    )
    return [create_sql, copy_sql, swap_sql]


def migrate_table(conn, cursor, table, delete_null_ids, dry_run):
    ident = is_identity(cursor, table)
    if ident is None:
        print(f"[{table}] no `id` column — skipped")
        return
    if ident:
        print(f"[{table}] already IDENTITY — skipped")
        return

    total, nonnull, distinct = id_health(cursor, table)
    nulls = total - nonnull
    dups = nonnull - distinct

    if dups > 0:
        print(f"[{table}] SKIPPED — {dups} duplicate id(s); needs manual dedupe")
        return

    if nulls > 0:
        if not delete_null_ids:
            print(f"[{table}] SKIPPED — {nulls} NULL id(s); rerun with --delete-null-ids")
            return
        if dry_run:
            print(f"[{table}] would DELETE {nulls} NULL-id row(s)")
        else:
            cursor.execute(f"DELETE FROM dbo.{table} WHERE id IS NULL")
            print(f"[{table}] deleted {cursor.rowcount} NULL-id row(s)")

    statements = build_statements(cursor, table)

    if dry_run:
        print(f"[{table}] would rebuild with IDENTITY id ({nonnull} rows preserved):")
        for s in statements:
            print("    " + s.replace("\n", "\n    "))
        return

    try:
        for s in statements:
            cursor.execute(s)
        cursor.execute(f"SELECT ISNULL(MAX(id), 0) FROM dbo.{table}")
        max_id = cursor.fetchone()[0]
        cursor.execute(f"DBCC CHECKIDENT ('dbo.{table}', RESEED, {max_id})")
        conn.commit()
        print(f"[{table}] DONE — id is IDENTITY, reseeded at {max_id}")
    except Exception as e:
        conn.rollback()
        print(f"[{table}] FAILED — rolled back: {e}", file=sys.stderr)
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    ap.add_argument("--delete-null-ids", action="store_true",
                    help="delete rows whose id IS NULL before migrating")
    ap.add_argument("--table", action="append", dest="tables",
                    help="limit to specific table(s); repeatable. Defaults to the built-in list.")
    args = ap.parse_args()

    tables = args.tables or TARGET_TABLES

    conn = _connect()
    conn.autocommit = False
    cursor = conn.cursor()

    if any_foreign_keys(cursor):
        print("Aborting: foreign keys exist in the database. A table rebuild would "
              "drop them. Handle FKs explicitly before running.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    for table in tables:
        migrate_table(conn, cursor, table, args.delete_null_ids, args.dry_run)

    cursor.close()
    conn.close()
    print("\nAll done." if not args.dry_run else "\nDry run complete — no changes made.")


if __name__ == "__main__":
    main()
