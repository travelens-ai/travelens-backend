import os
import hashlib
import hmac
import json
import time
import base64
import secrets
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify
import mysql.connector
import requests

from db import get_connection, is_db_ready

auth_bp = Blueprint("auth", __name__)


@auth_bp.before_request
def check_db_ready():
    if not is_db_ready():
        return jsonify({"status": "error", "message": "Database is connecting, please try again shortly"}), 503

JWT_SECRET = os.getenv("JWT_SECRET_KEY", "travelens-jwt-secret-key-2024")
JWT_EXPIRY = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES", 86400))
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")


def _hash_password(password):
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}:{hashed.hex()}"


def _verify_password(password, stored_hash):
    salt, hash_val = stored_hash.split(":")
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return hashed.hex() == hash_val


def _create_token(user_id, email):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload_data = {
        "user_id": user_id,
        "email": email,
        "exp": int(time.time()) + JWT_EXPIRY,
        "iat": int(time.time()),
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).decode().rstrip("=")
    signature = hmac.HMAC(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"{header}.{payload}.{signature}"


def _decode_token(token):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, signature = parts
        expected_sig = hmac.HMAC(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).hexdigest()
        if signature != expected_sig:
            return None
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        payload_data = json.loads(base64.urlsafe_b64decode(payload))
        if payload_data["exp"] < int(time.time()):
            return None
        return payload_data
    except Exception:
        return None


def _generate_otp():
    return str(random.randint(100000, 999999))


def _send_otp_email(email, otp, purpose="signup"):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print(f"SMTP not configured. OTP for {email}: {otp}")
        return True

    subject = "Travelens - Email Verification OTP" if purpose == "signup" else "Travelens - Password Reset OTP"
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2 style="color: #2c3e50;">Travelens</h2>
        <p>Your OTP for {'email verification' if purpose == 'signup' else 'password reset'} is:</p>
        <h1 style="color: #3498db; letter-spacing: 5px; font-size: 36px;">{otp}</h1>
        <p>This OTP is valid for <strong>10 minutes</strong>.</p>
        <p style="color: #7f8c8d;">If you didn't request this, please ignore this email.</p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_EMAIL
    msg["To"] = email
    msg.attach(MIMEText(body, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Failed to send OTP email: {e}")
        return False


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        if not token:
            return jsonify({"status": "error", "message": "Token is missing"}), 401
        payload = _decode_token(token)
        if not payload:
            return jsonify({"status": "error", "message": "Token is invalid or expired"}), 401
        request.user_id = payload["user_id"]
        request.user_email = payload["email"]
        return f(*args, **kwargs)
    return decorated


@auth_bp.route("/send-otp", methods=["POST"])
def send_otp():
    """Send OTP to email for verification
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
          properties:
            email:
              type: string
              example: john@example.com
            purpose:
              type: string
              enum: [signup, forgot_password]
              default: signup
              example: signup
    responses:
      200:
        description: OTP sent successfully
      409:
        description: Email already registered (for signup purpose)
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    email = data.get("email")
    purpose = data.get("purpose", "signup")

    if not email:
        return jsonify({"status": "error", "message": "Email is required"}), 400

    if purpose not in ("signup", "forgot_password"):
        return jsonify({"status": "error", "message": "Invalid purpose. Use 'signup' or 'forgot_password'"}), 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        if purpose == "signup":
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cursor.fetchone():
                return jsonify({"status": "error", "message": "Email already registered"}), 409

        if purpose == "forgot_password":
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            if not cursor.fetchone():
                return jsonify({"status": "success", "message": "If the email exists, an OTP has been sent"}), 200

        otp = _generate_otp()
        expires_at = datetime.now() + timedelta(minutes=10)

        # Invalidate previous OTPs for this email and purpose
        cursor.execute(
            "DELETE FROM otp_verifications WHERE email = %s AND purpose = %s",
            (email, purpose),
        )

        cursor.execute(
            "INSERT INTO otp_verifications (email, otp, purpose, expires_at) VALUES (%s, %s, %s, %s)",
            (email, otp, purpose, expires_at),
        )
        conn.commit()

        _send_otp_email(email, otp, purpose)
        return jsonify({"status": "success", "message": "OTP sent to your email"}), 200

    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/verify-otp", methods=["POST"])
def verify_otp():
    """Verify OTP sent to email
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
            - otp
          properties:
            email:
              type: string
              example: john@example.com
            otp:
              type: string
              example: "123456"
            purpose:
              type: string
              enum: [signup, forgot_password]
              default: signup
              example: signup
    responses:
      200:
        description: OTP verified successfully
      400:
        description: Invalid or expired OTP
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    email = data.get("email")
    otp = data.get("otp")
    purpose = data.get("purpose", "signup")

    if not email or not otp:
        return jsonify({"status": "error", "message": "Email and OTP are required"}), 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT * FROM otp_verifications WHERE email = %s AND otp = %s AND purpose = %s AND is_verified = FALSE AND expires_at > NOW()",
            (email, otp, purpose),
        )
        record = cursor.fetchone()

        if not record:
            return jsonify({"status": "error", "message": "Invalid or expired OTP"}), 400

        cursor.execute(
            "UPDATE otp_verifications SET is_verified = TRUE WHERE id = %s",
            (record["id"],),
        )
        conn.commit()

        return jsonify({"status": "success", "message": "OTP verified successfully", "email": email, "purpose": purpose}), 200

    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/signup", methods=["POST"])
def signup():
    """Register a new user (OTP must be verified first)
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - name
            - email
            - password
          properties:
            name:
              type: string
              example: John Doe
            email:
              type: string
              example: john@example.com
            password:
              type: string
              example: SecurePass123
            phone:
              type: string
              example: "+919876543210"
            age:
              type: integer
              example: 28
            gender:
              type: string
              enum: [male, female, other]
              example: male
            trip_type:
              type: string
              description: "solo, friends, family"
              example: solo
            trip_companion:
              type: string
              description: "male, female, mix, with_children, without_children"
              example: male
    responses:
      201:
        description: User registered successfully
      403:
        description: Email not verified via OTP
      409:
        description: Email already registered
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    name = data.get("name")
    email = data.get("email")
    phone = data.get("phone")
    password = data.get("password")
    age = data.get("age")
    gender = data.get("gender")
    trip_type = data.get("trip_type")
    trip_companion = data.get("trip_companion")

    if not name or not email or not password:
        return jsonify({"status": "error", "message": "Name, email and password are required"}), 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Check if OTP was verified for this email
        cursor.execute(
            "SELECT id FROM otp_verifications WHERE email = %s AND purpose = 'signup' AND is_verified = TRUE",
            (email,),
        )
        if not cursor.fetchone():
            return jsonify({"status": "error", "message": "Email not verified. Please verify OTP first"}), 403

        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            return jsonify({"status": "error", "message": "Email already registered"}), 409

        password_hash = _hash_password(password)
        cursor.execute(
            """INSERT INTO users (name, email, phone, password_hash, age, gender, trip_type, trip_companion, is_verified)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)""",
            (name, email, phone, password_hash, age, gender, trip_type, trip_companion),
        )
        conn.commit()
        user_id = cursor.lastrowid

        # Cleanup used OTP records
        cursor.execute("DELETE FROM otp_verifications WHERE email = %s AND purpose = 'signup'", (email,))
        conn.commit()

        token = _create_token(user_id, email)
        return jsonify({
            "status": "success",
            "message": "User registered successfully",
            "token": token,
            "user": {"id": user_id, "name": name, "email": email, "phone": phone, "age": age, "gender": gender, "trip_type": trip_type, "trip_companion": trip_companion},
        }), 201
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/login", methods=["POST"])
def login():
    """Login with email and password
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
            - password
          properties:
            email:
              type: string
              example: john@example.com
            password:
              type: string
              example: SecurePass123
    responses:
      200:
        description: Login successful, returns JWT token
      401:
        description: Invalid email or password
      403:
        description: Email not verified
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"status": "error", "message": "Email and password are required"}), 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if not user or not user["password_hash"]:
            return jsonify({"status": "error", "message": "Invalid email or password"}), 401

        if not user["is_verified"]:
            return jsonify({"status": "error", "message": "Email not verified. Please verify your email first"}), 403

        if not _verify_password(password, user["password_hash"]):
            return jsonify({"status": "error", "message": "Invalid email or password"}), 401

        token = _create_token(user["id"], user["email"])
        return jsonify({
            "status": "success",
            "message": "Login successful",
            "token": token,
            "user": {
                "id": user["id"],
                "name": user["name"],
                "email": user["email"],
                "phone": user["phone"],
                "age": user["age"],
                "gender": user["gender"],
                "trip_type": user["trip_type"],
                "trip_companion": user["trip_companion"],
                "profile_picture": user["profile_picture"],
            },
        }), 200
    except mysql.connector.Error as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    """Send OTP for password reset
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
          properties:
            email:
              type: string
              example: john@example.com
    responses:
      200:
        description: OTP sent to email
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    email = data.get("email")
    if not email:
        return jsonify({"status": "error", "message": "Email is required"}), 400

    return send_otp_internal(email, "forgot_password")


def send_otp_internal(email, purpose):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if not cursor.fetchone():
            return jsonify({"status": "success", "message": "If the email exists, an OTP has been sent"}), 200

        otp = _generate_otp()
        expires_at = datetime.now() + timedelta(minutes=10)

        cursor.execute("DELETE FROM otp_verifications WHERE email = %s AND purpose = %s", (email, purpose))
        cursor.execute(
            "INSERT INTO otp_verifications (email, otp, purpose, expires_at) VALUES (%s, %s, %s, %s)",
            (email, otp, purpose, expires_at),
        )
        conn.commit()

        _send_otp_email(email, otp, purpose)
        return jsonify({"status": "success", "message": "OTP sent to your email"}), 200

    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    """Reset password using OTP
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
            - otp
            - new_password
          properties:
            email:
              type: string
              example: john@example.com
            otp:
              type: string
              example: "123456"
            new_password:
              type: string
              example: NewSecurePass456
    responses:
      200:
        description: Password reset successfully
      400:
        description: Invalid or expired OTP
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    email = data.get("email")
    otp = data.get("otp")
    new_password = data.get("new_password")

    if not email or not otp or not new_password:
        return jsonify({"status": "error", "message": "Email, OTP and new password are required"}), 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Verify OTP
        cursor.execute(
            "SELECT id FROM otp_verifications WHERE email = %s AND otp = %s AND purpose = 'forgot_password' AND is_verified = FALSE AND expires_at > NOW()",
            (email, otp),
        )
        otp_record = cursor.fetchone()

        if not otp_record:
            return jsonify({"status": "error", "message": "Invalid or expired OTP"}), 400

        # Update password
        password_hash = _hash_password(new_password)
        cursor.execute("UPDATE users SET password_hash = %s WHERE email = %s", (password_hash, email))

        # Cleanup OTP
        cursor.execute("DELETE FROM otp_verifications WHERE email = %s AND purpose = 'forgot_password'", (email,))
        conn.commit()

        return jsonify({"status": "success", "message": "Password reset successfully"}), 200
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/update", methods=["PUT"])
@token_required
def update_profile():
    """Update user profile or password
    ---
    tags:
      - User
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            name:
              type: string
              example: Updated Name
            phone:
              type: string
              example: "+919999999999"
            age:
              type: integer
              example: 30
            gender:
              type: string
              enum: [male, female, other]
            trip_type:
              type: string
              example: family
            trip_companion:
              type: string
              example: with_children
            profile_picture:
              type: string
              example: https://example.com/pic.jpg
            old_password:
              type: string
              description: Required for password change
              example: OldPass123
            new_password:
              type: string
              description: Required for password change
              example: NewPass456
    responses:
      200:
        description: Profile or password updated successfully
      401:
        description: Token missing/invalid or wrong old password
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        user_id = request.user_id

        # Handle password change with old password
        if "old_password" in data and "new_password" in data:
            cursor.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            if not user or not _verify_password(data["old_password"], user["password_hash"]):
                return jsonify({"status": "error", "message": "Current password is incorrect"}), 401
            password_hash = _hash_password(data["new_password"])
            cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
            conn.commit()
            return jsonify({"status": "success", "message": "Password updated successfully"}), 200

        # Handle profile update
        updatable_fields = ["name", "phone", "age", "gender", "trip_type", "trip_companion", "profile_picture"]
        updates = []
        values = []
        for field in updatable_fields:
            if field in data:
                updates.append(f"{field} = %s")
                values.append(data[field])

        if not updates:
            return jsonify({"status": "error", "message": "No fields to update"}), 400

        values.append(user_id)
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", values)
        conn.commit()

        cursor.execute("SELECT id, name, email, phone, age, gender, trip_type, trip_companion, profile_picture FROM users WHERE id = %s", (user_id,))
        updated_user = cursor.fetchone()

        return jsonify({"status": "success", "message": "Profile updated successfully", "user": updated_user}), 200
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/google-login", methods=["POST"])
def google_login():
    """Login or register via Google
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - id_token
          properties:
            id_token:
              type: string
              description: Google ID token from frontend OAuth
              example: eyJhbGciOiJSUzI1NiIs...
    responses:
      200:
        description: Login successful
      201:
        description: New user registered via Google
      401:
        description: Invalid Google token
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    id_token = data.get("id_token")
    if not id_token:
        return jsonify({"status": "error", "message": "Google ID token is required"}), 400

    try:
        google_response = requests.get(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={id_token}"
        )
        if google_response.status_code != 200:
            return jsonify({"status": "error", "message": "Invalid Google token"}), 401

        google_data = google_response.json()
        google_id = google_data.get("sub")
        email = google_data.get("email")
        name = google_data.get("name", "")
        picture = google_data.get("picture", "")

        if not email:
            return jsonify({"status": "error", "message": "Could not get email from Google"}), 400

    except requests.RequestException:
        return jsonify({"status": "error", "message": "Failed to verify Google token"}), 500

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM users WHERE google_id = %s OR email = %s", (google_id, email))
        user = cursor.fetchone()

        if user:
            if not user["google_id"]:
                cursor.execute("UPDATE users SET google_id = %s, profile_picture = %s, is_verified = TRUE WHERE id = %s", (google_id, picture, user["id"]))
                conn.commit()
            token = _create_token(user["id"], user["email"])
            return jsonify({
                "status": "success",
                "message": "Login successful",
                "token": token,
                "user": {
                    "id": user["id"],
                    "name": user["name"],
                    "email": user["email"],
                    "phone": user["phone"],
                    "age": user["age"],
                    "gender": user["gender"],
                    "trip_type": user["trip_type"],
                    "trip_companion": user["trip_companion"],
                    "profile_picture": user.get("profile_picture") or picture,
                },
            }), 200
        else:
            cursor.execute(
                """INSERT INTO users (name, email, google_id, profile_picture, is_verified)
                   VALUES (%s, %s, %s, %s, TRUE)""",
                (name, email, google_id, picture),
            )
            conn.commit()
            user_id = cursor.lastrowid
            token = _create_token(user_id, email)
            return jsonify({
                "status": "success",
                "message": "User registered via Google",
                "token": token,
                "user": {"id": user_id, "name": name, "email": email, "profile_picture": picture},
            }), 201
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/favorite", methods=["POST"])
@token_required
def add_favorite():
    """Add itinerary to favorites
    ---
    tags:
      - Favorites
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - itinerary_id
          properties:
            itinerary_id:
              type: integer
              example: 1
            device_id:
              type: string
              example: "abc123-device-uuid"
    responses:
      201:
        description: Added to favorites
      409:
        description: Already in favorites
      401:
        description: Token missing or invalid
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    itinerary_id = data.get("itinerary_id")
    device_id = data.get("device_id")
    if not itinerary_id:
        return jsonify({"status": "error", "message": "itinerary_id is required"}), 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        user_id = request.user_id

        cursor.execute("SELECT id FROM itineraries WHERE id = %s", (itinerary_id,))
        if not cursor.fetchone():
            return jsonify({"status": "error", "message": "Itinerary not found"}), 404

        cursor.execute(
            "INSERT INTO favorites (user_id, itinerary_id, device_id) VALUES (%s, %s, %s)",
            (user_id, itinerary_id, device_id),
        )
        conn.commit()

        return jsonify({"status": "success", "message": "Added to favorites"}), 201
    except mysql.connector.IntegrityError:
        return jsonify({"status": "error", "message": "Already in favorites"}), 409
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/favorite", methods=["DELETE"])
@token_required
def remove_favorite():
    """Remove itinerary from favorites
    ---
    tags:
      - Favorites
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - itinerary_id
          properties:
            itinerary_id:
              type: integer
              example: 1
    responses:
      200:
        description: Removed from favorites
      404:
        description: Favorite not found
      401:
        description: Token missing or invalid
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    itinerary_id = data.get("itinerary_id")
    if not itinerary_id:
        return jsonify({"status": "error", "message": "itinerary_id is required"}), 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        user_id = request.user_id

        cursor.execute(
            "DELETE FROM favorites WHERE user_id = %s AND itinerary_id = %s",
            (user_id, itinerary_id),
        )
        conn.commit()

        if cursor.rowcount == 0:
            return jsonify({"status": "error", "message": "Favorite not found"}), 404

        return jsonify({"status": "success", "message": "Removed from favorites"}), 200
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/history", methods=["POST"])
def add_history():
    """Add itinerary to history
    ---
    tags:
      - History
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - itinerary_id
            - device_id
          properties:
            itinerary_id:
              type: integer
              example: 1
            device_id:
              type: string
              example: "abc123-device-uuid"
    responses:
      201:
        description: Added to history
      400:
        description: Missing required fields
      404:
        description: Itinerary not found
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    itinerary_id = data.get("itinerary_id")
    device_id = data.get("device_id")

    if not itinerary_id or not device_id:
        return jsonify({"status": "error", "message": "itinerary_id and device_id are required"}), 400

    user_id = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token_data = _decode_token(auth_header.split(" ")[1])
        if token_data:
            user_id = token_data["user_id"]

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT id FROM itineraries WHERE id = %s", (itinerary_id,))
        if not cursor.fetchone():
            return jsonify({"status": "error", "message": "Itinerary not found"}), 404

        cursor.execute(
            "INSERT INTO history (user_id, itinerary_id, device_id) VALUES (%s, %s, %s)",
            (user_id, itinerary_id, device_id),
        )
        conn.commit()

        return jsonify({"status": "success", "message": "Added to history"}), 201
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
