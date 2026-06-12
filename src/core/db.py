import mysql.connector
import os
import threading
import time
def _get_db_config():
    return {
        "host": os.getenv("DB_HOST"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "database": os.getenv("DB_NAME"),
        "ssl_disabled": os.getenv("DB_SSL_DISABLED", "true").lower() == "true",
        "connection_timeout": 10,
    }

_connection = None
_db_initialized = False
_db_error = None
_lock = threading.Lock()


def get_connection():
    global _connection
    with _lock:
        try:
            if _connection is None or not _connection.is_connected():
                raise Exception("reconnect")
        except Exception:
            db_config = _get_db_config()
            print(f"[DB] Attempting connection to {db_config['host']}:{db_config['port']}/{db_config['database']}")
            _connection = mysql.connector.connect(**db_config)
            _connection.autocommit = False
        return _connection


def new_connection():
    """Return a fresh dedicated connection — use when a query may conflict with the shared connection."""
    conn = mysql.connector.connect(**_get_db_config())
    conn.autocommit = True
    return conn


def is_db_ready():
    return _db_initialized


def init_db():
    global _db_initialized, _db_error
    max_retries = 5
    db_config = _get_db_config()
    print(f"[DB] Connecting to database at {db_config['host']}:{db_config['port']}...")
    for attempt in range(max_retries):
        try:
            print(f"[DB] Connection attempt {attempt + 1}/{max_retries}...")
            conn = get_connection()
            print(f"[DB] Connected successfully to {db_config['database']}.")
            break
        except Exception as e:
            _db_error = str(e)
            print(f"[DB] Connection attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                print(f"[DB] Retrying in 5 seconds...")
                time.sleep(5)
            else:
                print("[DB] All connection attempts failed. App will continue without database.")
                return
    try:
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
                user_id VARCHAR(255) NOT NULL,
                itinerary_id INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_favorite (user_id, itinerary_id),
                FOREIGN KEY (itinerary_id) REFERENCES itineraries(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                itinerary_id INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (itinerary_id) REFERENCES itineraries(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cities (
                name VARCHAR(100) PRIMARY KEY,
                state VARCHAR(100),
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

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT FALSE")
        except mysql.connector.Error:
            pass

        conn.commit()
        cursor.close()
        _db_initialized = True
        print("[DB] All tables initialized successfully. Database is ready.")
    except Exception as e:
        _db_error = str(e)
        print(f"[DB] Table initialization failed: {e}")


def init_db_async():
    threading.Thread(target=init_db, daemon=True).start()
