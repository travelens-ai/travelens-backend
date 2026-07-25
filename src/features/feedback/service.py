from core.db import get_connection


def create_feedback(*, type, message, device_id, user_id=None,
                    name=None, email=None, phone=None, itinerary_id=None):
    """Insert one feedback row. `type`, `message` and `device_id` are required;
    the rest are optional. Returns (feedback_id, (status, message, code))."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO feedback
                (type, message, device_id, user_id, name, email, phone, itinerary_id)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (type, message, device_id, user_id, name, email, phone, itinerary_id),
        )
        row = cursor.fetchone()
        feedback_id = int(row[0]) if row and row[0] is not None else None
        conn.commit()
        return feedback_id, ("success", "Feedback submitted", 201)
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()
