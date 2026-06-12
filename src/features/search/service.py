import time

from core.db import new_connection

_search_cache = {}
CACHE_TTL = 300  # 5 minutes


def search(q, limit=10):
    q_lower = q.strip().lower()
    limit = min(limit, 20)
    cache_key = f"{q_lower}|{limit}"

    cached = _search_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < CACHE_TTL:
        return cached[1]

    conn = new_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT c.name, s.name AS state "
            "FROM cities c LEFT JOIN states s ON c.state_id = s.id "
            "WHERE c.name LIKE %s LIMIT %s",
            (q_lower + "%", limit),
        )
        cities = [
            {"type": "city", "name": r["name"].title(), "state": r["state"]}
            for r in cursor.fetchall()
        ]

        remaining = limit - len(cities)
        places = []
        if remaining > 0:
            cursor.execute(
                "SELECT name, city, state FROM places WHERE name LIKE %s LIMIT %s",
                (q_lower + "%", remaining),
            )
            places = [
                {
                    "type": "place",
                    "name": r["name"].title(),
                    "city": r["city"].title() if r["city"] else None,
                    "state": r["state"],
                }
                for r in cursor.fetchall()
            ]
    finally:
        cursor.close()
        conn.close()

    result = cities + places
    _search_cache[cache_key] = (time.time(), result)
    return result
