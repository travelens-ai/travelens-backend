"""Drop the redundant `state` name column from `cities` (state_id is canonical).

Run from project root:
    venv/bin/python migrations/drop_state_from_cities.py

Idempotent and guarded: no-op if the column is already gone; refuses to drop
while any city still has a NULL state_id (would lose the only state info).
"""
import os

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "193.203.184.43"),
    "port": 3306,
    "user": os.getenv("DB_USER", "u574280806_travelens"),
    "password": os.getenv("DB_PASSWORD", "Travelens@123"),
    "database": os.getenv("DB_NAME", "u574280806_travelens"),
    "ssl_disabled": True,
    "connection_timeout": 30,
}


def column_exists(cursor, table, column):
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.columns
           WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s""",
        (table, column),
    )
    return cursor.fetchone()[0] > 0


def columns(cursor, table):
    cursor.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = DATABASE() AND table_name = %s
           ORDER BY ordinal_position""",
        (table,),
    )
    return [r[0] for r in cursor.fetchall()]


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    if not column_exists(cursor, "cities", "state"):
        print("Column cities.state already dropped — nothing to do.")
        print("cities columns:", columns(cursor, "cities"))
        cursor.close()
        conn.close()
        return

    # Safety: don't drop the name while any row lacks a state_id.
    if not column_exists(cursor, "cities", "state_id"):
        raise SystemExit("Abort: cities.state_id does not exist. Run add_state_id_to_cities.py first.")
    cursor.execute("SELECT COUNT(*) FROM cities WHERE state_id IS NULL")
    null_count = cursor.fetchone()[0]
    if null_count:
        raise SystemExit(
            f"Abort: {null_count} cities have NULL state_id. Backfill before dropping `state`."
        )

    print("Before:", columns(cursor, "cities"))
    cursor.execute("ALTER TABLE cities DROP COLUMN state")
    conn.commit()
    print("After: ", columns(cursor, "cities"))
    print("Dropped cities.state. Done.")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
