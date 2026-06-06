import mysql.connector
import os
import threading
import time
def _get_db_config():
    return {
        "host": os.getenv("DB_HOST", "193.203.184.43"),
        "port": 3306,
        "user": os.getenv("DB_USER", "u574280806_travelens"),
        "password": os.getenv("DB_PASSWORD", "Travelens@123"),
        "database": os.getenv("DB_NAME", "u574280806_travelens"),
        "ssl_disabled": True,
        "connection_timeout": 10,
    }

_connection = None
_db_initialized = False
_db_error = None
_lock = threading.Lock()


def get_connection():
    global _connection
    with _lock:
        if _connection is None or not _connection.is_connected():
            db_config = _get_db_config()
            print(f"[DB] Attempting connection to {db_config['host']}:{db_config['port']}/{db_config['database']}")
            _connection = mysql.connector.connect(**db_config)
            _connection.autocommit = False
        return _connection


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
