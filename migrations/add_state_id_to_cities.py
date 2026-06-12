"""Add `state_id` to the `cities` table and populate it from `states`.

Matches cities.state (state name) to states.name to fill states.id.
Run from project root:
    venv/bin/python migrations/add_state_id_to_cities.py

Idempotent: skips adding the column / FK if they already exist.
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


def fk_exists(cursor, table, constraint):
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.table_constraints
           WHERE table_schema = DATABASE() AND table_name = %s AND constraint_name = %s""",
        (table, constraint),
    )
    return cursor.fetchone()[0] > 0


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    if not column_exists(cursor, "cities", "state_id"):
        cursor.execute("ALTER TABLE cities ADD COLUMN state_id INT NULL AFTER state")
        print("Added column cities.state_id")
    else:
        print("Column cities.state_id already exists")

    # Populate state_id by matching the state name
    cursor.execute(
        """UPDATE cities c
           JOIN states s ON c.state = s.name
           SET c.state_id = s.id"""
    )
    print(f"Populated state_id for {cursor.rowcount} city rows")

    # Add FK constraint (only if not present)
    if not fk_exists(cursor, "cities", "fk_cities_state"):
        cursor.execute(
            """ALTER TABLE cities
               ADD CONSTRAINT fk_cities_state FOREIGN KEY (state_id)
               REFERENCES states (id) ON DELETE SET NULL"""
        )
        print("Added FK cities.state_id -> states.id")
    else:
        print("FK fk_cities_state already exists")

    conn.commit()

    # Report any rows still unmatched
    cursor.execute("SELECT COUNT(*) FROM cities WHERE state_id IS NULL")
    null_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM cities")
    total = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    print(f"cities: {total} total, {total - null_count} mapped, {null_count} unmapped")
    print("Done.")


if __name__ == "__main__":
    main()
