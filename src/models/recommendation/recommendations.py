import pandas as pd
from models.recommendation import scoring as _sc
from models.recommendation import image_helpers as _img
from prompts.constants import (
    BUDGET_TIER_MAP, HOTEL_TIER_STARS, MEAL_COST_CAPS,
    PLACE_COLS_PROMPT, HOTEL_COLS_PROMPT, REST_COLS_PROMPT,
)
from numpy.linalg import norm


def get_place_recommendations(system, user_preferences):
    user_embedding = _sc.generate_user_embedding(system, user_preferences)
    user_embedding = user_embedding / norm(user_embedding)

    top_places = system.places_df.copy()
    top_places['image'] = top_places['image'].apply(
        lambda x: x if isinstance(x, str) and x.endswith('.webp') else None
    )
    top_places['activity_score'] = top_places['famous activities with rating'].apply(
        lambda x: _sc.compute_activity_score(system, x, user_embedding)
    )
    top_places['trip_type_score'] = top_places['type'].apply(
        lambda x: _sc.compute_trip_type_score(system, x, user_embedding)
    )

    poi = user_preferences["places_of_interest"]
    preferred_location = (", ".join(poi) if isinstance(poi, list) else str(poi)).lower()
    location_parts = [part.strip() for part in preferred_location.split(",")]

    city_matches = top_places[
        top_places['city'].fillna('').astype(str).str.lower().apply(
            lambda city: any(part == city for part in location_parts)
        )
    ]
    if not city_matches.empty:
        top_places = city_matches.copy()
    else:
        top_places = top_places[
            top_places['state'].fillna('').astype(str).str.lower().apply(
                lambda state: any(part == state for part in location_parts))
        ].copy()

    C = top_places['rating'].mean()
    top_places['rating_score'] = top_places.apply(
        lambda x: _sc.weighted_place_rating(system, x, C), axis=1
    )
    for column in ['activity_score', 'trip_type_score', 'rating_score']:
        min_val = top_places[column].min()
        max_val = top_places[column].max()
        rng = max_val - min_val
        top_places[column] = (top_places[column] - min_val) / rng if rng > 0 else 1.0

    top_places['final_score'] = (
        0.5 * top_places['activity_score'] +
        0.3 * top_places['trip_type_score'] +
        0.2 * top_places['rating_score']
    )
    return top_places.sort_values('final_score', ascending=False).head(100)


def enrich_place_images(system, places_df):
    if places_df.empty:
        return places_df
    for idx, row in places_df.iterrows():
        if not row.get('image'):
            url = system.image_client.get_place_image(
                row.get('name', '') or row.get('placename', ''),
                row.get('city', '')
            )
            if url:
                places_df.at[idx, 'image'] = url
    return places_df


def get_hotel_recommendations(system, user_preferences):
    poi = user_preferences["places_of_interest"]
    city = ", ".join(poi) if isinstance(poi, list) else str(poi)
    city_part = city.split(',')[0].strip().lower()

    # 1. Azure SQL / CSV (in-memory, loaded at startup)
    top_hotels = system.hotels_df[
        system.hotels_df['city'].fillna('').astype(str).str.lower().str.contains(city_part, na=False) |
        system.hotels_df['address'].fillna('').astype(str).str.lower().str.contains(city_part, na=False)
    ].sort_values('site_review_rating', ascending=False)

    raw = str(user_preferences.get('hotel_preference') or user_preferences.get('budget') or '').strip().lower()
    pref = BUDGET_TIER_MAP.get(raw, raw)
    if pref in HOTEL_TIER_STARS:
        lo, hi = HOTEL_TIER_STARS[pref]
        in_tier = top_hotels[top_hotels['hotel_star_rating'].between(lo, hi, inclusive='both')]
        if len(in_tier) < 3:
            in_tier = top_hotels[
                top_hotels['hotel_star_rating'].between(max(0, lo - 1), min(5, hi + 1), inclusive='both')
            ]
        top_hotels = in_tier.reset_index(drop=True)

    if not top_hotels.empty:
        return top_hotels.head(100)

    # 2. SQLite cache (previously fetched Google Places results)
    cached = system.places_client.get_cached_hotels(city)
    if cached:
        return pd.DataFrame(cached).head(100)

    # 3. Live Google Places API
    live_hotels = system.places_client.search_hotels(city)
    if live_hotels:
        return pd.DataFrame(live_hotels).head(100)

    return top_hotels.head(100)


def get_restaurant_recommendations(system, user_preferences):
    poi = user_preferences["places_of_interest"]
    city = ", ".join(poi) if isinstance(poi, list) else str(poi)
    cuisine_raw = user_preferences["food_preferences"]
    cuisine = ", ".join(cuisine_raw) if isinstance(cuisine_raw, list) else str(cuisine_raw)

    raw = str(user_preferences.get('hotel_preference') or user_preferences.get('budget') or '').strip().lower()
    pref = BUDGET_TIER_MAP.get(raw, raw)
    caps = MEAL_COST_CAPS.get(pref)

    def _annotate_and_count(df):
        df = df.copy()
        if caps and 'Cost' in df.columns:
            b_max, l_max, d_max = caps
            b2, l2, d2 = b_max * 2, l_max * 2, d_max * 2
            cost_col = pd.to_numeric(df['Cost'], errors='coerce')
            unknown = cost_col.isna() | (cost_col == 0)
            df = df[unknown | (cost_col <= d2)].copy()
            cost_col = pd.to_numeric(df['Cost'], errors='coerce')

            def _slots(c):
                if pd.isna(c) or c == 0: return 'breakfast,lunch,dinner'
                if c <= b2:              return 'breakfast,lunch,dinner'
                if c <= l2:             return 'lunch,dinner'
                return 'dinner'
            df['suitable_slots'] = cost_col.apply(_slots)
        else:
            df['suitable_slots'] = 'breakfast,lunch,dinner'

        slots_str = df['suitable_slots'].astype(str)
        slot_counts = {
            'breakfast': int(slots_str.str.contains('breakfast').sum()),
            'lunch':     int(slots_str.str.contains('lunch').sum()),
            'dinner':    int(slots_str.str.contains('dinner').sum()),
        }
        return df, slot_counts

    # 1. Azure SQL / CSV (in-memory, loaded at startup)
    preferred_cuisines = [_sc.normalize(system, c) for c in cuisine.split(',')]
    city_part = _sc.normalize(system, city.split(',')[0])
    cuisine_pattern = '|'.join(preferred_cuisines)

    top_restaurants = system.restaurants_df[
        (system.restaurants_df['City'].apply(lambda x: _sc.normalize(system, x)).str.contains(city_part, na=False) |
         system.restaurants_df['Locality'].apply(lambda x: _sc.normalize(system, x)).str.contains(city_part, na=False)) &
        system.restaurants_df['Cuisine'].apply(lambda x: _sc.normalize(system, x)).str.contains(cuisine_pattern, na=False)
    ].sort_values('Rating', ascending=False)
    top_restaurants = top_restaurants.drop_duplicates(subset=['Name'], keep='first')

    C = top_restaurants['Rating'].mean()
    top_restaurants = top_restaurants.copy()
    top_restaurants['rating_score'] = top_restaurants.apply(
        lambda x: _sc.weighted_restaurants_rating(system, x, C), axis=1
    )
    top_restaurants = top_restaurants.sort_values('rating_score', ascending=False)

    if not top_restaurants.empty:
        return _annotate_and_count(top_restaurants.head(100))

    # 2. SQLite cache (previously fetched Google Places results)
    cached = system.places_client.get_cached_restaurants(city, cuisine)
    if cached:
        return _annotate_and_count(pd.DataFrame(cached).head(100))

    # 3. Live Google Places API
    live_restaurants = system.places_client.search_restaurants(city, cuisine)
    if live_restaurants:
        return _annotate_and_count(pd.DataFrame(live_restaurants).head(100))

    return _annotate_and_count(top_restaurants.head(100))


def get_available_places(system, itinerary, user_preferences, count, scored_df=None):
    used_names = set()
    for day in itinerary.get('itinerary', []):
        for item in day.get('timeline', []):
            if item.get('type') == 'place':
                n = item.get('name', '').strip()
                if n:
                    used_names.add(n.lower())

    if scored_df is None:
        scored_df = get_place_recommendations(system, user_preferences)
    remaining_df = scored_df[
        ~scored_df['effective_name'].str.strip().str.lower().isin(used_names)
    ].head(count)

    if remaining_df.empty:
        return []

    COLS = [
        'effective_name', 'placename', 'city', 'state',
        'primary_type_name', 'place_types', 'famous activities',
        'best month to visit', 'rating', 'google_rating', 'google_rating_count',
        'lat', 'lon', 'short_formatted_address', 'editorial_summary',
        'review_summary', 'opening_hours', 'website_uri', 'google_maps_uri',
    ]
    avail_cols = [c for c in COLS if c in remaining_df.columns]
    places_list = remaining_df[avail_cols].fillna('').to_dict(orient='records')

    for ap in places_list:
        ap['name'] = ap.pop('effective_name', ap.get('placename', ''))
        ap.setdefault('reason', '')
        ap.setdefault('activities', [])
        ap.setdefault('duration', '')
        ap.setdefault('suggested_start_time', '')
        ap.setdefault('travel_from_prev', None)
        ap.setdefault('images', [])

    image_map = _img.get_images_for_places(system, [ap['name'] for ap in places_list])
    for ap in places_list:
        ap['images'] = image_map.get(ap['name'].strip().lower(), [])[:5]
        if not ap['images']:
            ap['images'] = _img.search_images_by_keywords(
                system, [ap.get('name'), ap.get('city'), ap.get('state')], limit=5
            )

    from models.recommendation import finalization as _fin
    _wrapper = {
        'city': itinerary.get('city', ''),
        'state': itinerary.get('state', ''),
        'itinerary': [{'timeline': [dict(p, type='place') for p in places_list]}],
        'hotels': [],
    }
    _fin.attach_lat_long(system, _wrapper)
    _fin.attach_db_fields(system, _wrapper)
    return places_list


def get_edit_places(system, city, state):
    edit_places = system.places_df[
        system.places_df['city'].fillna('').astype(str).str.lower() == city.lower()
    ]
    if edit_places.empty:
        edit_places = system.places_df[
            system.places_df['state'].fillna('').astype(str).str.lower() == state.lower()
        ]

    C = edit_places['rating'].mean()
    edit_places = edit_places.copy()
    edit_places['rating_score'] = edit_places.apply(
        lambda x: _sc.weighted_place_rating(system, x, C), axis=1
    )
    edit_place_recommendation = edit_places.sort_values('rating_score', ascending=False)
    print("edit_place_recommendation", edit_place_recommendation)
    return edit_place_recommendation.head(20)


def validate_user_preferences(preferences):
    required_fields = [
        'preferred_activities', 'places_of_interest', 'number_of_people',
        'travel_group_type', 'food_preferences', 'user_location',
        'current_month', 'trip_type', 'trip_duration'
    ]
    for field in required_fields:
        if field not in preferences:
            raise ValueError(f"Missing required field: {field}")
    if not isinstance(preferences['preferred_activities'], list):
        raise ValueError("preferred_activities must be a list")
    if not isinstance(preferences['number_of_people'], int):
        raise ValueError("number_of_people must be an integer")


def trim_for_prompt(system, df, cols, n):
    available = [c for c in cols if c in df.columns]
    return df[available].head(n)
