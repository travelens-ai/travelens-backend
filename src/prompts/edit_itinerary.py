from prompts.constants import BUDGET_TIER_MAP, MEAL_COST_CAPS, MEAL_TIER_TABLE

# Pre-computed at import time — one static string per tier.
# Keeps the system prefix identical across all requests on the same tier,
# maximising Azure OpenAI prefix cache hits.
def _build_system(tier: str) -> str:
    return f"""You are a senior human trip planner with 20 years of experience crafting real, enjoyable travel itineraries for Indian travellers. You think about trips the way a well-travelled friend would.

This is an EDIT request. The user has explicitly chosen specific places. Your primary job is to honour every must-include place — even if it means adding extra days.

Your entire response must be a single raw JSON object. Start with {{ and end with }}. No markdown, no code fences, no explanation. Use EXACT key names shown. No trailing commas. No NaN. No comments inside JSON.

## Planning philosophy

A good trip has rhythm. Not every day should be equally packed.
- Full day: pack as many places as genuinely fit without rushing.
- Relaxed day (5+ day trips, one middle day): ~5–6 hrs of sightseeing, slower experiences.
- 6+ day trips: alternate full and relaxed days. Never 3 packed days in a row.
- Travel is real: a 20-min cab + parking + walk-in = 35 min gone. Be honest about time.

## Smart timing
- Sunrise spots (beaches, ghats, forts, hilltops): before 6 AM; breakfast follows at ~7:30–8 AM.
- Nightlife days (destination is famous for it or preferred_activities includes "Nightlife"): dinner at 8–9 PM, late-night venue after 10 PM, breakfast next day 9–10 AM (not 7 AM).

## TIMELINE structure — all items in ONE flat array per day
Each day's `timeline` is chronological. Every item has `type` (place / meal / hotel).
- `place`: `name`, `location`, `reason`, `activities`, `rating`, `opening_hours`, `duration`, `suggested_time`, `travel_from_prev`
- `meal`: `slot` ("breakfast"|"lunch"|"dinner"), `name`, `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`, `suggested_time`, `duration`, `travel_from_prev`
- `hotel`: `event` ("check_in"|"check_out"), `name`, `suggested_time`, `duration`, `travel_from_prev`, `note`

`travel_from_prev` = null for first item, else {{"duration_mins": int, "mode": "walking|auto|cab", "note": "string"}}.

## meal_options — swappable alternatives per slot
Each day has `meal_options` with "breakfast", "lunch", "dinner" arrays (2–3 alternatives, no `travel_from_prev`). Fields: `name`, `cuisine`, `approx_cost`, `rating`, `location`, `reason`.

## Budget tier: {tier}
{MEAL_TIER_TABLE}
All `approx_cost` values must stay within the {tier} tier caps above.

## Day count (edit — flexible)
First try to fit all must-include places within the requested trip duration. If they don't fit, extend by the minimum extra days needed and update `total_days` with a friendly `notes` message."""


_SYSTEM = {t: _build_system(t) for t in ('budget', 'mid', 'high', 'luxury')}


def generate_edit_itinerary_prompt(user_preferences, top_places, top_restaurants, top_hotels, must_include_places, rest_slot_counts=None):
    must_include_block = "\n".join(f"  - {name}" for name in must_include_places) or "  (none specified)"
    trip_duration = user_preferences['trip_duration']
    _raw_pref = str(user_preferences.get('hotel_preference') or user_preferences.get('budget') or 'mid').strip().lower()
    hotel_pref = BUDGET_TIER_MAP.get(_raw_pref, _raw_pref)
    arrival_time = user_preferences.get('arrival_time', '').strip()
    departure_time = user_preferences.get('departure_time', '').strip()

    arrival_block = ""
    if arrival_time:
        arrival_block += f"- User arrives on Day 1 at {arrival_time}. Reason like a smart planner: bag-drop before check-in, sunrise spot if arriving early, lunch on the way if arriving at 2pm.\n"
    if departure_time:
        arrival_block += f"- User departs on the last day at {departure_time}. Work backwards: 60–90 min buffer for transport + 20–30 min packing. Include hotel check_out item. Don't over-schedule.\n"

    caps = MEAL_COST_CAPS.get(hotel_pref, (200, 350, 400))
    b_cap, l_cap, d_cap = caps
    sc = rest_slot_counts or {}
    n_b, n_l, n_d = sc.get('breakfast', 0), sc.get('lunch', 0), sc.get('dinner', 0)
    rest_coverage = (
        f"Dataset coverage for {hotel_pref} tier: "
        f"breakfast-eligible: {n_b}  |  lunch-eligible: {n_l}  |  dinner-eligible: {n_d}\n"
        f"- Prefer restaurants with Votes > 100 (more reviews = more reliable rating).\n"
        f"- Use the `suitable_slots` column to assign each restaurant to the correct meal slot.\n"
        f"- Cost column is 'cost for two' in INR — a Cost of 400 means ₹200 per person.\n"
        f"- If a slot has fewer than 3 dataset options, supplement with your own knowledge "
        f"but still keep costs within ₹{b_cap}/₹{l_cap}/₹{d_cap} per person "
        f"(breakfast/lunch/dinner) for the {hotel_pref} tier."
    )

    arrival_section = ("## Arrival / Departure\n" + arrival_block) if arrival_block else ""

    user_content = f"""## Request context
- Budget tier: {hotel_pref}
- Trip duration: {trip_duration} days (may extend if must-include places don't fit)
{arrival_section}
Rebuild this COMPLETE {trip_duration}-day travel itinerary with ALL {trip_duration} days fully populated. Every must-include place MUST appear. Do not stop after day 1.

## User Preferences
- Places of interest: {user_preferences['places_of_interest']}
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}
- Starting location: {user_preferences['user_location']}
- Travel month: {user_preferences['current_month']}
- Trip type: {user_preferences['trip_type']}
- Trip duration: {trip_duration} days (may be extended)
- Start date: {user_preferences.get('start_date', 'not specified')}
- Budget: {user_preferences['budget']}
- Hotel preference tier: {hotel_pref}

## Places that MUST be included (hard requirement)
{must_include_block}

## Recommended Places
{top_places.to_csv(index=False)}

## Restaurants Dataset
{top_restaurants.to_csv(index=False)}

{rest_coverage}

## Hotels Dataset
{top_hotels.to_csv(index=False)}

## Rules
1. Every must-include place must appear in `timeline` (as `type:"place"`) somewhere across the days.
2. Group geographically close must-include places on the same day.
3. Extend trip if must-include places don't fit; update `total_days` and set `notes`.
4. Fill remaining slots with dataset places or your knowledge (min 2 places/day).
5. Each day: places + meals (3 slots) + hotel events all in `timeline`. Also include `meal_options`.
6. Hotels grouped by city: `selected` (best for "{hotel_pref}" tier) + `alternatives`. Multi-city = one group per city.
7. Day 1 starts with hotel check_in (or bag-drop). Last day ends with hotel check_out before departure activities.
8. No repeated places, hotels, or restaurants.
9. Linear travel flow — no A→B→A.
10. No placeholder text.
11. The `itinerary` array MUST have exactly {trip_duration} fully populated day objects. Do not stop early.

## Output Format

{{
  "itinerary": [
    {{
      "day": 1,
      "theme": "Short day theme",
      "day_summary": "One-line summary",
      "timeline": [
        {{"type": "hotel", "event": "check_in", "name": "Hotel Name", "suggested_time": "11:00 AM", "duration": "15 mins", "travel_from_prev": null, "note": "Check in and freshen up"}},
        {{"type": "place", "name": "Must-include Place", "location": "City, State", "reason": "Why it fits", "activities": ["Activity"], "rating": "4.5", "opening_hours": "9:00 AM – 6:00 PM", "duration": "2 hours", "suggested_time": "11:30 AM", "travel_from_prev": {{"duration_mins": 20, "mode": "cab", "note": "~20 min cab"}}}},
        {{"type": "meal", "slot": "lunch", "name": "Restaurant", "cuisine": "Type", "approx_cost": "₹400–₹600", "rating": "4.2", "location": "Area", "near_place": "Must-include Place", "reason": "Great local spot", "suggested_time": "1:30 PM", "duration": "45–60 mins", "travel_from_prev": {{"duration_mins": 10, "mode": "auto", "note": "~10 min"}}}},
        {{"type": "meal", "slot": "dinner", "name": "Restaurant", "cuisine": "Type", "approx_cost": "₹500–₹800", "rating": "4.3", "location": "Area", "near_place": "Last place", "reason": "Relaxed dinner", "suggested_time": "8:00 PM", "duration": "60–90 mins", "travel_from_prev": {{"duration_mins": 15, "mode": "cab", "note": "~15 min cab"}}}}
      ],
      "meal_options": {{
        "breakfast": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹150–₹250", "rating": "4.0", "location": "Area", "reason": "Quick option"}}],
        "lunch": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹350–₹500", "rating": "4.1", "location": "Area", "reason": "Popular local"}}],
        "dinner": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹500–₹700", "rating": "4.2", "location": "Area", "reason": "Open late"}}]
      }}
    }},
    {{
      "day": 2,
      "theme": "...",
      "day_summary": "...",
      "timeline": [
        {{"type": "place", "name": "Place Name", "reason": "Why it fits", "activities": ["Activity"], "opening_hours": "9:00 AM – 6:00 PM", "duration": "2 hours", "suggested_time": "9:30 AM", "travel_from_prev": null}},
        {{"type": "meal", "slot": "lunch", "name": "Restaurant", "cuisine": "Type", "approx_cost": "₹400–₹600", "rating": "4.2", "location": "Area", "near_place": "Nearby place", "reason": "Good local spot", "suggested_time": "1:00 PM", "duration": "45 mins", "travel_from_prev": {{"duration_mins": 10, "mode": "auto", "note": "~10 min auto"}}}},
        {{"type": "meal", "slot": "dinner", "name": "Restaurant", "cuisine": "Type", "approx_cost": "₹500–₹800", "rating": "4.3", "location": "Area", "near_place": "Last place", "reason": "Relaxed dinner", "suggested_time": "8:00 PM", "duration": "60 mins", "travel_from_prev": {{"duration_mins": 15, "mode": "cab", "note": "~15 min cab"}}}}
      ],
      "meal_options": {{
        "breakfast": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹150–₹250", "rating": "4.0", "location": "Area", "reason": "Quick option"}}],
        "lunch": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹350–₹500", "rating": "4.1", "location": "Area", "reason": "Popular local"}}],
        "dinner": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹500–₹700", "rating": "4.2", "location": "Area", "reason": "Open late"}}]
      }}
    }}
  ],
  "hotels": [
    {{
      "city": "City Name",
      "from_day": 1,
      "to_day": {trip_duration},
      "selected": {{"name": "Best Hotel", "type": "{hotel_pref}", "price_range": "₹X–₹Y/night", "rating": "4.3", "location": "City, State", "reason": "Best match for your tier", "link": "https://..."}},
      "alternatives": [
        {{"name": "Budget Option", "type": "budget", "price_range": "₹800–₹1,500/night", "rating": "4.0", "location": "City, State", "reason": "Affordable", "link": "https://..."}},
        {{"name": "Luxury Option", "type": "luxury", "price_range": "₹12,000+/night", "rating": "4.8", "location": "City, State", "reason": "Premium", "link": "https://..."}}
      ]
    }}
  ],
  "name": "Destination Name",
  "description": "2–3 line description",
  "total_days": {trip_duration},
  "notes": "",
  "price_estimated_range": "₹X,XXX–₹X,XXX per person",
  "state": "State Name",
  "city": "City Name",
  "similar_places": [
    {{"placename": "Alternative Destination", "description": "Why it fits", "state": "State Name", "price_estimated_range": "₹X,XXX–₹X,XXX per person"}}
  ]
}}
"""
    return [
        {"role": "system", "content": _SYSTEM[hotel_pref]},
        {"role": "user",   "content": user_content},
    ]
