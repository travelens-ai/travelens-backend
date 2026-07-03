"""Make `itineraries.id` an IDENTITY (auto-increment) column.

Why: `store_itinerary()` does `INSERT ... ; SELECT SCOPE_IDENTITY()` to get the
new row id, which it returns to the client as `itinerary_id` (and streams in the
SSE `done` event). If `itineraries.id` is a plain INT (not IDENTITY),
SCOPE_IDENTITY() returns NULL, so `itinerary_id` comes back null.

SQL Server cannot ALTER a column to add IDENTITY in place — the property is
fixed at column creation. The only safe fix is to rebuild the table with an
IDENTITY id and copy the data over (preserving the original ids via
IDENTITY_INSERT), then reseed the identity past the max existing id.

This script introspects the LIVE schema so it rebuilds `itineraries` with the
exact same columns/types/nullability it currently has (only the `id` column
gains IDENTITY(1,1)). It also drops and recreates any foreign keys that
reference `itineraries.id` (e.g. history/favorites), plus the table's own PK.

Run from project root (needs an authenticated Azure session — `az login`):
    venv/bin/python migrations/make_itineraries_id_identity.py            # apply
    venv/bin/python migrations/make_itineraries_id_identity.py --dry-run  # print plan only

Idempotent: if `itineraries.id` is already IDENTITY, it does nothing.
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
TABLE = "itineraries"


def _connect():
    """Open a pyodbc connection using Azure AD token auth, mirroring core.db."""
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


def is_identity(cursor):
    cursor.execute(
        "SELECT c.is_identity FROM sys.columns c "
        "WHERE c.object_id = OBJECT_ID(?) AND c.name = 'id'",
        (f"dbo.{TABLE}",),
    )
    row = cursor.fetchone()
    if row is None:
        raise SystemExit(f"Table dbo.{TABLE} (or its `id` column) not found.")
    return bool(row[0])


def get_columns(cursor):
    """Return [(name, type_definition, is_nullable, is_id)] in column order,
    where type_definition is a ready-to-use DDL fragment like 'NVARCHAR(MAX)'."""
    cursor.execute(
        """
        SELECT c.name,
               t.name AS type_name,
               c.max_length,
               c.precision,
               c.scale,
               c.is_nullable,
               c.is_identity
        FROM sys.columns c
        JOIN sys.types t
          ON c.user_type_id = t.user_type_id
        WHERE c.object_id = OBJECT_ID(?)
        ORDER BY c.column_id
        """,
        (f"dbo.{TABLE}",),
    )
    cols = []
    for name, type_name, max_len, precision, scale, is_nullable, is_id in cursor.fetchall():
        cols.append((name, _type_ddl(type_name, max_len, precision, scale), bool(is_nullable), bool(is_id)))
    return cols


def _type_ddl(type_name, max_len, precision, scale):
    """Build the DDL type fragment for a column from sys.columns/sys.types info."""
    t = type_name.lower()
    # Length-bearing string/binary types. max_length is in bytes; nchar/nvarchar
    # store 2 bytes/char, and -1 means MAX.
    if t in ("nvarchar", "nchar"):
        if max_len == -1:
            return f"{type_name.upper()}(MAX)"
        return f"{type_name.upper()}({max_len // 2})"
    if t in ("varchar", "char", "varbinary", "binary"):
        if max_len == -1:
            return f"{type_name.upper()}(MAX)"
        return f"{type_name.upper()}({max_len})"
    if t in ("decimal", "numeric"):
        return f"{type_name.upper()}({precision},{scale})"
    if t in ("datetime2", "time", "datetimeoffset") and scale is not None:
        return f"{type_name.upper()}({scale})"
    # Fixed-size types (int, bigint, bit, datetime, float, etc.)
    return type_name.upper()


def get_referencing_fks(cursor):
    """Return FK constraints that reference dbo.<TABLE>, with enough info to
    recreate them: [(fk_name, child_schema, child_table, child_col, parent_col)]."""
    cursor.execute(
        """
        SELECT fk.name,
               SCHEMA_NAME(child.schema_id) AS child_schema,
               child.name AS child_table,
               cchild.name AS child_col,
               cparent.name AS parent_col
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
        JOIN sys.tables  child  ON child.object_id  = fk.parent_object_id
        JOIN sys.columns cchild ON cchild.object_id = fkc.parent_object_id
                                AND cchild.column_id = fkc.parent_column_id
        JOIN sys.columns cparent ON cparent.object_id = fkc.referenced_object_id
                                 AND cparent.column_id = fkc.referenced_column_id
        WHERE fk.referenced_object_id = OBJECT_ID(?)
        """,
        (f"dbo.{TABLE}",),
    )
    return cursor.fetchall()


def get_pk_name(cursor):
    cursor.execute(
        """
        SELECT kc.name
        FROM sys.key_constraints kc
        WHERE kc.parent_object_id = OBJECT_ID(?) AND kc.type = 'PK'
        """,
        (f"dbo.{TABLE}",),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def build_plan(cursor):
    cols = get_columns(cursor)
    fks = get_referencing_fks(cursor)
    pk_name = get_pk_name(cursor)

    col_names = [c[0] for c in cols]
    tmp = f"{TABLE}_identity_migration_tmp"

    # CREATE TABLE with IDENTITY on id, everything else identical.
    col_defs = []
    for name, type_ddl, nullable, is_id in cols:
        if name.lower() == "id":
            null_sql = "NOT NULL"
            col_defs.append(f"    [{name}] {type_ddl} IDENTITY(1,1) {null_sql}")
        else:
            null_sql = "NULL" if nullable else "NOT NULL"
            col_defs.append(f"    [{name}] {type_ddl} {null_sql}")
    create_sql = (
        f"CREATE TABLE dbo.{tmp} (\n" + ",\n".join(col_defs) + ",\n"
        f"    CONSTRAINT PK_{tmp} PRIMARY KEY ([id])\n);"
    )

    col_list = ", ".join(f"[{c}]" for c in col_names)
    copy_sql = (
        f"SET IDENTITY_INSERT dbo.{tmp} ON;\n"
        f"INSERT INTO dbo.{tmp} ({col_list})\n"
        f"SELECT {col_list} FROM dbo.{TABLE};\n"
        f"SET IDENTITY_INSERT dbo.{tmp} OFF;"
    )

    drop_fk_sql = [
        f"ALTER TABLE [{s}].[{ct}] DROP CONSTRAINT [{fkn}];"
        for (fkn, s, ct, _cc, _pc) in fks
    ]
    recreate_fk_sql = [
        f"ALTER TABLE [{s}].[{ct}] ADD CONSTRAINT [{fkn}] "
        f"FOREIGN KEY ([{cc}]) REFERENCES dbo.{TABLE} ([{pc}]);"
        for (fkn, s, ct, cc, pc) in fks
    ]

    swap_sql = (
        f"DROP TABLE dbo.{TABLE};\n"
        f"EXEC sp_rename 'dbo.{tmp}', '{TABLE}';\n"
        f"EXEC sp_rename 'dbo.PK_{tmp}', '{pk_name or ('PK_' + TABLE)}';"
    )

    return {
        "cols": cols,
        "fks": fks,
        "create": create_sql,
        "drop_fk": drop_fk_sql,
        "copy": copy_sql,
        "swap": swap_sql,
        "recreate_fk": recreate_fk_sql,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = ap.parse_args()

    conn = _connect()
    conn.autocommit = False
    cursor = conn.cursor()

    if is_identity(cursor):
        print(f"dbo.{TABLE}.id is already IDENTITY — nothing to do.")
        conn.close()
        return

    plan = build_plan(cursor)

    print(f"Columns on dbo.{TABLE}:")
    for name, type_ddl, nullable, is_id in plan["cols"]:
        flags = " IDENTITY(new)" if name.lower() == "id" else ""
        print(f"  {name:24} {type_ddl:16} {'NULL' if nullable else 'NOT NULL'}{flags}")
    if plan["fks"]:
        print("Foreign keys referencing this table (will be dropped & recreated):")
        for (fkn, s, ct, cc, pc) in plan["fks"]:
            print(f"  {fkn}: [{s}].[{ct}].[{cc}] -> {TABLE}.[{pc}]")
    else:
        print("No foreign keys reference this table.")

    statements = (
        plan["drop_fk"]
        + [plan["create"], plan["copy"], plan["swap"]]
        + plan["recreate_fk"]
    )

    print("\n--- SQL to execute ---")
    for s in statements:
        print(s)
    print("--- end SQL ---\n")

    if args.dry_run:
        print("Dry run: no changes made.")
        conn.close()
        return

    try:
        for s in statements:
            cursor.execute(s)
        # Reseed the identity so the next insert continues past the max id.
        cursor.execute(f"SELECT ISNULL(MAX(id), 0) FROM dbo.{TABLE}")
        max_id = cursor.fetchone()[0]
        cursor.execute(f"DBCC CHECKIDENT ('dbo.{TABLE}', RESEED, {max_id})")
        conn.commit()
        print(f"Done. dbo.{TABLE}.id is now IDENTITY, reseeded at {max_id}.")
    except Exception:
        conn.rollback()
        print("Migration failed — rolled back. No changes were made.", file=sys.stderr)
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
