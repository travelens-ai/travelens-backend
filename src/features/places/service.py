import math
import random
import threading
import collections

from core.db import new_connection

_city_coords_cache = {}
_city_coords_loaded = False

# Curated Gen Z popular destinations — aspirational, diverse, all have good data in DB.
# Randomly sampled each request so the list feels alive without needing real trending data.
_POPULAR_CITIES = [
    ('panaji',       'Goa'),
    ('jaipur',       'Rajasthan'),
    ('manali',       'Himachal Pradesh'),
    ('rishikesh',    'Uttarakhand'),
    ('udaipur',      'Rajasthan'),
    ('hampi',        'Karnataka'),
    ('darjeeling',   'West Bengal'),
    ('puducherry',   'Puducherry'),
    ('coorg',        'Karnataka'),
    ('jaisalmer',    'Rajasthan'),
    ('varanasi',     'Uttar Pradesh'),
    ('leh',          'Ladakh'),
    ('amritsar',     'Punjab'),
    ('shimla',       'Himachal Pradesh'),
    ('gokarna',      'Karnataka'),
    ('munnar',       'Kerala'),
    ('jodhpur',      'Rajasthan'),
    ('pushkar',      'Rajasthan'),
    ('dharamshala',  'Himachal Pradesh'),
    ('gangtok',      'Sikkim'),
    ('alappuzha',    'Kerala'),
    ('ooty',         'Tamil Nadu'),
    ('mahabaleshwar','Maharashtra'),
    ('kasol',        'Himachal Pradesh'),
    ('ziro',         'Arunachal Pradesh'),
    ('mawlynnong',   'Meghalaya'),
    ('tawang',       'Arunachal Pradesh'),
    ('khajuraho',    'Madhya Pradesh'),
    ('agra',         'Uttar Pradesh'),
    ('haridwar',     'Uttarakhand'),
]

# Trending = buzzing right now — mix of viral + classic, slightly different pool.
_TRENDING_CITIES = [
    ('kasol',        'Himachal Pradesh'),
    ('rishikesh',    'Uttarakhand'),
    ('panaji',       'Goa'),
    ('mcleod ganj',  'Himachal Pradesh'),
    ('hampi',        'Karnataka'),
    ('ziro',         'Arunachal Pradesh'),
    ('gokarna',      'Karnataka'),
    ('mawlynnong',   'Meghalaya'),
    ('varkala',      'Kerala'),
    ('tawang',       'Arunachal Pradesh'),
    ('jaipur',       'Rajasthan'),
    ('udaipur',      'Rajasthan'),
    ('jaisalmer',    'Rajasthan'),
    ('leh',          'Ladakh'),
    ('chopta',       'Uttarakhand'),
    ('darjeeling',   'West Bengal'),
    ('coorg',        'Karnataka'),
    ('puducherry',   'Puducherry'),
    ('alibaug',      'Maharashtra'),
    ('auli',         'Uttarakhand'),
    ('munnar',       'Kerala'),
    ('alappuzha',    'Kerala'),
    ('khajuraho',    'Madhya Pradesh'),
    ('nainital',     'Uttarakhand'),
    ('varanasi',     'Uttar Pradesh'),
]

# Primary types that represent actual tourist destinations (not shops, temples, localities).
TOURIST_TYPES = (
    'tourist_attraction', 'historical_landmark', 'historical_place', 'national_park',
    'beach', 'lake', 'park', 'museum', 'mountain_peak', 'hiking_area', 'scenic_spot',
    'natural_feature', 'garden', 'zoo', 'amusement_park', 'wildlife_park', 'island',
    'monument', 'castle', 'landmark', 'botanical_garden', 'nature_preserve',
    'wildlife_refuge', 'aquarium', 'cultural_center', 'art_museum', 'history_museum',
    'waterfall', 'resort_hotel',
)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _cursor_to_dicts(cursor):
    cols = [col[0] for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def is_coords_loaded():
    return _city_coords_loaded


def load_city_coords():
    def _do_load():
        global _city_coords_cache, _city_coords_loaded
        try:
            conn = new_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT name, lat, lon FROM cities")
            rows = _cursor_to_dicts(cursor)
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


def _city_aggregate_query(city_filter=None, limit=50):
    """Aggregate tourist places by city and return city-level cards.

    city_filter: optional list of lowercase city names to restrict the query
    (used by nearby/weekend after haversine pre-filtering).
    Returns list of dicts: city, state, lat, lon, place_count, avg_rating,
    total_reviews, best_month. image/best_activities are filled in later by
    _attach_images_and_activities().
    """
    placeholders = ','.join(['?' for _ in TOURIST_TYPES])
    params = list(TOURIST_TYPES)

    city_clause = ''
    if city_filter:
        city_placeholders = ','.join(['?' for _ in city_filter])
        city_clause = f'AND LOWER(c.name) IN ({city_placeholders})'
        params += city_filter  # already lowercased by caller

    params.append(limit)

    sql = f"""
        SELECT c.name AS city, s.name AS state,
               CAST(c.lat AS float) AS lat, CAST(c.lon AS float) AS lon,
               COUNT(p.id) AS place_count,
               AVG(CAST(p.rating AS float)) AS avg_rating,
               SUM(CAST(COALESCE(p.google_rating_count, 0) AS bigint)) AS total_reviews,
               MAX(p.best_month) AS best_month
        FROM places p
        JOIN cities c ON p.city_id = c.id
        JOIN states s ON c.state_id = s.id
        WHERE (p.primary_type IN ({placeholders}) OR p.primary_type IS NULL)
          AND p.rating IS NOT NULL
          {city_clause}
        GROUP BY c.name, s.name, c.lat, c.lon
        HAVING COUNT(p.id) >= 2
        ORDER BY total_reviews DESC, avg_rating DESC
        OFFSET 0 ROWS FETCH NEXT (?) ROWS ONLY
    """

    conn = new_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        rows = _cursor_to_dicts(cursor)
    finally:
        cursor.close()
        conn.close()

    return [
        {
            'city': r['city'],
            'state': r['state'],
            'lat': r['lat'],
            'lon': r['lon'],
            'place_count': int(r['place_count']),
            'avg_rating': round(float(r['avg_rating']), 2) if r['avg_rating'] else None,
            'total_reviews': int(r['total_reviews']),
            'best_month': r['best_month'] or '',
            'image': '',
            'best_activities': '',
        }
        for r in rows
    ]


def _attach_images_and_activities(city_rows):
    """Enrich city rows with image and best_activities from the in-memory
    places DataFrame (CSV-sourced, loaded by the recommender at startup)."""
    try:
        from features.itinerary.service import recommender
        if recommender is None:
            return city_rows
        df = recommender.places_df
    except Exception:
        return city_rows

    # Best image per city — top-rated place that has a non-empty image.
    has_img = df[
        df['image'].notna() & (df['image'].astype(str).str.strip() != '') &
        df['city'].notna()
    ]
    img_map = (
        has_img.sort_values('rating', ascending=False)
               .drop_duplicates('city')
               .set_index('city')['image']
               .to_dict()
    )
    img_map_lower = {str(k).lower(): v for k, v in img_map.items()}

    # Top 3 activity tags per city (most frequent across all its places).
    act_col = 'famous activities'
    act_map = {}
    if act_col in df.columns:
        for city_name, group in df[df['city'].notna()].groupby('city'):
            counter = collections.Counter()
            for val in group[act_col].dropna():
                for act in str(val).split(','):
                    act = act.strip()
                    if act:
                        counter[act] += 1
            act_map[city_name.lower()] = ', '.join(a for a, _ in counter.most_common(3))

    for row in city_rows:
        key = str(row['city']).lower()
        row['image'] = img_map_lower.get(key, '')
        row['best_activities'] = act_map.get(key, '')

    return city_rows


_CITY_PREFIXES = ('new ', 'old ', 'north ', 'south ', 'east ', 'west ', 'greater ')

# Known alternate spellings that should resolve to the same city.
_CITY_ALIASES = {
    'bengaluru': 'bangalore',
    'bombay':    'mumbai',
    'calcutta':  'kolkata',
    'madras':    'chennai',
    'mysuru':    'mysore',
    'thiruvananthapuram': 'trivandrum',
    'kozhikode': 'calicut',
    'thrissur':  'trichur',
    'ernakulam': 'kochi',
}

def _city_dedup_key(name):
    """Normalise city name for dedup — strip direction/qualifier prefixes and
    resolve known alternate spellings so duplicates collapse to one key."""
    key = str(name).lower().strip()
    for prefix in _CITY_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    key = key.replace(' ', '')
    return _CITY_ALIASES.get(key, key)


def _dedup_cities(rows):
    """Remove duplicate cities (e.g. 'delhi' vs 'new delhi') by normalised key.
    Keeps the entry with the higher place_count on collision."""
    seen = {}
    for row in rows:
        key = _city_dedup_key(row['city'])
        if key not in seen or (row.get('place_count') or 0) > (seen[key].get('place_count') or 0):
            seen[key] = row
    return list(seen.values())


def _nearest_city(lat, lon):
    """Return the dedup-key of the closest city in the coords cache."""
    if not _city_coords_cache:
        return None
    return min(
        _city_coords_cache,
        key=lambda name: haversine(lat, lon, *_city_coords_cache[name]),
    )


def _curated_city_cards(city_state_list, n=10, exclude_city_key=None):
    """Build city cards from a curated (city, state) list by looking up the
    city aggregate from the DB. Returns n randomly sampled entries.
    exclude_city_key: dedup key of user's current city — excluded from results."""
    pool = city_state_list
    if exclude_city_key:
        pool = [(c, s) for c, s in city_state_list
                if _city_dedup_key(c) != exclude_city_key]
    sample = random.sample(pool, min(n * 2, len(pool)))
    city_names = [c.lower() for c, _ in sample]
    rows = _city_aggregate_query(city_filter=city_names, limit=len(city_names) + 5)
    rows = _attach_images_and_activities(rows)
    # Preserve curated order (random.sample order) rather than review-count order.
    order = {c.lower(): i for i, (c, _) in enumerate(sample)}
    rows.sort(key=lambda r: order.get(str(r['city']).lower(), 999))
    return rows[:n]


def _state_diverse(rows, max_per_state=2, total=10):
    """Cap results to `max_per_state` cities per state for diversity."""
    state_counts = collections.Counter()
    result = []
    for row in rows:
        state = row.get('state', '')
        if state_counts[state] < max_per_state:
            result.append(row)
            state_counts[state] += 1
        if len(result) >= total:
            break
    return result


def query_popular(lat=None, lon=None):
    excl = _city_dedup_key(_nearest_city(lat, lon)) if lat is not None and lon is not None else None
    return _curated_city_cards(_POPULAR_CITIES, n=10, exclude_city_key=excl)


def query_trending(lat=None, lon=None):
    excl = _city_dedup_key(_nearest_city(lat, lon)) if lat is not None and lon is not None else None
    return _curated_city_cards(_TRENDING_CITIES, n=10, exclude_city_key=excl)


def query_nearby(lat, lon):
    # Start at 150 km; expand to 250 km if fewer than 5 results (sparse regions).
    # Lower bound 1 km excludes the user's own city (exact match is 0 km).
    # Many cities have duplicate/placeholder coords so we also post-filter
    # any result row whose stored coords are < 1 km from the user.
    rows = []
    for max_km in (150, 250):
        nearby = [
            name for name, (clat, clon) in _city_coords_cache.items()
            if 1 <= haversine(lat, lon, clat, clon) <= max_km
        ]
        if not nearby:
            continue
        rows = _city_aggregate_query(city_filter=nearby, limit=50)
        coords = _city_coords_cache
        rows.sort(key=lambda r: (
            haversine(lat, lon, *coords.get(r['city'].lower(), (lat, lon))),
            -(r['avg_rating'] or 0),
        ))
        rows = _dedup_cities(rows)
        rows = [r for r in rows if r.get('lat') is None or
                haversine(lat, lon, r['lat'], r['lon']) >= 1]
        if len(rows) >= 5 or max_km == 250:
            break
    return _attach_images_and_activities(rows[:10])


def query_weekend(lat, lon):
    # 150–350 km: far enough to feel like a trip, close enough for a weekend.
    weekend = [
        name for name, (clat, clon) in _city_coords_cache.items()
        if 150 < haversine(lat, lon, clat, clon) <= 350
    ]
    if not weekend:
        return []
    rows = _city_aggregate_query(city_filter=weekend, limit=50)
    rows.sort(key=lambda r: -(r['avg_rating'] or 0))
    rows = _dedup_cities(rows)
    return _attach_images_and_activities(rows[:10])


def query_popular_states(limit=10):
    """Return the most popular states ranked by total review count."""
    conn = new_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT s.id, s.name, "
            "COALESCE(SUM(p.num_ratings), 0) AS total_ratings, "
            "COUNT(p.id) AS place_count "
            "FROM states s "
            "JOIN cities c ON c.state_id = s.id "
            "JOIN places p ON p.city_id = c.id "
            "GROUP BY s.id, s.name "
            "ORDER BY total_ratings DESC, place_count DESC "
            "OFFSET 0 ROWS FETCH NEXT (?) ROWS ONLY",
            (limit,),
        )
        return [
            {
                "name": r[1],
                "total_ratings": int(r[2]) if r[2] is not None else 0,
                "place_count": int(r[3]),
            }
            for r in cursor.fetchall()
        ]
    finally:
        cursor.close()
        conn.close()


def query_by_keyword(keyword, limit=10):
    """Return city, state and place names containing `keyword` (case-insensitive).
    Exact matches sort first; ties broken by source priority then alphabetically."""
    term = keyword.strip().lower()
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = "%" + escaped + "%"

    sources = [("cities", "city", 0), ("states", "state", 1), ("places", "place", 2)]

    conn = new_connection()
    cursor = conn.cursor()
    try:
        results = []
        for table, type_label, rank in sources:
            cursor.execute(
                f"SELECT name FROM {table} WHERE name LIKE ?",
                (like,),
            )
            for row in cursor.fetchall():
                name = row[0]
                results.append({
                    "name": name,
                    "type": type_label,
                    "_exact": str(name).strip().lower() == term,
                    "_rank": rank,
                })

        results.sort(key=lambda r: (not r["_exact"], r["_rank"], str(r["name"]).lower()))
        return [{"name": r["name"], "type": r["type"]} for r in results[:limit]]
    finally:
        cursor.close()
        conn.close()
