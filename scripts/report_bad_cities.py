"""
Report cities that look like LLM-generated location phrases rather than real city names,
along with any places linked to them.

Usage:
    PYTHONPATH=src python scripts/report_bad_cities.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.config import *  # noqa: F401,F403 — loads .env / config before db import
from core.db import get_connection

_NOISE_WORDS = frozenset({
    'between', 'near', 'along', 'towards', 'from', 'behind', 'above',
    'below', 'inside', 'outside', 'opposite', 'beyond', 'within', 'off',
    'via', 'on', 'at', 'the', 'and', 'to', 'of', 'by', 'en',
})


def _is_bad_city(name):
    words = name.strip().split()
    if len(words) > 4:
        return True
    if any(w.lower() in _NOISE_WORDS for w in words):
        return True
    return False


def main():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, name FROM cities WHERE state_id IS NULL ORDER BY id")
    all_unlinked = cursor.fetchall()

    bad_cities = [(cid, cname) for cid, cname in all_unlinked if _is_bad_city(cname)]

    print(f"Total unlinked cities: {len(all_unlinked)}")
    print(f"Bad city rows (noise phrases): {len(bad_cities)}\n")
    print(f"{'city_id':<10} {'city_name':<50} {'place_id':<10} {'place_display_name'}")
    print("-" * 120)

    for city_id, city_name in bad_cities:
        cursor.execute(
            "SELECT id, display_name FROM places WHERE city_id = ?", (city_id,)
        )
        places = cursor.fetchall()
        if places:
            for place_id, display_name in places:
                print(f"{city_id:<10} {city_name:<50} {place_id:<10} {display_name or ''}")
        else:
            print(f"{city_id:<10} {city_name:<50} {'(no places)':<10}")

    cursor.close()
    conn.close()


if __name__ == '__main__':
    main()
