import hashlib
import secrets
import json
from datetime import datetime, timedelta

import mysql.connector

from core.db import get_connection
from auth.email_utils import generate_otp, send_otp_email
from auth.jwt_utils import create_token


def hash_password(password):
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}:{hashed.hex()}"


def verify_password(password, stored_hash):
    salt, hash_val = stored_hash.split(":")
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return hashed.hex() == hash_val


def send_otp_internal(email, purpose):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if not cursor.fetchone():
            return None, ("success", "If the email exists, an OTP has been sent", 200)

        otp = generate_otp()
        expires_at = datetime.now() + timedelta(minutes=10)

        cursor.execute("DELETE FROM otp_verifications WHERE email = %s AND purpose = %s", (email, purpose))
        cursor.execute(
            "INSERT INTO otp_verifications (email, otp, purpose, expires_at) VALUES (%s, %s, %s, %s)",
            (email, otp, purpose, expires_at),
        )
        conn.commit()
        send_otp_email(email, otp, purpose)
        return otp, ("success", "OTP sent to your email", 200)
    except mysql.connector.Error as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()


def create_otp_record(email, purpose):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if purpose == "signup":
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cursor.fetchone():
                return None, ("error", "Email already registered", 409)

        if purpose == "forgot_password":
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            if not cursor.fetchone():
                return None, ("success", "If the email exists, an OTP has been sent", 200)

        otp = generate_otp()
        expires_at = datetime.now() + timedelta(minutes=10)

        cursor.execute(
            "DELETE FROM otp_verifications WHERE email = %s AND purpose = %s",
            (email, purpose),
        )
        cursor.execute(
            "INSERT INTO otp_verifications (email, otp, purpose, expires_at) VALUES (%s, %s, %s, %s)",
            (email, otp, purpose, expires_at),
        )
        conn.commit()
        send_otp_email(email, otp, purpose)
        return otp, ("success", "OTP sent to your email", 200)
    except mysql.connector.Error as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()


def verify_otp_record(email, otp, purpose):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM otp_verifications WHERE email = %s AND otp = %s AND purpose = %s AND is_verified = FALSE AND expires_at > NOW()",
            (email, otp, purpose),
        )
        record = cursor.fetchone()
        if not record:
            return False, ("error", "Invalid or expired OTP", 400)

        cursor.execute(
            "UPDATE otp_verifications SET is_verified = TRUE WHERE id = %s",
            (record["id"],),
        )
        conn.commit()
        return True, ("success", "OTP verified successfully", 200)
    except mysql.connector.Error as e:
        conn.rollback()
        return False, ("error", str(e), 500)
    finally:
        cursor.close()


def create_user(name, email, password, phone=None, age=None, gender=None, trip_type=None, trip_companion=None, device_id=None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id FROM otp_verifications WHERE email = %s AND purpose = 'signup' AND is_verified = TRUE",
            (email,),
        )
        if not cursor.fetchone():
            return None, ("error", "Email not verified. Please verify OTP first", 403)

        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            return None, ("error", "Email already registered", 409)

        password_hash = hash_password(password)
        cursor.execute(
            """INSERT INTO users (name, email, phone, password_hash, age, gender, trip_type, trip_companion, is_verified)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)""",
            (name, email, phone, password_hash, age, gender, trip_type, trip_companion),
        )
        conn.commit()
        user_id = cursor.lastrowid

        if device_id:
            cursor.execute("UPDATE favorites SET user_id = %s WHERE user_id = %s", (str(user_id), device_id))
            cursor.execute("UPDATE history SET user_id = %s WHERE user_id = %s", (str(user_id), device_id))
            conn.commit()

        cursor.execute("DELETE FROM otp_verifications WHERE email = %s AND purpose = 'signup'", (email,))
        conn.commit()

        token = create_token(user_id, email)
        user = {"id": user_id, "name": name, "email": email, "phone": phone, "age": age, "gender": gender, "trip_type": trip_type, "trip_companion": trip_companion}
        return {"token": token, "user": user}, ("success", "User registered successfully", 201)
    except mysql.connector.Error as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()


def authenticate_user(email, password):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if not user or not user["password_hash"]:
            return None, ("error", "Invalid email or password", 401)
        if not user["is_verified"]:
            return None, ("error", "Email not verified. Please verify your email first", 403)
        if not verify_password(password, user["password_hash"]):
            return None, ("error", "Invalid email or password", 401)

        token = create_token(user["id"], user["email"])
        return {"token": token, "user": {
            "id": user["id"], "name": user["name"], "email": user["email"],
            "phone": user["phone"], "age": user["age"], "gender": user["gender"],
            "trip_type": user["trip_type"], "trip_companion": user["trip_companion"],
            "profile_picture": user["profile_picture"],
        }}, ("success", "Login successful", 200)
    except mysql.connector.Error as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()


def reset_user_password(email, otp, new_password):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id FROM otp_verifications WHERE email = %s AND otp = %s AND purpose = 'forgot_password' AND is_verified = FALSE AND expires_at > NOW()",
            (email, otp),
        )
        if not cursor.fetchone():
            return False, ("error", "Invalid or expired OTP", 400)

        password_hash = hash_password(new_password)
        cursor.execute("UPDATE users SET password_hash = %s WHERE email = %s", (password_hash, email))
        cursor.execute("DELETE FROM otp_verifications WHERE email = %s AND purpose = 'forgot_password'", (email,))
        conn.commit()
        return True, ("success", "Password reset successfully", 200)
    except mysql.connector.Error as e:
        conn.rollback()
        return False, ("error", str(e), 500)
    finally:
        cursor.close()


def update_user_profile(user_id, data):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if "old_password" in data and "new_password" in data:
            cursor.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            if not user or not verify_password(data["old_password"], user["password_hash"]):
                return None, ("error", "Current password is incorrect", 401)
            password_hash = hash_password(data["new_password"])
            cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
            conn.commit()
            return {}, ("success", "Password updated successfully", 200)

        updatable_fields = ["name", "phone", "age", "gender", "trip_type", "trip_companion", "profile_picture"]
        updates = []
        values = []
        for field in updatable_fields:
            if field in data:
                updates.append(f"{field} = %s")
                values.append(data[field])

        if not updates:
            return None, ("error", "No fields to update", 400)

        values.append(user_id)
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", values)
        conn.commit()

        cursor.execute(
            "SELECT id, name, email, phone, age, gender, trip_type, trip_companion, profile_picture FROM users WHERE id = %s",
            (user_id,),
        )
        updated_user = cursor.fetchone()
        return {"user": updated_user}, ("success", "Profile updated successfully", 200)
    except mysql.connector.Error as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()


def google_upsert_user(google_id, email, name, picture, device_id=None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM users WHERE google_id = %s OR email = %s", (google_id, email))
        user = cursor.fetchone()

        if user:
            if not user["google_id"]:
                cursor.execute(
                    "UPDATE users SET google_id = %s, profile_picture = %s, is_verified = TRUE WHERE id = %s",
                    (google_id, picture, user["id"]),
                )
                conn.commit()
            token = create_token(user["id"], user["email"])
            return {"token": token, "user": {
                "id": user["id"], "name": user["name"], "email": user["email"],
                "phone": user["phone"], "age": user["age"], "gender": user["gender"],
                "trip_type": user["trip_type"], "trip_companion": user["trip_companion"],
                "profile_picture": user.get("profile_picture") or picture,
            }, "is_new": False}, ("success", "Login successful", 200)
        else:
            cursor.execute(
                "INSERT INTO users (name, email, google_id, profile_picture, is_verified) VALUES (%s, %s, %s, %s, TRUE)",
                (name, email, google_id, picture),
            )
            conn.commit()
            user_id = cursor.lastrowid
            token = create_token(user_id, email)
            return {"token": token, "user": {"id": user_id, "name": name, "email": email, "profile_picture": picture}, "is_new": True}, ("success", "User registered via Google", 201)
    except mysql.connector.Error as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()


