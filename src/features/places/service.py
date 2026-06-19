import math
import threading

from core.db import new_connection

_city_coords_cache = {}
_city_coords_loaded = False


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def is_coords_loaded():
    return _city_coords_loaded


def load_city_coords():
    def _do_load():
        global _city_coords_cache, _city_coords_loaded
        try:
            conn = new_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT name, lat, lon FROM cities")
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            for row in rows:
                if row["name"] and row["lat"] is not None:
                    _city_coords_cache[row["name"].strip().lower()] = (float(row["lat"]), float(row["lon"]))
            _city_coords_loaded = True
            print(f"Loaded {len(_city_coords_cache)} city coordinates from DB.")
        except Exception as e:
            print(f"[places] Failed to load city coords from DB: {e}")

    threading.Thread(target=_do_load, daemon=True).start()


def _row_to_dict(row):
    return {
        "city": row.get("city"),
        "state": row.get("state"),
        "name": row.get("name"),
        "type": row.get("type"),
        "distance from airport": row.get("dist_airport"),
        "distance from bus stand": row.get("dist_bus_stand"),
        "distance from railway station": row.get("dist_railway"),
        "rating": row.get("rating"),
        "no of rating": row.get("num_ratings"),
        "best month to visit": row.get("best_month"),
        "famous activities": row.get("famous_activities"),
        "prefer for friends": row.get("prefer_friends"),
        "prefer for couple": row.get("prefer_couple"),
        "prefer for family with children": row.get("prefer_family_children"),
        "prefer for family without children": row.get("prefer_family_no_children"),
        "famous activities with rating": row.get("famous_activities_rating"),
        "image": row.get("image"),
    }


def query_popular():
    from features.itinerary.service import recommender
    return recommender.get_popular_destination() or []


def query_by_keyword(keyword, limit=10):
    """Return city, state and place names that contain `keyword`
    (case-insensitive). Exact matches sort first; ties are broken by source
    priority (city > state > place) then alphabetically. LIKE wildcards in the
    keyword are escaped so user input is matched literally."""
    term = keyword.strip().lower()
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = "%" + escaped + "%"

    # (table, type label, source-priority rank for tie-breaking)
    sources = [("cities", "city", 0), ("states", "state", 1), ("places", "place", 2)]

    conn = new_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        results = []
        for table, type_label, rank in sources:
            cursor.execute(
                f"SELECT name FROM {table} WHERE name LIKE %s",
                (like,),
            )
            for row in cursor.fetchall():
                name = row["name"]
                results.append({
                    "name": name,
                    "type": type_label,
                    "_exact": str(name).strip().lower() == term,
                    "_rank": rank,
                })

        # Exact matches first, then source priority, then alphabetical.
        results.sort(key=lambda r: (not r["_exact"], r["_rank"], str(r["name"]).lower()))

        out = []
        for r in results[:limit]:
            out.append({"name": r["name"], "type": r["type"]})
        return out
    finally:
        cursor.close()
        conn.close()


def query_trending():
    conn = new_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT p.*, c.name AS city, s.name AS state "
            "FROM places p "
            "LEFT JOIN cities c ON p.city_id = c.id "
            "LEFT JOIN states s ON c.state_id = s.id "
            "WHERE p.num_ratings IS NOT NULL ORDER BY p.num_ratings DESC LIMIT 10"
        )
        return [_row_to_dict(r) for r in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()


def query_nearby(lat, lon):
    conn = new_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT p.*, c.name AS city, s.name AS state, c.lat AS city_lat, c.lon AS city_lon "
            "FROM places p "
            "JOIN cities c ON p.city_id = c.id "
            "LEFT JOIN states s ON c.state_id = s.id"
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    with_dist = []
    for row in rows:
        if row["city_lat"] is not None:
            dist = haversine(lat, lon, float(row["city_lat"]), float(row["city_lon"]))
            with_dist.append((dist, row))

    with_dist.sort(key=lambda x: x[0])
    return [_row_to_dict(r) for _, r in with_dist[:10]]


def query_weekend(lat, lon):
    conn = new_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT p.*, c.name AS city, s.name AS state, c.lat AS city_lat, c.lon AS city_lon "
            "FROM places p "
            "JOIN cities c ON p.city_id = c.id "
            "LEFT JOIN states s ON c.state_id = s.id"
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    candidates = []
    for row in rows:
        if row["city_lat"] is not None:
            dist = haversine(lat, lon, float(row["city_lat"]), float(row["city_lon"]))
            if dist <= 300:
                candidates.append((dist, row))

    candidates.sort(key=lambda x: float(x[1].get("rating") or 0), reverse=True)
    return [_row_to_dict(r) for _, r in candidates[:10]]
