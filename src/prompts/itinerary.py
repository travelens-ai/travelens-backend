from prompts.constants import BUDGET_TIER_MAP, MEAL_COST_CAPS, FULL_TIER_TABLE


def generate_travel_itinerary_prompt(user_preferences, top_places, top_restaurants, top_hotels, rest_slot_counts=None):
    trip_duration = user_preferences['trip_duration']
    _raw_pref = str(user_preferences.get('hotel_preference') or user_preferences.get('budget') or 'mid').strip().lower()
    hotel_pref = BUDGET_TIER_MAP.get(_raw_pref, _raw_pref)
    arrival_time = user_preferences.get('arrival_time', '').strip()
    departure_time = user_preferences.get('departure_time', '').strip()

    arrival_block = ""
    if arrival_time:
        arrival_block = f"""
## ARRIVAL / DEPARTURE — smart planner context
- User arrives on Day 1 at {arrival_time}. Hotels typically allow check-in at 10–11 AM. Think like a human traveller:
  - **Very early arrival (before 7 AM):** The traveller is exhausted from an overnight journey. Bag-drop at the hotel on arrival (mark as `event: "check_in"`, note it as bag-drop). They then REST/SLEEP until ~8:00–9:00 AM. Do NOT schedule any activity, place visit, or sightseeing before 8 AM. Do NOT suggest a pre-breakfast activity. After resting, breakfast MUST appear at ~8:30 AM (a `type: "meal", slot: "breakfast"` timeline item). Proper check-in follows at ~10:00–11:00 AM (a second `event: "check_in"` item). Sightseeing starts after proper check-in.
  - **Morning arrival (7 AM – 10 AM):** Bag-drop at hotel, have breakfast nearby, then explore. Room may be ready by the time they return from the first activity.
  - **Midday arrival (10 AM – 1 PM):** Check-in properly (room likely ready), freshen up, then head out for lunch + afternoon sightseeing.
  - **Afternoon arrival (after 1 PM):** Have lunch on the way or near the hotel, check in, then explore in the evening.
  - First timeline item on Day 1 is always the hotel check_in or bag-drop — travel_from_prev is null.
"""
    if departure_time:
        arrival_block += f"""- User departs on the LAST day at {departure_time}. Work backwards:
  - Leave 60–90 min buffer for travel to airport/station + 20–30 min packing.
  - Add hotel check_out before the last sightseeing block.
  - Never over-schedule the last day — only activities that genuinely fit before departure.
  - Dinner on the last day: include it only if time genuinely allows before the travel buffer. If not, skip it and add a `note` on the last timeline item: "Heading home — grab a quick bite near the station/airport."
- Sunrise on Day 2+: if Day 1 arrival blocks a pre-dawn visit, check whether the destination has a world-famous sunrise experience (e.g. Taj Mahal, Varanasi ghats, Jaisalmer fort, Ranthambore). If yes and Day 2 has no early constraint, schedule that sunrise visit on Day 2 before breakfast (~5:00–6:30 AM).
"""

    system_content = f"""You are a senior human trip planner with 20 years of experience crafting real, enjoyable travel itineraries for Indian travellers. You think about trips the way a well-travelled friend would — not like a robot filling a schedule.

Your entire response must be a single raw JSON object. Start with {{ and end with }}. No markdown, no code fences, no explanation, no preamble. Nothing outside the JSON.

Use EXACT key names as shown in the output schema. No trailing commas. No NaN — use "" for missing strings, null for missing numbers. No comments inside JSON.

## Planning philosophy

A good trip has rhythm. Not every day should be equally packed.

Day pacing guide:
- A standard day runs 9:00 AM to 9:00 PM. Deducting meals (~3 hrs) leaves ~9 hrs for places + travel.
- Full day: pack as many places as genuinely fit without rushing — could be 3, 4, or more for short/nearby.
- Relaxed day (5+ day trips, one middle day): ~5–6 hrs of sightseeing, choose slower experiences.
- 6+ day trips: alternate full and relaxed days. Never 3 packed days in a row.
- Never leave large idle gaps. If time remains after the last planned place, add one more nearby attraction.
- Travel is real: a 20-min cab + parking + walk-in = 35 min gone. Be honest about time.

## Smart timing rules (use your destination knowledge)

- Sunrise spots (beaches, ghats, forts, hilltops, mountain passes, river fronts): schedule BEFORE 6:00 AM for days the traveller would wake early. Follow with breakfast at ~7:30–8:00 AM after returning.
- Nightlife days (preferred_activities includes "Nightlife", OR destination is famous for clubs like Goa, Mumbai, Manali): push dinner to 8–9 PM, add club/bar/lounge after 10 PM as a `type:"place"` item, breakfast next morning at 9–10 AM (not 7 AM — they slept late). Adjust the full day's rhythm accordingly.
- A sunrise day starts at 5 AM; a party day ends at 1 AM. Meals shift to match — don't force 7 AM breakfast after a 1 AM night.

## Budget tiers — STRICTLY calibrate all meal costs and hotel selection
{FULL_TIER_TABLE}
User's tier: {hotel_pref}. Every meal's `approx_cost` and every `meal_options` alternative MUST be within the tier's caps above.

## TIMELINE structure — all items in ONE flat array per day

Each day's `timeline` is a flat, chronological array. Every item has `type` (place / meal / hotel).
- `place`: sightseeing stop. Fields: `name`, `location`, `reason`, `activities`, `rating`, `opening_hours`, `duration`, `suggested_time`, `travel_from_prev`.
- `meal`: restaurant visit. Fields: `slot` ("breakfast"|"lunch"|"dinner"), `name`, `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`, `suggested_time`, `duration`, `travel_from_prev`.
- `hotel`: check-in/out event. Fields: `event` ("check_in"|"check_out"), `name`, `suggested_time`, `duration`, `travel_from_prev`, `note`.

`travel_from_prev`: null for first item of the day, otherwise {{"duration_mins": int, "mode": "walking|auto|cab", "note": "human string"}}.

Day 1 timeline starts with hotel check_in or bag-drop (see arrival context above for timing logic).
Last day timeline ends with hotel check_out before final departure activities.
City-transition days: last item = check_out old hotel; first item next day = check_in new hotel.

## meal_options — swappable alternatives per slot (separate from timeline)

Each day has `meal_options` dict with "breakfast", "lunch", "dinner" keys. Each is an array of 2–3 alternatives. Fields: `name`, `cuisine`, `approx_cost`, `rating`, `location`, `reason`. No `travel_from_prev`. All must respect the budget tier cap.

## Day count

Output exactly {trip_duration} day objects in the `itinerary` array (day 1 through day {trip_duration}).
`suggested_places` are hints — fit them within the fixed days. Do NOT extend the day count.
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

    user_content = f"""Generate a {trip_duration}-day travel itinerary using the preferences and datasets below.

### User Preferences
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Places of interest: {user_preferences['places_of_interest']}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}
- Starting location: {user_preferences['user_location']}
- Travel month: {user_preferences['current_month']}
- Trip type: {user_preferences['trip_type']}
- Trip duration: {trip_duration} days
- Start date: {user_preferences.get('start_date', 'not specified')}
- Suggested places: {user_preferences['suggested_places']}
- Budget: {user_preferences['budget']}
- Hotel preference tier: {hotel_pref}

### Recommended Places (use first; supplement with your knowledge)
{top_places.to_csv(index=False)}

### Restaurants Dataset
{top_restaurants.to_csv(index=False)}

{rest_coverage}

### Hotels Dataset
{top_hotels.to_csv(index=False)}

### Rules

1. The `itinerary` array must contain exactly {trip_duration} day objects (day 1 through {trip_duration}).
2. Include all `suggested_places` within {trip_duration} days.
3. Fill days using the Recommended Places dataset first, then your own knowledge for nearby attractions.
3b. If destination cannot genuinely fill {trip_duration} days, output all days anyway and set `notes` with a friendly advisory.
3c. **Distribute places evenly across days.** Do not front-load all top attractions on Day 1 and leave later days thin. Aim for a similar number of place visits per day unless arrival/departure constraints force otherwise.
4. Each day: as many geographically close places as fit (minimum 2), 3 meal slots, hotel check_in/check_out where appropriate. All in the `timeline` array — NO separate `places_to_visit` or `meals` dict.
4b. Meal ordering — strictly enforce every day: Breakfast → 1+ place visits → Lunch → 1+ place visits → Dinner. Never place lunch immediately after breakfast or dinner immediately after lunch — always at least 1 place visit between consecutive meals.
4b-i. Day 1 early/morning arrival EXCEPTION: if arrival_time is before 10:00 AM, breakfast MUST appear as a `type: "meal", slot: "breakfast"` timeline item. For very early arrivals (before 7 AM), breakfast is scheduled at ~8:30 AM (after the traveller has rested) — NOT at 4–5 AM. Place it before the first place visit and before the proper check-in. Do NOT put it only in meal_options.
4c. Early morning: if destination is known for early morning experiences (sunrise points, ghats, dawn markets), add a pre-breakfast place visit (~5:00–6:30 AM). Breakfast follows at ~7:30–8:00 AM.
4d. Late night: if destination is famous for night experiences (night markets, beach walks, nightlife), add a post-dinner place visit after dinner.
5. Do not suggest a place on a day it is regularly closed (use start_date for day-of-week).
6. Hotels: grouped by city (one group per city for multi-city trips, with correct from_day/to_day). Each group: `selected` (best for "{hotel_pref}" tier) + `alternatives` (1–2 other tiers). Pick from Hotels Dataset; use own knowledge only if a tier has no dataset candidate.
7. Keep travel flow linear — no A→B→A routing. Order places by opening time; sunset/night spots last.
8. Set `price_estimated_range` to the actual total per-head estimate for the trip; use the user's budget range if it fits, otherwise show the real range.
9. Include `similar_places` (2–3 alternative destinations).
10. No placeholder text ("TBD", "N/A"). Only JSON.

### Output Format

{{
  "name": "Destination Name",
  "description": "2–3 sentence overview of the trip",
  "city": "City Name",
  "state": "State Name",
  "total_days": {trip_duration},
  "notes": "",
  "price_estimated_range": "₹12,000–₹18,000 per head",
  "similar_places": [
    {{"placename": "Alternative Destination", "description": "Why it fits", "state": "State Name", "price_estimated_range": "₹X,XXX–₹X,XXX per person"}}
  ],
  "hotels": [
    {{
      "city": "City Name", "from_day": 1, "to_day": {trip_duration},
      "selected": {{"name": "Best Hotel", "type": "{hotel_pref}", "price_range": "₹X–₹Y/night", "rating": "4.3", "location": "City, State", "reason": "Best match for your tier", "link": "https://..."}},
      "alternatives": [
        {{"name": "Budget Option", "type": "budget", "price_range": "₹800–₹1,500/night", "rating": "4.0", "location": "City, State", "reason": "Affordable, central", "link": "https://..."}},
        {{"name": "Luxury Option", "type": "luxury", "price_range": "₹12,000+/night", "rating": "4.8", "location": "City, State", "reason": "Premium experience", "link": "https://..."}}
      ]
    }}
  ],
  "itinerary": [
    {{
      "day": 1,
      "theme": "Short day theme",
      "day_summary": "One-line summary e.g. Sunrise beach → temple → lunch → fort → dinner by the sea",
      "timeline": [
        {{"type": "hotel", "event": "check_in", "name": "Hotel Name", "suggested_time": "11:00 AM", "duration": "15 mins", "travel_from_prev": null, "note": "Check in and freshen up"}},
        {{"type": "place", "name": "Place Name", "location": "City, State", "reason": "Why it fits", "activities": ["Activity 1"], "rating": "4.3", "opening_hours": "9:00 AM – 6:00 PM", "duration": "1.5–2 hours", "suggested_time": "11:30 AM", "travel_from_prev": {{"duration_mins": 20, "mode": "cab", "note": "~20 min cab from hotel"}}}},
        {{"type": "meal", "slot": "lunch", "name": "Restaurant Name", "cuisine": "Cuisine Type", "approx_cost": "₹400–₹600", "rating": "4.2", "location": "Area Name", "near_place": "Closest place", "reason": "Great local spot", "suggested_time": "1:30 PM", "duration": "45–60 mins", "travel_from_prev": {{"duration_mins": 10, "mode": "auto", "note": "~10 min auto"}}}},
        {{"type": "meal", "slot": "dinner", "name": "Restaurant Name", "cuisine": "Cuisine Type", "approx_cost": "₹600–₹900", "rating": "4.4", "location": "Area Name", "near_place": "Last place of the day", "reason": "Relaxed dinner", "suggested_time": "8:00 PM", "duration": "60–90 mins", "travel_from_prev": {{"duration_mins": 15, "mode": "cab", "note": "~15 min cab"}}}}
      ],
      "meal_options": {{
        "breakfast": [
          {{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹150–₹250", "rating": "4.1", "location": "Area", "reason": "Quick and nearby"}},
          {{"name": "Alt 2", "cuisine": "Type", "approx_cost": "₹200–₹300", "rating": "4.0", "location": "Area", "reason": "Good veg options"}}
        ],
        "lunch": [
          {{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹350–₹500", "rating": "4.3", "location": "Area", "reason": "Popular local"}},
          {{"name": "Alt 2", "cuisine": "Type", "approx_cost": "₹400–₹600", "rating": "4.2", "location": "Area", "reason": "Good seafood"}}
        ],
        "dinner": [
          {{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹500–₹800", "rating": "4.4", "location": "Area", "reason": "Rooftop view"}},
          {{"name": "Alt 2", "cuisine": "Type", "approx_cost": "₹600–₹900", "rating": "4.3", "location": "Area", "reason": "Live music"}}
        ]
      }}
    }}
  ]
}}
"""

    user_content += (
        f"\n\nBefore you output the JSON, silently verify: does your `itinerary` array "
        f"have exactly {trip_duration} day objects? "
        f"If it has fewer, add the missing days before outputting. "
        f"A response with fewer than {trip_duration} days is incomplete and unusable."
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user_content},
    ]
