import mysql.connector
from mysql.connector import pooling
import os
import threading

db_config = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
}

connection_pool = None
_db_initialized = False
_db_error = None


def get_pool():
    global connection_pool
    if connection_pool is None:
        connection_pool = pooling.MySQLConnectionPool(
            pool_name="travelens_pool",
            pool_size=5,
            pool_reset_session=True,
            **db_config
        )
    return connection_pool


def get_connection():
    return get_pool().get_connection()


def is_db_ready():
    return _db_initialized


def init_db():
    global _db_initialized, _db_error
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                phone VARCHAR(20),
                password_hash VARCHAR(255),
                age INT,
                gender ENUM('male', 'female', 'other'),
                trip_type VARCHAR(50),
                trip_companion VARCHAR(100),
                google_id VARCHAR(255) UNIQUE,
                profile_picture VARCHAR(500),
                is_verified BOOLEAN DEFAULT FALSE,
                reset_token VARCHAR(255),
                reset_token_expiry DATETIME,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS otp_verifications (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                otp VARCHAR(6) NOT NULL,
                purpose ENUM('signup', 'forgot_password') NOT NULL DEFAULT 'signup',
                is_verified BOOLEAN DEFAULT FALSE,
                expires_at DATETIME NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS itineraries (
                id INT AUTO_INCREMENT PRIMARY KEY,
                request_json JSON NOT NULL,
                response_json JSON,
                status ENUM('success', 'error') DEFAULT 'success',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                itinerary_id INT NOT NULL,
                device_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_favorite (user_id, itinerary_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (itinerary_id) REFERENCES itineraries(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT,
                itinerary_id INT NOT NULL,
                device_id VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (itinerary_id) REFERENCES itineraries(id) ON DELETE CASCADE
            )
        """)

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT FALSE")
        except mysql.connector.Error:
            pass

        conn.commit()
        cursor.close()
        conn.close()
        _db_initialized = True
        print("Database tables initialized successfully.")
    except Exception as e:
        _db_error = str(e)
        print(f"Database initialization failed (app will continue): {e}")


def init_db_async():
    threading.Thread(target=init_db, daemon=True).start()
