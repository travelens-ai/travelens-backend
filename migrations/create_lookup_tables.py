"""Create and seed lookup tables: group_types, food_preferences, activities.

Run from project root:
    venv/bin/python migrations/create_lookup_tables.py

Idempotent: tables are created only if absent, and rows are seeded with
INSERT IGNORE keyed on a UNIQUE column so re-running won't duplicate data.
"""
import os

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "193.203.184.43"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "u574280806_travelens"),
    "password": os.getenv("DB_PASSWORD", "Travelens@123"),
    "database": os.getenv("DB_NAME", "u574280806_travelens"),
    "ssl_disabled": True,
    "connection_timeout": 30,
}

GROUP_TYPES = [
    "Couples",
    "Friends",
    "Family with children",
    "Family without children",
    "Solo boy",
    "Solo girl",
    "Solo mix",
]

FOOD_PREFERENCES = [
    "South Indian",
    "North Indian",
    "Fast Food",
    "Street Food",
    "Bakery",
    "Any",
]

ACTIVITIES = [
    {"id": "pref1", "name": "Heritage & Culture", "icon": "🏛️"},
    {"id": "pref2", "name": "Beaches & Coastal", "icon": "🏖️"},
    {"id": "pref3", "name": "Spiritual & Temples", "icon": "🕉️"},
    {"id": "pref4", "name": "Food & Cuisine", "icon": "🍛"},
    {"id": "pref5", "name": "Nature & Wildlife", "icon": "🐯"},
    {"id": "pref6", "name": "Shopping & Bazaars", "icon": "🛍️"},
    {"id": "pref7", "name": "Adventure & Trekking", "icon": "🏔️"},
    {"id": "pref8", "name": "Arts & Crafts", "icon": "🎨"},
]


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS group_types (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS food_preferences (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS activities (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ref_id VARCHAR(20) NOT NULL UNIQUE,
            name VARCHAR(100) NOT NULL,
            icon VARCHAR(20),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.executemany(
        "INSERT IGNORE INTO group_types (name) VALUES (%s)",
        [(name,) for name in GROUP_TYPES],
    )
    print(f"group_types: seeded ({len(GROUP_TYPES)} candidate rows)")

    cursor.executemany(
        "INSERT IGNORE INTO food_preferences (name) VALUES (%s)",
        [(name,) for name in FOOD_PREFERENCES],
    )
    print(f"food_preferences: seeded ({len(FOOD_PREFERENCES)} candidate rows)")

    cursor.executemany(
        "INSERT IGNORE INTO activities (ref_id, name, icon) VALUES (%s, %s, %s)",
        [(a["id"], a["name"], a["icon"]) for a in ACTIVITIES],
    )
    print(f"activities: seeded ({len(ACTIVITIES)} candidate rows)")

    conn.commit()
    cursor.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
