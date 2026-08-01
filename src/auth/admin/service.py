"""Admin auth logic — OTP (email one-time code) based, no password.

Reuses the app's existing OTP machinery: the `otp_verifications` table and
`auth.email_utils` (generate_otp / send_otp_email). Admin OTPs use a dedicated
purpose (`admin_login`) so they never collide with app-user signup/reset codes,
and an OTP is only ever issued to an email that exists in `admin_users` — app
users can't request an admin code.

Returns the codebase-standard (payload, (status, message, code)) tuple.
"""
from datetime import datetime, timedelta

from core.db import get_connection
from auth.email_utils import generate_otp, send_otp_email
from auth.admin.jwt_utils import create_admin_token

_PURPOSE = "admin_login"
_OTP_TTL_MINUTES = 10


def _fetchone_dict(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    cols = [col[0] for col in cursor.description]
    return dict(zip(cols, row))


def _public_admin(admin):
    return {
        "id": admin["id"],
        "name": admin["name"],
        "email": admin["email"],
        "role": admin["role"],
        # Admin tier: 'admin' | 'super admin' (see add_status_to_admin_users).
        "status": admin.get("status", "admin"),
    }


def request_login_otp(email):
    """Issue a login OTP to an admin's email.

    Returns (registered: bool, (status, msg, code)). `registered` is True only
    when the email is a known active admin (an OTP is generated/sent in that
    case), False otherwise — the route surfaces this so the client can tell the
    caller whether the email is valid."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, is_active FROM admin_users WHERE email = ?", (email,)
        )
        admin = _fetchone_dict(cursor)
        if not admin or not admin.get("is_active", True):
            return False, ("error", "invalid email id", 200)

        otp = generate_otp()
        expires_at = datetime.now() + timedelta(minutes=_OTP_TTL_MINUTES)
        cursor.execute(
            "DELETE FROM otp_verifications WHERE email = ? AND purpose = ?",
            (email, _PURPOSE),
        )
        # Set is_verified = 0 explicitly: the column has no DB default, so
        # omitting it leaves NULL, which the "is_verified = 0" check below (and
        # in the app-user flow) would never match.
        cursor.execute(
            "INSERT INTO otp_verifications (email, otp, purpose, expires_at, is_verified) VALUES (?, ?, ?, ?, 0)",
            (email, otp, _PURPOSE, expires_at),
        )
        conn.commit()
        send_otp_email(email, otp, purpose="signup")  # generic verification email
        return True, ("success", "An OTP send successfully", 200)
    except Exception as e:
        conn.rollback()
        return False, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def verify_login_otp(email, otp):
    """Verify an admin login OTP and, on success, mint an admin JWT.

    Returns ({token, admin}, (status, msg, code))."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id FROM otp_verifications
            WHERE email = ? AND otp = ? AND purpose = ?
              AND ISNULL(is_verified, 0) = 0 AND expires_at > GETDATE()
            """,
            (email, otp, _PURPOSE),
        )
        record = _fetchone_dict(cursor)
        if not record:
            return None, ("error", "Invalid or expired OTP", 400)

        # Load the admin the code belongs to (must still exist and be active).
        cursor.execute("SELECT * FROM admin_users WHERE email = ?", (email,))
        admin = _fetchone_dict(cursor)
        if not admin or not admin.get("is_active", True):
            return None, ("error", "Admin account not found or disabled", 403)

        # Burn the OTP so it can't be replayed.
        cursor.execute(
            "UPDATE otp_verifications SET is_verified = 1 WHERE id = ?",
            (record["id"],),
        )
        conn.commit()

        token = create_admin_token(
            admin["id"], admin["email"], admin.get("status", "admin")
        )
        return {"token": token, "admin": _public_admin(admin)}, ("success", "Logged in", 200)
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def get_admin(admin_id):
    """Fetch one admin by id (without the password hash), or None."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM admin_users WHERE id = ?", (admin_id,))
        admin = _fetchone_dict(cursor)
        return _public_admin(admin) if admin else None
    finally:
        cursor.close()
        conn.close()


def get_admin_status(admin_id):
    """Return an admin's tier ('admin' | 'super admin') from the live DB, or None
    if the id no longer exists. Used by super_admin_required for a fresh check."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT status FROM admin_users WHERE id = ? AND is_active = 1", (admin_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()
        conn.close()


# --- admin management (super-admin only routes) -----------------------------
_VALID_STATUSES = ("admin", "super admin")


def list_admins():
    """All admins (public fields only), newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM admin_users ORDER BY id DESC")
        cols = [c[0] for c in cursor.description]
        admins = [_public_admin(dict(zip(cols, r))) for r in cursor.fetchall()]
        return admins, ("success", "OK", 200)
    except Exception as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def create_admin(name, email, status="admin"):
    """Add an admin (OTP login — no password). status defaults to 'admin'.
    Returns ({...admin}, ...); 400 on missing/invalid input or duplicate email."""
    name = (name or "").strip()
    email = (email or "").strip().lower()
    status = (status or "admin").strip().lower()
    if not name or not email:
        return None, ("error", "name and email are required", 400)
    if status not in _VALID_STATUSES:
        return None, ("error", "status must be 'admin' or 'super admin'", 400)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM admin_users WHERE email = ?", (email,))
        if cursor.fetchone() is not None:
            return None, ("error", "An admin with this email already exists", 400)

        cursor.execute(
            "INSERT INTO admin_users (name, email, role, status, is_active) "
            "OUTPUT INSERTED.id VALUES (?, ?, 'admin', ?, 1)",
            (name, email, status),
        )
        new_id = cursor.fetchone()[0]
        conn.commit()
        cursor.execute("SELECT * FROM admin_users WHERE id = ?", (new_id,))
        return _public_admin(_fetchone_dict(cursor)), ("success", "Created", 201)
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 400)
    finally:
        cursor.close()
        conn.close()


def delete_admin(admin_id, *, requester_id):
    """Delete an admin by id. Guards: can't delete yourself, and can't delete the
    last remaining super admin (would lock everyone out of admin management)."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, status FROM admin_users WHERE id = ?", (admin_id,))
        target = _fetchone_dict(cursor)
        if not target:
            return False, ("error", "Admin not found", 404)
        if int(admin_id) == int(requester_id):
            return False, ("error", "You cannot delete your own account", 400)
        if target["status"] == "super admin":
            cursor.execute("SELECT COUNT(*) FROM admin_users WHERE status = 'super admin'")
            if int(cursor.fetchone()[0]) <= 1:
                return False, ("error", "Cannot delete the last super admin", 400)

        cursor.execute("DELETE FROM admin_users WHERE id = ?", (admin_id,))
        conn.commit()
        return True, ("success", "Deleted", 200)
    except Exception as e:
        conn.rollback()
        return False, ("error", str(e), 400)
    finally:
        cursor.close()
        conn.close()
