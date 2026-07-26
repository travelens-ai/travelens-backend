from core.db import get_connection


def save_token(*, device_id, fcm_token, user_id=None):
    """Upsert an FCM token for a (device_id, user_id) pair. Updates the token in
    place when the pair already exists, else inserts a new row. Returns
    (row_id, (status, message, code)).

    user_id may be NULL, so the existence check uses an IS-NULL-aware match
    rather than a plain `= ?` (which never matches NULL)."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id FROM device_tokens
            WHERE device_id = ?
              AND ((user_id IS NULL AND ? IS NULL) OR user_id = ?)
            """,
            (device_id, user_id, user_id),
        )
        row = cursor.fetchone()
        if row:
            row_id = int(row[0])
            cursor.execute(
                "UPDATE device_tokens SET fcm_token = ?, updated_at = SYSUTCDATETIME() WHERE id = ?",
                (fcm_token, row_id),
            )
            conn.commit()
            return row_id, ("success", "Token updated", 200)

        cursor.execute(
            """
            INSERT INTO device_tokens (device_id, user_id, fcm_token)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?)
            """,
            (device_id, user_id, fcm_token),
        )
        row = cursor.fetchone()
        row_id = int(row[0]) if row and row[0] is not None else None
        conn.commit()
        return row_id, ("success", "Token saved", 201)
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def list_tokens():
    """Return all device-token registrations as a list of dicts with device_id
    and user_id (and timestamps) — the fcm_token value itself is deliberately
    omitted. Returns (rows, (status, message, code))."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, device_id, user_id, created_at, updated_at
            FROM device_tokens
            ORDER BY updated_at DESC
            """
        )
        rows = [
            {
                "id": int(r[0]),
                "device_id": r[1],
                "user_id": r[2],
                "created_at": r[3].isoformat() if r[3] is not None else None,
                "updated_at": r[4].isoformat() if r[4] is not None else None,
            }
            for r in cursor.fetchall()
        ]
        return rows, ("success", "Tokens fetched", 200)
    except Exception as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def update_user_id(*, device_id, user_id):
    """Set the user_id on a device's token row(s) — typically when a device-only
    registration (user_id NULL) is claimed after the user logs in. Returns
    (rows_updated, (status, message, code)).

    If a row for (device_id, user_id) already exists, the UNIQUE index would
    reject the update, so those conflicting device-only rows are removed first."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Drop any device-only row that would collide with the target pair.
        cursor.execute(
            """
            DELETE FROM device_tokens
            WHERE device_id = ? AND user_id IS NULL
              AND EXISTS (
                  SELECT 1 FROM device_tokens
                  WHERE device_id = ? AND user_id = ?
              )
            """,
            (device_id, device_id, user_id),
        )
        cursor.execute(
            "UPDATE device_tokens SET user_id = ?, updated_at = SYSUTCDATETIME() "
            "WHERE device_id = ? AND user_id IS NULL",
            (user_id, device_id),
        )
        updated = cursor.rowcount
        conn.commit()
        if updated == 0:
            return 0, ("error", "No device-only token found for this device", 404)
        return updated, ("success", "user_id updated", 200)
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()
