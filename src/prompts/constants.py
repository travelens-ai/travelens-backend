BUDGET_TIER_MAP = {
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
    'short_formatted_address',
    'primary_type_name',
    'google_rating',
    'google_rating_count',
    'rating',
    'famous activities',
    'best month to visit',
    'opening_hours',
    'editorial_summary',
    'review_summary',
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
