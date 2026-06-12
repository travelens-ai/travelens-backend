"""Create `country` and `states` tables and seed India + its states/UTs.

Run from project root:
    venv/bin/python migrations/create_country_states.py

Idempotent: uses CREATE TABLE IF NOT EXISTS and INSERT ... ON DUPLICATE KEY
UPDATE so re-running won't create duplicates.
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


def get_connection():
    return mysql.connector.connect(**DB_CONFIG)

# 28 states + 8 union territories (current as of 2020 reorganization)
INDIAN_STATES_AND_UTS = [
    # States
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand",
    "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal",
    # Union Territories
    "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]


def main():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS country (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            name        VARCHAR(100) NOT NULL UNIQUE,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS states (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            name        VARCHAR(150) NOT NULL,
            country_id  INT NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_state_country (name, country_id),
            CONSTRAINT fk_states_country FOREIGN KEY (country_id)
                REFERENCES country (id) ON DELETE CASCADE
        )
        """
    )

    # Insert India (idempotent) and fetch its id
    cursor.execute(
        "INSERT INTO country (name) VALUES (%s) ON DUPLICATE KEY UPDATE name = name",
        ("India",),
    )
    cursor.execute("SELECT id FROM country WHERE name = %s", ("India",))
    india_id = cursor.fetchone()[0]

    # Insert all states/UTs (idempotent)
    cursor.executemany(
        "INSERT INTO states (name, country_id) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE name = name",
        [(name, india_id) for name in INDIAN_STATES_AND_UTS],
    )

    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM states WHERE country_id = %s", (india_id,))
    state_count = cursor.fetchone()[0]
    cursor.close()

    print(f"country: India (id={india_id})")
    print(f"states: {state_count} rows for India ({len(INDIAN_STATES_AND_UTS)} seeded)")
    print("Done.")


if __name__ == "__main__":
    main()
