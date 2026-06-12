import json

import mysql.connector

from core.db import get_connection


def add_favorite(user_id, itinerary_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM itineraries WHERE id = %s", (itinerary_id,))
        if not cursor.fetchone():
            return False, ("error", "Itinerary not found", 404)

        cursor.execute(
            "INSERT INTO favorites (user_id, itinerary_id) VALUES (%s, %s)",
            (str(user_id), itinerary_id),
        )
        conn.commit()
        return True, ("success", "Added to favorites", 201)
    except mysql.connector.IntegrityError:
        return False, ("error", "Already in favorites", 409)
    except mysql.connector.Error as e:
        conn.rollback()
        return False, ("error", str(e), 500)
    finally:
        cursor.close()


def get_favorites(user_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT f.id, f.itinerary_id, f.created_at, i.response_json
               FROM favorites f
               LEFT JOIN itineraries i ON f.itinerary_id = i.id
               WHERE f.user_id = %s
               ORDER BY f.created_at DESC""",
            (user_id,),
        )
        favorites = cursor.fetchall()
        for fav in favorites:
            fav["itinerary"] = json.loads(fav["response_json"]) if fav["response_json"] else None
            del fav["response_json"]
            if fav.get("created_at"):
                fav["created_at"] = fav["created_at"].isoformat()
        return favorites, ("success", "", 200)
    except mysql.connector.Error as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()


def remove_favorite(user_id, itinerary_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "DELETE FROM favorites WHERE user_id = %s AND itinerary_id = %s",
            (str(user_id), itinerary_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return False, ("error", "Favorite not found", 404)
        return True, ("success", "Removed from favorites", 200)
    except mysql.connector.Error as e:
        conn.rollback()
        return False, ("error", str(e), 500)
    finally:
        cursor.close()


def add_history(user_id, itinerary_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM itineraries WHERE id = %s", (itinerary_id,))
        if not cursor.fetchone():
            return False, ("error", "Itinerary not found", 404)

        cursor.execute(
            "INSERT INTO history (user_id, itinerary_id) VALUES (%s, %s)",
            (str(user_id), itinerary_id),
        )
        conn.commit()
        return True, ("success", "Added to history", 201)
    except mysql.connector.Error as e:
        conn.rollback()
        return False, ("error", str(e), 500)
    finally:
        cursor.close()


def get_history(user_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT h.id, h.itinerary_id, h.created_at, i.response_json
               FROM history h
               LEFT JOIN itineraries i ON h.itinerary_id = i.id
               WHERE h.user_id = %s
               ORDER BY h.created_at DESC""",
            (user_id,),
        )
        history = cursor.fetchall()
        for item in history:
            item["itinerary"] = json.loads(item["response_json"]) if item["response_json"] else None
            del item["response_json"]
            if item.get("created_at"):
                item["created_at"] = item["created_at"].isoformat()
        return history, ("success", "", 200)
    except mysql.connector.Error as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
