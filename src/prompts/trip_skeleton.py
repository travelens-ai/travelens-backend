from prompts.constants import BUDGET_TIER_MAP, HOTEL_TIER_TABLE


def generate_trip_skeleton_prompt(user_preferences, top_places, top_restaurants, top_hotels):
    trip_duration = user_preferences['trip_duration']
    _raw_pref = str(user_preferences.get('hotel_preference') or user_preferences.get('budget') or 'mid').strip().lower()
    hotel_pref = BUDGET_TIER_MAP.get(_raw_pref, _raw_pref)

    system_content = """You are a senior human trip planner. Produce ONLY the trip-level summary
for a multi-day trip — NOT the day-by-day plan.

Your entire response must be a single raw JSON object. Start with { and end with }. No markdown,
no code fences, no explanation. Use EXACT key names shown. No trailing commas. No NaN — use "" for
missing strings. No comments inside JSON."""

    user_content = f"""Create the trip-level summary for this trip.

## User Preferences
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Places of interest: {user_preferences['places_of_interest']}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}
- Starting location: {user_preferences['user_location']}
- Travel month: {user_preferences['current_month']}
- Trip type: {user_preferences['trip_type']}
- Trip duration: {trip_duration} days
- Budget: {user_preferences['budget']}
- Hotel preference tier: {hotel_pref}

## Hotels Dataset (prefer these; supplement with your knowledge)
{top_hotels.to_csv(index=False)}

## Budget tiers
{HOTEL_TIER_TABLE}
The user's preferred tier is "{hotel_pref}". Make `selected` the best match for that tier.

## Rules
1. Output ONLY the trip-level fields below — do NOT include any day-by-day `itinerary` array.
2. Hotels are GROUPED by city. If the trip covers one city, provide one group. If multi-city (e.g. Rajasthan covering Jaipur + Jodhpur), provide one group per city with the correct from_day/to_day range.
3. Each hotel group has `selected` (best pick for user's tier) and `alternatives` (1–2 other tier options). Pick from the Hotels Dataset; use your knowledge only if a tier has no dataset candidate.
4. `description` is a short, engaging 2-3 sentence overview of the whole {trip_duration}-day trip.
5. `price_estimated_range` is the total per-head estimate; keep within {user_preferences['budget']} if realistic, else show the real range.
6. Provide 2-4 `similar_places`.

## Output Format (JSON)

{{
  "name": "Destination Name",
  "description": "Short engaging overview of the whole trip",
  "city": "City Name",
  "state": "State Name",
  "total_days": {trip_duration},
  "notes": "",
  "price_estimated_range": "e.g. ₹12,000–₹18,000 per head",
  "hotels": [
    {{
      "city": "City Name",
      "from_day": 1,
      "to_day": {trip_duration},
      "selected": {{"name": "Best Hotel", "type": "{hotel_pref}", "price_range": "₹X–₹Y/night", "rating": "4.3", "location": "City, State", "reason": "Best match for your tier", "link": "https://..."}},
      "alternatives": [
        {{"name": "Budget Option", "type": "budget", "price_range": "₹800–₹1,500/night", "rating": "4.0", "location": "City, State", "reason": "Affordable, central", "link": "https://..."}},
        {{"name": "Luxury Option", "type": "luxury", "price_range": "₹12,000+/night", "rating": "4.8", "location": "City, State", "reason": "Premium experience", "link": "https://..."}}
      ]
    }}
  ],
  "similar_places": [
    {{"placename": "Alternative 1", "description": "Why it fits", "state": "State Name", "price_estimated_range": "₹..."}},
    {{"placename": "Alternative 2", "description": "Why it fits", "state": "State Name", "price_estimated_range": "₹..."}}
  ]
}}
"""
    return [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user_content},
    ]
