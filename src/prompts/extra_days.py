from prompts.constants import BUDGET_TIER_MAP, MEAL_COST_CAPS, MEAL_TIER_TABLE


def generate_extra_days_prompt(user_preferences, top_places, top_restaurants, top_hotels,
                               start_day, num_days, used_places, rest_slot_counts=None):
    used_block = ", ".join(used_places) if used_places else "(none yet)"
    _raw_pref = str(user_preferences.get('hotel_preference') or user_preferences.get('budget') or 'mid').strip().lower()
    hotel_pref = BUDGET_TIER_MAP.get(_raw_pref, _raw_pref)
    departure_time = user_preferences.get('departure_time', '').strip()
    trip_duration = user_preferences.get('trip_duration', num_days)

    arrival_block = ""
    if departure_time and (start_day + num_days - 1) >= int(trip_duration):
        arrival_block = (
            f"\n## DEPARTURE CONTEXT\n"
            f"- User departs on Day {trip_duration} at {departure_time}. Work backwards: 60–90 min travel buffer + 20–30 min packing.\n"
            f"- Add hotel check_out before the final activities.\n"
            f"- Dinner on the last day: include only if time genuinely allows before the travel buffer. If not, skip it and add a note: \"Heading home — grab a quick bite near the station/airport.\"\n"
        )

    system_content = f"""You are a senior human trip planner extending an existing itinerary with additional days. You think about trips the way a well-travelled friend would — not like a robot filling a schedule.

Your entire response must be a single raw JSON object with one key `itinerary` containing an array of the new day objects. Start with {{ and end with }}. No markdown, no code fences, no explanation, no preamble.

No trailing commas. No NaN — use "" for missing strings, null for missing numbers. No comments inside JSON.

## Planning philosophy

A good trip has rhythm. Not every day should be equally packed.
- A standard day runs 9:00 AM to 9:00 PM. Deducting meals (~3 hrs) leaves ~9 hrs for places + travel.
- Full day: pack as many places as genuinely fit without rushing — could be 3, 4, or more for short/nearby.
- Relaxed day (5+ day trips, one middle day): ~5–6 hrs of sightseeing, slower experiences.
- 6+ day trips: alternate full and relaxed days. Never 3 packed days in a row.
- Never leave large idle gaps. If time remains after the last planned place, add one more nearby attraction.
- Travel is real: a 20-min cab + parking + walk-in = 35 min gone. Be honest about time.

## Smart timing rules
- Sunrise spots (beaches, ghats, forts, hilltops, mountain passes, river fronts): schedule before 6 AM if day warrants it; breakfast follows at ~7:30–8 AM after returning.
- Nightlife days (preferred_activities includes "Nightlife" or destination is famous for it): push dinner to 8–9 PM, add late-night venue after 10 PM as a `type:"place"` item, breakfast next day at 9–10 AM (not 7 AM — they slept late).
- A sunrise day starts at 5 AM; a party day ends at 1 AM. Meals shift to match.

## TIMELINE structure — all items in ONE flat array per day
Each day's `timeline` is chronological. Every item has `type` (place / meal / hotel).
- `place`: `name`, `location`, `reason`, `activities`, `rating`, `opening_hours`, `duration`, `suggested_time`, `travel_from_prev`.
- `meal`: `slot` ("breakfast"|"lunch"|"dinner"), `name`, `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`, `suggested_time`, `duration`, `travel_from_prev`.
- `hotel`: `event` ("check_in"|"check_out"), `name`, `suggested_time`, `duration`, `travel_from_prev`, `note`.

`travel_from_prev` = null for first item of the day, otherwise {{"duration_mins": int, "mode": "walking|auto|cab", "note": "string"}}.
City-transition days: last item = check_out old hotel; first item next day = check_in new hotel.

## meal_options — swappable alternatives per slot
Each day also has `meal_options` with "breakfast", "lunch", "dinner" arrays (2–3 alternatives each). Fields: `name`, `cuisine`, `approx_cost`, `rating`, `location`, `reason`. No `travel_from_prev`. All must respect the budget tier cap.

## Budget tier for meals: {hotel_pref}
{MEAL_TIER_TABLE}
All `approx_cost` values (in timeline and meal_options) must stay within the "{hotel_pref}" tier caps above.
{arrival_block}"""

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

    user_content = f"""Generate EXACTLY {num_days} fully populated day object(s) numbered {start_day} through {start_day + num_days - 1}. Do not stop early.

## Trip context
- Destination / places of interest: {user_preferences['places_of_interest']}
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}
- Starting location: {user_preferences.get('user_location', '')}
- Travel month: {user_preferences.get('current_month', '')}
- Trip type: {user_preferences['trip_type']}
- Trip duration: {trip_duration} days (these are days {start_day}–{start_day + num_days - 1})
- Start date: {user_preferences.get('start_date', 'not specified')} (use for day-of-week — do not suggest places closed on their scheduled day)
- Budget: {user_preferences['budget']}
- Hotel preference tier: {hotel_pref}

## Places already used — DO NOT reuse any of these
{used_block}

## Recommended Places (prefer unused ones; supplement with your knowledge)
{top_places.to_csv(index=False)}

## Restaurants Dataset
{top_restaurants.to_csv(index=False)}

{rest_coverage}

## Hotels Dataset
{top_hotels.to_csv(index=False)}

## Rules
1. Output EXACTLY {num_days} day object(s) numbered {start_day} to {start_day + num_days - 1}. Every day must be fully populated with timeline + meal_options. Do not stop after the first day.
2. Never reuse a place from the already-used list or repeat within these new days.
3. Each day: as many geographically close places as fit (min 2), 3 meal slots in timeline, meal_options dict.
3b. **Meal ordering — strictly enforce every day:** Breakfast → 1 or more place visits → Lunch → 1 or more place visits → Dinner. Never place lunch immediately after breakfast, and never place dinner immediately after lunch — there must always be at least 1 place visit between consecutive meals. Between meals, include as many nearby places as naturally fit — no upper cap.
3c. **Early morning:** If the destination is known for early morning experiences (sunrise spots, ghats, dawn markets), add a pre-breakfast place visit; breakfast follows at ~7:30–8:00 AM.
3d. **Late night:** If the destination is famous for night experiences (night markets, beach walks, nightlife), add a post-dinner place visit after dinner.
3e. **Even distribution:** Aim for a similar number of place visits per day — do not make some days thin while others are packed.
4. Do not suggest a place on a day it is regularly closed (use start_date for day-of-week calculations).
5. NO separate `places_to_visit` or `meals` dict — everything goes into `timeline`.
6. All meal costs must respect the {hotel_pref} tier caps.

## Output Format

{{"itinerary": [
  {{
    "day": {start_day},
    "theme": "Short day theme",
    "day_summary": "One-line summary of the day's flow",
    "timeline": [
      {{
        "type": "meal",
        "slot": "breakfast",
        "name": "Restaurant Name",
        "cuisine": "Cuisine Type",
        "approx_cost": "₹150–₹250",
        "rating": "4.1",
        "location": "Area Name",
        "near_place": "First place of the day",
        "reason": "Quick breakfast before heading out",
        "suggested_time": "8:00 AM",
        "duration": "30–45 mins",
        "travel_from_prev": null
      }},
      {{
        "type": "place",
        "name": "Place Name",
        "location": "City, State",
        "reason": "why it fits",
        "activities": ["Activity 1"],
        "rating": "4.3",
        "opening_hours": "9:00 AM – 6:00 PM",
        "duration": "1.5–2 hours",
        "suggested_time": "9:00 AM",
        "travel_from_prev": {{"duration_mins": 15, "mode": "cab", "note": "~15 min cab"}}
      }},
      {{
        "type": "meal",
        "slot": "lunch",
        "name": "Restaurant Name",
        "cuisine": "Cuisine Type",
        "approx_cost": "₹400–₹600",
        "rating": "4.2",
        "location": "Area Name",
        "near_place": "Closest place at midday",
        "reason": "Good spot near your midday stop",
        "suggested_time": "1:00 PM",
        "duration": "45–60 mins",
        "travel_from_prev": {{"duration_mins": 10, "mode": "auto", "note": "~10 min auto"}}
      }},
      {{
        "type": "meal",
        "slot": "dinner",
        "name": "Restaurant Name",
        "cuisine": "Cuisine Type",
        "approx_cost": "₹500–₹800",
        "rating": "4.3",
        "location": "Area Name",
        "near_place": "Last place of the day",
        "reason": "Relaxed dinner to end the day",
        "suggested_time": "8:00 PM",
        "duration": "60–90 mins",
        "travel_from_prev": {{"duration_mins": 20, "mode": "cab", "note": "~20 min cab"}}
      }}
    ],
    "meal_options": {{
      "breakfast": [
        {{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹100–₹200", "rating": "4.0", "location": "Area", "reason": "Budget-friendly"}},
        {{"name": "Alt 2", "cuisine": "Type", "approx_cost": "₹200–₹300", "rating": "4.2", "location": "Area", "reason": "Good South Indian"}}
      ],
      "lunch": [
        {{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹350–₹500", "rating": "4.1", "location": "Area", "reason": "Popular local"}},
        {{"name": "Alt 2", "cuisine": "Type", "approx_cost": "₹400–₹600", "rating": "4.3", "location": "Area", "reason": "Great thali"}}
      ],
      "dinner": [
        {{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹500–₹700", "rating": "4.2", "location": "Area", "reason": "Open late"}},
        {{"name": "Alt 2", "cuisine": "Type", "approx_cost": "₹600–₹900", "rating": "4.4", "location": "Area", "reason": "Sea view"}}
      ]
    }}
  }}{f''',
  {{
    "day": {start_day + 1},
    "theme": "...",
    "day_summary": "...",
    "timeline": [
      {{"type": "meal", "slot": "breakfast", "name": "Restaurant Name", "cuisine": "Type", "approx_cost": "₹150–₹250", "rating": "4.1", "location": "Area", "near_place": "First place", "reason": "Quick start", "suggested_time": "8:00 AM", "duration": "30 mins", "travel_from_prev": null}},
      {{"type": "place", "name": "Place Name", "reason": "Why it fits", "activities": ["Activity"], "opening_hours": "9:00 AM – 6:00 PM", "duration": "2 hours", "suggested_time": "9:00 AM", "travel_from_prev": {{"duration_mins": 15, "mode": "cab", "note": "~15 min cab"}}}},
      {{"type": "meal", "slot": "lunch", "name": "Restaurant Name", "cuisine": "Type", "approx_cost": "₹400–₹600", "rating": "4.2", "location": "Area", "near_place": "Nearby place", "reason": "Good local spot", "suggested_time": "1:00 PM", "duration": "45 mins", "travel_from_prev": {{"duration_mins": 10, "mode": "auto", "note": "~10 min auto"}}}},
      {{"type": "meal", "slot": "dinner", "name": "Restaurant Name", "cuisine": "Type", "approx_cost": "₹500–₹800", "rating": "4.3", "location": "Area", "near_place": "Last place", "reason": "Relaxed dinner", "suggested_time": "8:00 PM", "duration": "60 mins", "travel_from_prev": {{"duration_mins": 20, "mode": "cab", "note": "~20 min cab"}}}}
    ],
    "meal_options": {{
      "breakfast": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹100–₹200", "rating": "4.0", "location": "Area", "reason": "Budget-friendly"}}],
      "lunch": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹350–₹500", "rating": "4.1", "location": "Area", "reason": "Popular local"}}],
      "dinner": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹500–₹700", "rating": "4.2", "location": "Area", "reason": "Open late"}}]
    }}
  }}''' if num_days > 1 else ''}
]}}
"""
    return [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user_content},
    ]
