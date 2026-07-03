import json

from core.db import get_connection
from core.images import with_image_urls
from core.ads import interleave_ads


def _fetchone_dict(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    cols = [col[0] for col in cursor.description]
    return dict(zip(cols, row))


def _fetchall_dicts(cursor):
    cols = [col[0] for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def add_favorite(user_id, itinerary_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM itineraries WHERE id = ?", (itinerary_id,))
        if not _fetchone_dict(cursor):
            return False, ("error", "Itinerary not found", 404)

        # T-SQL equivalent of INSERT IGNORE: silently skip on unique constraint violation
        cursor.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM favorites WHERE user_id = ? AND itinerary_id = ?)
                INSERT INTO favorites (user_id, itinerary_id) VALUES (?, ?)
            """,
            (str(user_id), itinerary_id, str(user_id), itinerary_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return True, ("success", "Already in favorites", 200)
        return True, ("success", "Added to favorites", 201)
    except Exception as e:
        conn.rollback()
        return False, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def get_favorites(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT f.id, f.itinerary_id, f.created_at, i.response_json
               FROM favorites f
               LEFT JOIN itineraries i ON f.itinerary_id = i.id
               WHERE f.user_id = ?
               ORDER BY f.id DESC""",
            (user_id,),
        )
        favorites = _fetchall_dicts(cursor)
        for fav in favorites:
            itinerary = json.loads(fav["response_json"]) if fav["response_json"] else None
            fav["itinerary"] = with_image_urls(itinerary) if itinerary else None
            del fav["response_json"]
            if fav.get("created_at"):
                fav["created_at"] = fav["created_at"].isoformat()
        return interleave_ads(favorites, "favorites"), ("success", "", 200)
    except Exception as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def remove_favorite(user_id, itinerary_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM favorites WHERE user_id = ? AND itinerary_id = ?",
            (str(user_id), itinerary_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return False, ("error", "Favorite not found", 404)
        return True, ("success", "Removed from favorites", 200)
    except Exception as e:
        conn.rollback()
        return False, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def add_history(user_id, itinerary_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM itineraries WHERE id = ?", (itinerary_id,))
        if not _fetchone_dict(cursor):
            return False, ("error", "Itinerary not found", 404)

        cursor.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM history WHERE user_id = ? AND itinerary_id = ?)
                INSERT INTO history (user_id, itinerary_id) VALUES (?, ?)
            """,
            (str(user_id), itinerary_id, str(user_id), itinerary_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return True, ("success", "Already in history", 200)
        return True, ("success", "Added to history", 201)
    except Exception as e:
        conn.rollback()
        return False, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def get_history(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT h.id, h.itinerary_id, h.created_at, i.response_json
               FROM history h
               LEFT JOIN itineraries i ON h.itinerary_id = i.id
               WHERE h.user_id = ?
               ORDER BY h.id DESC""",
            (user_id,),
        )
        history = _fetchall_dicts(cursor)
        for item in history:
            itinerary = json.loads(item["response_json"]) if item["response_json"] else None
            item["itinerary"] = with_image_urls(itinerary) if itinerary else None
            del item["response_json"]
            if item.get("created_at"):
                item["created_at"] = item["created_at"].isoformat()
        return interleave_ads(history, "history"), ("success", "", 200)
    except Exception as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()
