"""Generic CRUD service driven by the resource registry.

Every function takes a resource CONFIG dict (from resources.py) plus arguments,
runs parameterized T-SQL against the config's real table, and returns the
codebase-standard (payload, (status, message, code)) tuple.

Safety: table/column NAMES come only from the trusted registry (never from
request input), and all VALUES are passed as pyodbc `?` parameters, so untrusted
input can't reach SQL as identifiers or literals. Writable-column whitelisting in
create/update means unknown body keys are silently ignored.
"""
import datetime
import decimal
import os

from core.db import get_connection
from core.images import _to_url

# generated_images/ lives at the repo root (this file is src/features/admin/).
_IMAGE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "generated_images"
)


# --- helpers ----------------------------------------------------------------
def _json_safe(value):
    """Coerce pyodbc/T-SQL scalar types into JSON-serializable Python values."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


def _row_to_dict(cursor, row, hidden):
    cols = [c[0] for c in cursor.description]
    return {c: _json_safe(v) for c, v in zip(cols, row) if c not in hidden}


def _clean_writable(config, data):
    """Keep only the config's writable keys that are present in the body."""
    writable = set(config["writable"])
    return {k: v for k, v in (data or {}).items() if k in writable}


# --- read -------------------------------------------------------------------
def list_rows(config, *, page, limit, search):
    """Paged list with optional case-insensitive search across config['search'].

    Returns ({data, total, page, limit}, (status, msg, code)). Uses T-SQL
    OFFSET/FETCH which requires an ORDER BY (config['order_by'])."""
    table = config["table"]
    hidden = config["hidden"]
    offset = (page - 1) * limit

    where, params = "", []
    if search and config["search"]:
        clauses = [f"CAST({col} AS NVARCHAR(MAX)) LIKE ?" for col in config["search"]]
        where = " WHERE " + " OR ".join(clauses)
        like = f"%{search}%"
        params = [like] * len(config["search"])

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT COUNT(*) FROM {table}{where}", params)
        total = int(cursor.fetchone()[0])

        cursor.execute(
            f"SELECT * FROM {table}{where} "
            f"ORDER BY {config['order_by']} "
            f"OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
            params + [offset, limit],
        )
        rows = cursor.fetchall()
        data = [_row_to_dict(cursor, r, hidden) for r in rows]
        return {"data": data, "total": total, "page": page, "limit": limit}, ("success", "OK", 200)
    except Exception as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def list_feedback_rows(config, *, page, limit, search):
    """Feedback list joined with the author (users) and itinerary it references.

    feedback.user_id is NVARCHAR while users.id is INT, so the join casts
    users.id to match. Both joins are LEFT so feedback with no logged-in user or
    no itinerary still appears. Related rows are nested under `user` and
    `itinerary` (null when absent); the feedback columns stay top-level."""
    offset = (page - 1) * limit

    where, params = "", []
    if search and config["search"]:
        clauses = [f"CAST(f.{col} AS NVARCHAR(MAX)) LIKE ?" for col in config["search"]]
        where = " WHERE " + " OR ".join(clauses)
        params = [f"%{search}%"] * len(config["search"])

    # Explicit column lists per joined table so we can label them and rebuild
    # nested dicts by prefix (SELECT * would collide on `id`, `name`, etc.).
    user_cols = ["id", "name", "email", "phone"]
    itin_cols = ["id", "status", "request_json", "response_json", "created_at"]
    user_select = ", ".join(f"u.{c} AS user__{c}" for c in user_cols)
    itin_select = ", ".join(f"i.{c} AS itin__{c}" for c in itin_cols)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT COUNT(*) FROM feedback f{where}", params)
        total = int(cursor.fetchone()[0])

        cursor.execute(
            f"SELECT f.*, {user_select}, {itin_select} "
            f"FROM feedback f "
            f"LEFT JOIN users u ON TRY_CAST(f.user_id AS INT) = u.id "
            f"LEFT JOIN itineraries i ON f.itinerary_id = i.id"
            f"{where} "
            f"ORDER BY f.{config['order_by']} "
            f"OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
            params + [offset, limit],
        )
        cols = [c[0] for c in cursor.description]
        data = [_split_feedback_row(cols, r) for r in cursor.fetchall()]
        return {"data": data, "total": total, "page": page, "limit": limit}, ("success", "OK", 200)
    except Exception as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def _split_feedback_row(cols, row):
    """Turn a joined feedback row into {<feedback cols>, user:{...}|None,
    itinerary:{...}|None} using the user__/itin__ alias prefixes."""
    base, user, itin = {}, {}, {}
    for col, val in zip(cols, row):
        v = _json_safe(val)
        if col.startswith("user__"):
            user[col[len("user__"):]] = v
        elif col.startswith("itin__"):
            itin[col[len("itin__"):]] = v
        else:
            base[col] = v
    # A LEFT JOIN miss leaves every joined column NULL → treat as no related row.
    base["user"] = user if any(v is not None for v in user.values()) else None
    base["itinerary"] = itin if any(v is not None for v in itin.values()) else None
    return base


# --- place images (custom, not generic CRUD) --------------------------------
def list_place_images(*, page, limit, search, only_unmoderated=True):
    """Paged list of place images, joined to the place each belongs to.

    Sourced from place_image_map → images (+ places). Each row carries the
    image (id, name, full URL, source, moderated) and its place (id, name) so the
    panel can show which place an image is attached to. `search` matches the
    image name or place name. `image_id` is the id passed to the delete/moderate
    endpoints.

    only_unmoderated=True (default) returns just the review queue
    (images.moderated = 0); False returns every image regardless of moderation.
    """
    offset = (page - 1) * limit

    # Only-unreviewed filter (the review queue) unless the caller wants all.
    clauses, params = [], []
    if only_unmoderated:
        clauses.append("ISNULL(i.moderated, 0) = 0")
    if search:
        clauses.append("(i.image_name LIKE ? OR p.name LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM place_image_map pim "
            "JOIN images i ON pim.image_id = i.id "
            "LEFT JOIN places p ON pim.place_id = p.id" + where,
            params,
        )
        total = int(cursor.fetchone()[0])

        cursor.execute(
            "SELECT i.id AS image_id, i.image_name, i.source, i.created_at, i.moderated, "
            "pim.place_id, p.name AS place_name "
            "FROM place_image_map pim "
            "JOIN images i ON pim.image_id = i.id "
            "LEFT JOIN places p ON pim.place_id = p.id"
            + where
            + " ORDER BY i.id DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
            params + [offset, limit],
        )
        data = []
        for image_id, image_name, source, created_at, moderated, place_id, place_name in cursor.fetchall():
            data.append({
                "image_id": image_id,
                "image_name": image_name,
                "image_url": _to_url(image_name),
                "source": source,
                "created_at": _json_safe(created_at),
                "moderated": bool(moderated),
                "place": {"id": place_id, "name": place_name} if place_id is not None else None,
            })
        return {"data": data, "total": total, "page": page, "limit": limit}, ("success", "OK", 200)
    except Exception as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


def delete_place_image(image_id):
    """Delete one image everywhere: place_image_map links, the images row, and
    the file on disk. Mirrors scripts/delete_image.py. Returns (ok, (status,
    msg, code)); 404 if the image id doesn't exist."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT image_name FROM images WHERE id = ?", (image_id,))
        row = cursor.fetchone()
        if row is None:
            return False, ("error", "Not found", 404)
        image_name = row[0]

        cursor.execute("DELETE FROM place_image_map WHERE image_id = ?", (image_id,))
        cursor.execute("DELETE FROM images WHERE id = ?", (image_id,))
        conn.commit()

        # Best-effort file removal — the DB row is the source of truth; a missing
        # file (already gone, or served only from the CDN) is not an error.
        file_removed = False
        if image_name:
            file_path = os.path.join(_IMAGE_DIR, image_name)
            if os.path.exists(file_path):
                os.remove(file_path)
                file_removed = True
        return (
            {"image_id": image_id, "image_name": image_name, "file_removed": file_removed},
            ("success", "Deleted", 200),
        )
    except Exception as e:
        conn.rollback()
        return False, ("error", str(e), 400)
    finally:
        cursor.close()
        conn.close()


def set_images_moderated(image_ids, moderated):
    """Mark one or more images moderated (True) or back to unreviewed (False).

    Moderated images drop out of the GET /admin/place-images review queue.
    Unknown ids are reported in `not_found` rather than failing the batch.
    Returns ({updated:[ids], not_found:[ids], moderated:bool, count}, ...).
    """
    ids = []
    for v in image_ids or []:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    ids = list(dict.fromkeys(ids))
    if not ids:
        return None, ("error", "image_ids must be a non-empty list of integers", 400)

    flag = 1 if moderated else 0
    conn = get_connection()
    cursor = conn.cursor()
    try:
        placeholders = ", ".join(["?"] * len(ids))
        cursor.execute(f"SELECT id FROM images WHERE id IN ({placeholders})", ids)
        found = [int(r[0]) for r in cursor.fetchall()]
        not_found = [i for i in ids if i not in found]

        if found:
            ph = ", ".join(["?"] * len(found))
            cursor.execute(
                f"UPDATE images SET moderated = ? WHERE id IN ({ph})", [flag] + found
            )
            conn.commit()
        return (
            {"updated": found, "not_found": not_found, "moderated": bool(moderated), "count": len(found)},
            ("success", "Updated", 200),
        )
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 400)
    finally:
        cursor.close()
        conn.close()


def bulk_delete_place_images(image_ids):
    """Delete many images in one call (DB rows + links + files). Returns
    ({deleted:[...], not_found:[...], count}, ...). Unknown ids are reported in
    `not_found` rather than failing the whole batch; the DELETEs run in one
    transaction. Mirrors delete_place_image per id."""
    ids = []
    for v in image_ids or []:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    ids = list(dict.fromkeys(ids))  # de-dupe, preserve order
    if not ids:
        return None, ("error", "image_ids must be a non-empty list of integers", 400)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        placeholders = ", ".join(["?"] * len(ids))
        cursor.execute(
            f"SELECT id, image_name FROM images WHERE id IN ({placeholders})", ids
        )
        found = {int(r[0]): r[1] for r in cursor.fetchall()}
        not_found = [i for i in ids if i not in found]

        if found:
            found_ids = list(found.keys())
            ph = ", ".join(["?"] * len(found_ids))
            cursor.execute(f"DELETE FROM place_image_map WHERE image_id IN ({ph})", found_ids)
            cursor.execute(f"DELETE FROM images WHERE id IN ({ph})", found_ids)
            conn.commit()

        deleted = []
        for image_id, image_name in found.items():
            file_removed = False
            if image_name:
                file_path = os.path.join(_IMAGE_DIR, image_name)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    file_removed = True
            deleted.append({
                "image_id": image_id,
                "image_name": image_name,
                "file_removed": file_removed,
            })
        return (
            {"deleted": deleted, "not_found": not_found, "count": len(deleted)},
            ("success", "Deleted", 200),
        )
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 400)
    finally:
        cursor.close()
        conn.close()


def get_row(config, row_id):
    table, pk, hidden = config["table"], config["pk"], config["hidden"]
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (row_id,))
        row = cursor.fetchone()
        if row is None:
            return None, ("error", "Not found", 404)
        return _row_to_dict(cursor, row, hidden), ("success", "OK", 200)
    except Exception as e:
        return None, ("error", str(e), 500)
    finally:
        cursor.close()
        conn.close()


# --- write ------------------------------------------------------------------
def create_row(config, data):
    fields = _clean_writable(config, data)
    if not fields:
        return None, ("error", "No writable fields provided", 400)

    table, pk, hidden = config["table"], config["pk"], config["hidden"]
    cols = list(fields.keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_list = ", ".join(cols)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"INSERT INTO {table} ({col_list}) OUTPUT INSERTED.{pk} "
            f"VALUES ({placeholders})",
            list(fields.values()),
        )
        new_id = cursor.fetchone()[0]
        conn.commit()
        # Re-fetch so the response includes DB-defaulted columns (created_at, ...).
        cursor.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (new_id,))
        row = cursor.fetchone()
        return _row_to_dict(cursor, row, hidden), ("success", "Created", 201)
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 400)
    finally:
        cursor.close()
        conn.close()


def update_row(config, row_id, data):
    fields = _clean_writable(config, data)
    if not fields:
        return None, ("error", "No writable fields provided", 400)

    table, pk, hidden = config["table"], config["pk"], config["hidden"]
    set_clause = ", ".join(f"{c} = ?" for c in fields)
    values = list(fields.values()) + [row_id]

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT 1 FROM {table} WHERE {pk} = ?", (row_id,))
        if cursor.fetchone() is None:
            return None, ("error", "Not found", 404)

        cursor.execute(f"UPDATE {table} SET {set_clause} WHERE {pk} = ?", values)
        conn.commit()
        cursor.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (row_id,))
        row = cursor.fetchone()
        return _row_to_dict(cursor, row, hidden), ("success", "Updated", 200)
    except Exception as e:
        conn.rollback()
        return None, ("error", str(e), 400)
    finally:
        cursor.close()
        conn.close()


def delete_row(config, row_id):
    table, pk = config["table"], config["pk"]
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"DELETE FROM {table} WHERE {pk} = ?", (row_id,))
        conn.commit()
        if cursor.rowcount == 0:
            return False, ("error", "Not found", 404)
        return True, ("success", "Deleted", 204)
    except Exception as e:
        conn.rollback()
        return False, ("error", str(e), 400)
    finally:
        cursor.close()
        conn.close()
