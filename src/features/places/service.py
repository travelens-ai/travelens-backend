import math
import random
import re
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


# Scenic types get priority for the city card image — these are the places
# a traveller actually wants to see on the card (not temples/malls/hotels).
_SCENIC_TYPES = {
    'Beach', 'Nature', 'Waterfall', 'National Park', 'Hill', 'Viewpoint',
    'Wildlife Sanctuary', 'Historical Fort', 'Historical Palace', 'Lake',
    'Valley', 'Hill Station', 'Island', 'Cave', 'Hot Spring', 'Glacier',
    'Scenic Area', 'Activity Center', 'Garden', 'Dam', 'Historical Monument',
    # Rajasthan / heritage cities — forts and palaces often typed as Historical Site
    'Historical Site', 'Desert',
    # Other useful types found in CSV
    'Mountain Pass', 'Natural Wonder', 'Plateau', 'Backwaters',
    # Iconic religious sites ARE the draw for Varanasi, Amritsar, Haridwar, Madurai etc.
    'Religious Site', 'Religious',
}


_VIBE_TYPES = {
    'Beach':      {'Beach', 'Coastal', 'Island'},
    'Heritage':   {'Historical Fort', 'Historical Palace', 'Historical Site',
                   'Historical Monument', 'Historical Observatory',
                   'Astronomical Observatory', 'Archaeological Site', 'Fort'},
    'Nature':     {'Nature', 'National Park', 'Waterfall', 'Lake', 'Valley', 'Hill',
                   'Hill Station', 'Mountain Pass', 'Glacier', 'Cave', 'Hot Spring',
                   'Natural Wonder', 'Plateau', 'Backwaters', 'Scenic Area', 'Dam',
                   'Viewpoint', 'Desert', 'River', 'Garden', 'Botanical Garden'},
    'Adventure':  {'Adventure', 'Adventure Spot', 'Adventure Sports', 'Trekking',
                   'Ski Resort', 'Activity Center', 'Racing Track'},
    'Wildlife':   {'Wildlife Sanctuary', 'Wildlife', 'Nature Reserve',
                   'Bird Sanctuary', 'Zoological Park'},
    'Pilgrimage': {'Religious Site', 'Religious', 'Temple', 'Church', 'Mosque',
                   'Shrine', 'Buddhist Monastery', 'Gurudwara'},
    'Culture':    {'Museum', 'Art Museum', 'Cultural Center', 'Market', 'Theatre', 'Fair'},
}
_VIBE_LABEL = {
    'Beach':      'Beach Getaway',
    'Heritage':   'Heritage City',
    'Nature':     'Nature Escape',
    'Adventure':  'Adventure Hub',
    'Wildlife':   'Wildlife Retreat',
    'Pilgrimage': 'Pilgrimage Town',
    'Culture':    'Cultural Hub',
}
_SKIP_VIBE = {'Parking', 'Restaurant', 'Hotel', 'Shop', 'Mall',
              'Petrol Pump', 'Hospital', 'Bank', 'ATM', 'University'}
_type_to_vibe = {t: v for v, types in _VIBE_TYPES.items() for t in types}

# Hand-curated overrides: algorithm picks wrong image for these cities
# (iconic image loses on rating to a less visually representative place).
_CITY_IMAGE_OVERRIDE = {
    'puducherry':         'Paradise_Beach_Puducherry_Puducherry.webp',
    'shimla':             'Mall_Road_Shimla_Himachal_Pradesh.webp',
    'chopta':             'Chandrashila_Peak_Chopta_Uttarakhand1744839886130.webp',
    'ziro':               'Ziro_Valley_Ziro_Arunachal_Pradesh1744814868045.webp',
    'mawlynnong':         'Living_Root_Bridge_Mawlynnong_Meghalaya1744829095683.webp',
    'manali':             'Solang_Valley_Manali_Himachal_Pradesh.webp',
    'rishikesh':          'Laxman_Jhula_Rishikesh_Uttarakhand1744839587223.webp',
    'hampi':              'Stone_Chariot_Hampi_Karnataka.webp',
    'panaji':             'Baga_Beach_Panaji_Goa.webp',
    'tawang':             'Tawang_Monastery_Tawang_Arunachal_Pradesh.webp',
    'darjeeling':         'Toy_Train_Ride_Darjeeling_West_Bengal.webp',
    # major city fixes
    'chennai':            'Marina_Beach_Chennai_Tamil_Nadu.webp',
    'kolkata':            'Victoria_Memorial_Kolkata_West_Bengal.webp',
    'srinagar':           'Dal_Lake_Srinagar_Jammu_and_Kashmir.webp',
    'varanasi':           'Ghats_of_Varanasi_Varanasi_Uttar_Pradesh.webp',
    'jodhpur':            'Mehrangarh_Fort_Jodhpur_Rajasthan.webp',
    # regional city fixes
    'thrissur':           'Athirappilly_Falls_Thrissur_Kerala.webp',
    'thiruvananthapuram': 'Kovalam_Beach_Thiruvananthapuram_Kerala.webp',
    'imphal':             'Kangla_Fort_Imphal_Manipur.webp',
    'diu':                'Diu_Fort_Diu_Dadra_and_Nagar_Haveli_and_Daman_and_Diu.webp',
    'daman':              'Moti_Daman_Fort_Daman_Dadra_and_Nagar_Haveli_and_Daman_and_Diu.webp',
    'nagpur':             'Futala_Lake_Nagpur_Maharashtra.webp',
    'nashik':             'Pandavleni_Caves_Nashik_Maharashtra.webp',
    'mandi':              'Prashar_Lake_Mandi_Himachal_Pradesh.webp',
    'silvassa':           'Khanvel_Silvassa_Dadra_and_Nagar_Haveli_and_Daman_and_Diu.webp',
    'sirmaur':            'Renuka_Lake_Sirmaur_Himachal_Pradesh.webp',
    'ponda':              'Spice_Plantations_Ponda_Goa.webp',
    'kodagu':             'Nagarhole_National_Park_Kodagu_Karnataka.webp',
    'ernakulam':          'Fort_Kochi_Ernakulam_Kerala1744825586688.webp',
    'guwahati':           'Dipor_Bil_Guwahati_Assam.webp',
    'bangalore':          'Vidhana_Soudha_Bangalore_Karnataka1744824261443.webp',
    'dehradun':           'Forest_Research_Institute_Dehradun_Uttarakhand1744839332689.webp',
    'alleppey':           'Alleppey_Backwaters_Alappuzha_Kerala.webp',
}


def _attach_images_and_activities(city_rows):
    """Enrich city rows with CSV-sourced image, rating, review_count,
    famous_places list, and best_activities."""
    try:
        from features.itinerary.service import recommender
        if recommender is None:
            return city_rows
        df = recommender.places_df
    except Exception:
        return city_rows

    df_valid = df[df['city'].notna()].copy()
    has_img = df_valid[
        df_valid['image'].notna() & (df_valid['image'].astype(str).str.strip() != '')
    ]

    name_col = 'name' if 'name' in df.columns else ('placename' if 'placename' in df.columns else None)
    has_type = 'type' in df.columns
    has_rating = 'rating' in df.columns
    has_reviews = 'no of rating' in df.columns

    # --- avg_rating: mean of top-5 rated places per city (CSV, curated data) ---
    rating_map = {}
    review_map = {}
    if has_rating:
        for city_name, grp in df_valid.groupby('city'):
            top5 = grp.nlargest(5, 'rating')
            rating_map[city_name.lower()] = round(float(top5['rating'].mean()), 2)
            if has_reviews:
                review_map[city_name.lower()] = int(grp['no of rating'].sum())

    # --- Vibe: dominant place-type bucket per city ---
    vibe_map = {}
    vibe_bucket_types = {}  # city (original case) -> set of raw types in dominant vibe
    if has_type:
        for city_name, grp in df_valid[~df_valid['type'].isin(_SKIP_VIBE)].groupby('city'):
            counts = grp['type'].map(_type_to_vibe).dropna().value_counts()
            if not counts.empty:
                dominant_vibe = counts.index[0]
                vibe_map[city_name.lower()] = _VIBE_LABEL[dominant_vibe]
                vibe_bucket_types[city_name] = _VIBE_TYPES[dominant_vibe]

    # --- Image: 6-tier priority ---
    # Tier 1: _0 + scenic type  (curated landmark)
    # Tier 2: ts + scenic type  (auto-generated landmark)
    # Tier 3: _0 + city vibe bucket  (best image across all types in dominant vibe)
    # Tier 4: ts + city vibe bucket
    # Tier 5: _0 + any
    # Tier 6: ts + any
    _is_orig = lambda img: not bool(re.search(r'\d{10,}', str(img)))

    def _best_img_map(df_subset):
        if df_subset.empty:
            return {}
        return (df_subset.sort_values('rating', ascending=False)
                         .drop_duplicates('city')
                         .set_index('city')['image']
                         .to_dict())

    img_map_lower = {}
    if has_type:
        orig    = has_img[has_img['image'].apply(_is_orig)]
        ts      = has_img[~has_img['image'].apply(_is_orig)]
        orig_sc = orig[orig['type'].isin(_SCENIC_TYPES)]
        ts_sc   = ts[ts['type'].isin(_SCENIC_TYPES)]
        orig_vib = orig[orig.apply(
            lambda r: r['type'] in vibe_bucket_types.get(r['city'], set()), axis=1
        )]
        ts_vib = ts[ts.apply(
            lambda r: r['type'] in vibe_bucket_types.get(r['city'], set()), axis=1
        )]
        for tier in (_best_img_map(orig_sc), _best_img_map(ts_sc),
                     _best_img_map(orig_vib), _best_img_map(ts_vib),
                     _best_img_map(orig), _best_img_map(ts)):
            for k, v in tier.items():
                img_map_lower.setdefault(str(k).lower(), v)
    else:
        orig = has_img[has_img['image'].apply(_is_orig)]
        for k, v in _best_img_map(orig).items():
            img_map_lower[str(k).lower()] = v
        for k, v in _best_img_map(has_img).items():
            img_map_lower.setdefault(str(k).lower(), v)

    # --- famous_places: top 3 scenic-typed places per city by rating ---
    def _top3_unique(grp, col):
        seen, result = set(), []
        for name in grp.sort_values('rating', ascending=False)[col]:
            n = str(name).strip()
            if n and n not in seen:
                seen.add(n)
                result.append(n)
            if len(result) == 3:
                break
        return result

    famous_map = {}
    if name_col and has_type:
        scenic_named = df_valid[
            df_valid['type'].isin(_SCENIC_TYPES) & df_valid[name_col].notna()
        ]
        for city_name, grp in scenic_named.groupby('city'):
            famous_map[city_name.lower()] = _top3_unique(grp, name_col)
    # Fallback for cities with no scenic-typed places
    if name_col:
        for city_name, grp in df_valid[df_valid[name_col].notna()].groupby('city'):
            key = city_name.lower()
            if key not in famous_map:
                famous_map[key] = _top3_unique(grp, name_col)

    # --- best_activities: top 3 most-frequent activity tags per city ---
    act_col = 'famous activities'
    act_map = {}
    if act_col in df.columns:
        for city_name, grp in df_valid.groupby('city'):
            counter = collections.Counter()
            for val in grp[act_col].dropna():
                for act in str(val).split(','):
                    act = act.strip()
                    if act:
                        counter[act] += 1
            act_map[city_name.lower()] = ', '.join(a for a, _ in counter.most_common(3))

    img_map_lower.update(_CITY_IMAGE_OVERRIDE)

    for row in city_rows:
        key = str(row['city']).lower()
        row['image'] = img_map_lower.get(key, '')
        row['vibe'] = vibe_map.get(key, '')
        row['best_activities'] = act_map.get(key, '')
        row['famous_places'] = famous_map.get(key, [])
        # Overwrite DB-aggregated rating/reviews with cleaner CSV values
        if key in rating_map:
            row['avg_rating'] = rating_map[key]
        if key in review_map:
            row['review_count'] = review_map[key]
        # Remove internal/redundant fields
        row.pop('lat', None)
        row.pop('lon', None)
        row.pop('total_reviews', None)
        row.pop('place_count', None)

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
    # Name-based exclusion also catches spelling variants (e.g. Bengaluru vs Bangalore).
    # Many cities have duplicate/placeholder coords so we also post-filter
    # any result row whose stored coords are < 1 km from the user.
    home_key = _city_dedup_key(_nearest_city(lat, lon))
    rows = []
    for max_km in (150, 250):
        nearby = [
            name for name, (clat, clon) in _city_coords_cache.items()
            if 1 <= haversine(lat, lon, clat, clon) <= max_km
            and _city_dedup_key(name) != home_key
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
    # Name-based exclusion guards against the home city appearing via spelling variants.
    home_key = _city_dedup_key(_nearest_city(lat, lon))
    weekend = [
        name for name, (clat, clon) in _city_coords_cache.items()
        if 150 < haversine(lat, lon, clat, clon) <= 350
        and _city_dedup_key(name) != home_key
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
        states = [
            {
                "id": r[0],
                "name": r[1],
                "total_ratings": int(r[2]) if r[2] is not None else 0,
                "place_count": int(r[3]),
            }
            for r in cursor.fetchall()
        ]
        # Attach a representative image per state via the real relationship
        # (images -> place_image_map -> places -> cities -> states), picking the
        # image of the highest-rated place in that state. This avoids the
        # false positives of substring-matching image_name (e.g. "Manipur"
        # matching "Mukutmanipur").
        for state in states:
            try:
                cursor.execute(
                    "SELECT TOP 1 i.image_name "
                    "FROM images i "
                    "JOIN place_image_map pim ON pim.image_id = i.id "
                    "JOIN places p ON pim.place_id = p.id "
                    "JOIN cities c ON p.city_id = c.id "
                    "WHERE c.state_id = ? "
                    "ORDER BY p.num_ratings DESC, i.id",
                    (state["id"],),
                )
                row = cursor.fetchone()
                state["image"] = row[0] if row else ""
            except Exception as e:
                print(f"[popular_states] image lookup failed for {state['name']}: {e}")
                state["image"] = ""
            state.pop("id", None)
        return states
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
