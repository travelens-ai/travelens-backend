import time

from core.db import new_connection

_search_cache = {}
CACHE_TTL = 300  # 5 minutes


def _cursor_to_dicts(cursor):
    cols = [col[0] for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def search(q, limit=10):
    q_lower = q.strip().lower()
    limit = min(limit, 20)
    cache_key = f"{q_lower}|{limit}"

    cached = _search_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < CACHE_TTL:
        return cached[1]

    conn = new_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT TOP (?) c.name, s.name AS state "
            "FROM cities c LEFT JOIN states s ON c.state_id = s.id "
            "WHERE c.name LIKE ?",
            (limit, q_lower + "%"),
        )
        cities = [
            {"type": "city", "name": r[0].title(), "state": r[1]}
            for r in cursor.fetchall()
        ]

        remaining = limit - len(cities)
        places = []
        if remaining > 0:
            cursor.execute(
                "SELECT TOP (?) p.name, c.name AS city, s.name AS state "
                "FROM places p "
                "LEFT JOIN cities c ON p.city_id = c.id "
                "LEFT JOIN states s ON c.state_id = s.id "
                "WHERE p.name LIKE ?",
                (remaining, q_lower + "%"),
            )
            places = [
                {
                    "type": "place",
                    "name": r[0].title(),
                    "city": r[1].title() if r[1] else None,
                    "state": r[2],
                }
                for r in cursor.fetchall()
            ]
    finally:
        cursor.close()
        conn.close()

    result = cities + places
    _search_cache[cache_key] = (time.time(), result)
    return result
