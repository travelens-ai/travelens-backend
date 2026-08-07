BUDGET_TIER_MAP = {
    'low':        'budget',
    'budget':     'budget',
    'mid range':  'mid',
    'mid':        'mid',
    'high range': 'high',
    'high':       'high',
    'luxury':     'luxury',
}

HOTEL_TIER_STARS = {
    'budget':  (0, 2),
    'mid':     (3, 3),
    'high':    (4, 4),
    'luxury':  (5, 5),
}

# Google place_types tokens → trip type match (81% DB coverage)
TRIP_TYPE_PLACE_TYPES = {
    'adventure':  ['hiking_area', 'campground', 'sports_activity_location', 'ski_resort', 'river'],
    'honeymoon':  ['beach', 'resort_hotel', 'spa', 'scenic_spot', 'garden'],
    'spiritual':  ['place_of_worship', 'hindu_temple', 'buddhist_temple', 'church', 'mosque'],
    'family':     ['amusement_park', 'zoo', 'water_park', 'national_park', 'wildlife_park'],
    'workation':  [],
    'leisure':    [],
    'business':   [],
}

# Our curated `type` column values → trip type match (~300 distinct values in DB)
TRIP_TYPE_PLACE_TYPE_NAMES = {
    'adventure':  ['trekking trail', 'trekking', 'adventure activity', 'adventure', 'adventure spot',
                   'glacier', 'cave', 'caves', 'wildlife safari', 'camping site', 'mountain peak',
                   'peak', 'mountain', 'mountain pass', 'river', 'trekking route', 'adventure park'],
    'honeymoon':  ['beach', 'scenic valley', 'waterfall', 'waterfalls', 'hill station', 'lake', 'lakes',
                   'scenic viewpoint', 'scenic island', 'scenic hill', 'scenic mountain', 'scenic spot',
                   'scenic area', 'botanical garden', 'river cruise', 'meadow', 'hill', 'hills',
                   'backwaters', 'hot spring', 'hot springs'],
    'spiritual':  ['temple', 'monastery', 'shrine', 'gurudwara', 'church', 'mosque', 'ghat',
                   'ashram', 'stupa', 'buddhist monastery', 'sacred lake', 'religious site',
                   'religious shrine', 'cave temple', 'cave temples', 'jain temple',
                   'meditation center', 'meditation garden', 'yoga center', 'spiritual center',
                   'religious', 'religious monument'],
    'family':     ['zoo', 'amusement park', 'water park', 'national park', 'wildlife sanctuary',
                   'botanical garden', 'aquarium', 'deer park', 'elephant camp', 'tiger reserve',
                   'wildlife reserve', 'wildlife park', 'nature park', 'bird sanctuary',
                   'science centre', 'science center', 'science museum', 'planetarium',
                   'nature camp', 'ecotourism', 'eco tourism'],
    'workation':  [],
    'leisure':    [],
    'business':   [],
}

# Per-meal cost caps in INR (breakfast_max, lunch_max, dinner_max).
# Cost column in restaurants is "cost for two" — double these before comparing.
MEAL_COST_CAPS = {
    'budget':  (200,  350,  400),
    'mid':     (400,  700,  700),
    'high':    (700,  1200, 1500),
    'luxury':  (1200, 2500, 9999),
}

# Columns sent to the LLM for each dataset — the only gate controlling prompt size.
PLACE_COLS_PROMPT = [
    'effective_name',
    'primary_type_name',
    'google_rating',
    'famous activities',
    'editorial_summary',
    'review_summary',
    'suitable_for',
]
HOTEL_COLS_PROMPT = ['property_name', 'city', 'hotel_star_rating', 'site_review_rating', 'property_type', 'pageurl']
REST_COLS_PROMPT  = ['Name', 'City', 'Cuisine', 'Rating', 'Votes', 'Cost', 'Locality', 'suitable_slots']

# Pre-built tier table strings used verbatim in prompt builders.
HOTEL_TIER_TABLE = (
    "| tier    | hotel/night      |\n"
    "|---------|------------------|\n"
    "| budget  | <₹1,500          |\n"
    "| mid     | ₹1,500–₹4,500    |\n"
    "| high    | ₹4,500–₹10,000   |\n"
    "| luxury  | ₹10,000+         |"
)

MEAL_TIER_TABLE = (
    "| tier   | breakfast | lunch   | dinner  |\n"
    "|--------|-----------|---------|----------|\n"
    "| budget | <₹200     | <₹350   | <₹400    |\n"
    "| mid    | <₹400     | <₹700   | <₹700    |\n"
    "| high   | <₹700     | <₹1,200 | <₹1,500  |\n"
    "| luxury | <₹1,200   | <₹2,500 | ₹3,000+  |"
)

FULL_TIER_TABLE = (
    "| tier    | hotel/night      | breakfast | lunch   | dinner  |\n"
    "|---------|-----------------|-----------|---------|----------|\n"
    "| budget  | <₹1,500         | <₹200     | <₹350   | <₹400    |\n"
    "| mid     | ₹1,500–₹4,500   | <₹400     | <₹700   | <₹700    |\n"
    "| high    | ₹4,500–₹10,000  | <₹700     | <₹1,200 | <₹1,500  |\n"
    "| luxury  | ₹10,000+        | <₹1,200   | <₹2,500 | ₹3,000+  |"
)
