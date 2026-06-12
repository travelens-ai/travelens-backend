"""
One-time migration: CSV + pkl → MySQL cities and places tables.

Run from project root:
    .venv/bin/python3 scripts/migrate_csv_to_db.py
"""

import os
import pickle
import sys

import pandas as pd
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

CSV_FILE = "indian_travel_places.csv"
PKL_FILE = "city_coords.pkl"


def bool_val(v):
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        import math
        if math.isnan(v):
            return False
        return bool(v)
    if isinstance(v, str):
        return v.strip().upper() == "TRUE"
    return bool(v)


def str_or_none(v):
    import math
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v).strip()
    return s if s else None


def float_or_none(v):
    try:
        f = float(v)
        import math
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def int_or_none(v):
    try:
        f = float(v)
        import math
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def migrate():
    print("Connecting to DB...")
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    print("Connected.")

    # ── cities ──────────────────────────────────────────────────────────────
    print(f"\nLoading {PKL_FILE}...")
    with open(PKL_FILE, "rb") as f:
        coords = pickle.load(f)
    print(f"  {len(coords)} cities found in pkl.")

    # Also pull state info from CSV
    df = pd.read_csv(CSV_FILE)
    city_state = (
        df[["city", "state"]]
        .drop_duplicates(subset="city")
        .set_index("city")["state"]
        .to_dict()
    )

    print("Creating tables if not exist...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cities (
            name VARCHAR(100) PRIMARY KEY,
            state_id INT,
            lat DECIMAL(9,6),
            lon DECIMAL(9,6)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS places (
            id INT AUTO_INCREMENT PRIMARY KEY,
            city VARCHAR(100) NOT NULL,
            state VARCHAR(100),
            name VARCHAR(255) NOT NULL,
            type VARCHAR(100),
            dist_airport DECIMAL(8,2),
            dist_bus_stand DECIMAL(8,2),
            dist_railway DECIMAL(8,2),
            rating DECIMAL(3,1),
            num_ratings INT,
            best_month VARCHAR(100),
            famous_activities TEXT,
            prefer_friends BOOLEAN DEFAULT FALSE,
            prefer_couple BOOLEAN DEFAULT FALSE,
            prefer_family_children BOOLEAN DEFAULT FALSE,
            prefer_family_no_children BOOLEAN DEFAULT FALSE,
            famous_activities_rating TEXT,
            image VARCHAR(255),
            INDEX idx_city (city),
            INDEX idx_type (type),
            INDEX idx_rating (rating)
        )
    """)
    conn.commit()

    print("Inserting cities...")
    # Resolve state names to state_id via the states table
    cursor.execute("SELECT id, name FROM states")
    state_id_by_name = {name: sid for sid, name in cursor.fetchall()}

    city_rows = []
    for city_raw, (lat, lon) in coords.items():
        if not isinstance(city_raw, str) or not city_raw.strip():
            continue
        city = city_raw.strip().lower()
        state = city_state.get(city_raw, city_state.get(city_raw.title(), None))
        state_id = state_id_by_name.get(state)
        city_rows.append((city, state_id, lat, lon))

    cursor.executemany(
        """INSERT INTO cities (name, state_id, lat, lon)
           VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE lat=VALUES(lat), lon=VALUES(lon), state_id=VALUES(state_id)""",
        city_rows,
    )
    conn.commit()
    print(f"  {len(city_rows)} cities inserted/updated.")

    # ── places ──────────────────────────────────────────────────────────────
    print(f"\nLoading {CSV_FILE}...")
    print(f"  {len(df)} rows found.")

    # Check if places table already has data
    cursor.execute("SELECT COUNT(*) FROM places")
    existing = cursor.fetchone()[0]
    if existing > 0:
        answer = input(f"  places table already has {existing} rows. Re-insert? [y/N] ").strip().lower()
        if answer != "y":
            print("Skipping places migration.")
            cursor.close()
            conn.close()
            return

        print("  Clearing existing places...")
        cursor.execute("DELETE FROM places")
        conn.commit()

    print("Inserting places (batch of 500)...")
    place_rows = []
    for _, row in df.iterrows():
        place_rows.append((
            str_or_none(row.get("city")).lower() if str_or_none(row.get("city")) else None,
            str_or_none(row.get("state")),
            str_or_none(row.get("name")),
            str_or_none(row.get("type")),
            float_or_none(row.get("distance from airport")),
            float_or_none(row.get("distance from bus stand")),
            float_or_none(row.get("distance from railway station")),
            float_or_none(row.get("rating")),
            int_or_none(row.get("no of rating")),
            str_or_none(row.get("best month to visit")),
            str_or_none(row.get("famous activities")),
            bool_val(row.get("prefer for friends", False)),
            bool_val(row.get("prefer for couple", False)),
            bool_val(row.get("prefer for family with children", False)),
            bool_val(row.get("prefer for family without children", False)),
            str_or_none(row.get("famous activities with rating")),
            str_or_none(row.get("image")),
        ))

    BATCH = 500
    for i in range(0, len(place_rows), BATCH):
        batch = place_rows[i:i + BATCH]
        cursor.executemany(
            """INSERT INTO places
               (city, state, name, type, dist_airport, dist_bus_stand, dist_railway,
                rating, num_ratings, best_month, famous_activities,
                prefer_friends, prefer_couple, prefer_family_children,
                prefer_family_no_children, famous_activities_rating, image)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            batch,
        )
        conn.commit()
        print(f"  Inserted {min(i + BATCH, len(place_rows))}/{len(place_rows)}...")

    print(f"\nDone. {len(place_rows)} places inserted.")
    cursor.close()
    conn.close()


if __name__ == "__main__":
    if not os.path.exists(CSV_FILE):
        print(f"ERROR: {CSV_FILE} not found. Run from project root.")
        sys.exit(1)
    if not os.path.exists(PKL_FILE):
        print(f"ERROR: {PKL_FILE} not found. Run from project root.")
        sys.exit(1)
    migrate()
