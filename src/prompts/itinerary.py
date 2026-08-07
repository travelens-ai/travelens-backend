import re as _re
from prompts.constants import BUDGET_TIER_MAP, MEAL_COST_CAPS, FULL_TIER_TABLE


def _parse_hour(time_str: str):
    s = (time_str or '').strip().upper()
    m = _re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?$', s)
    if not m:
        return None
    h = int(m.group(1))
    if m.group(3) == 'PM' and h != 12:
        h += 12
    elif m.group(3) == 'AM' and h == 12:
        h = 0
    return h if 0 <= h <= 23 else None


def _parse_minute(time_str: str) -> int:
    s = (time_str or '').strip().upper()
    m = _re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?$', s)
    return int(m.group(2)) if m and m.group(2) else 0


def _checkout_deadline(dep_hour: int, dep_minute: int = 0, buffer_mins: int = 90) -> str:
    """Subtract buffer from departure time and return a human clock string like '11:30 AM'."""
    total = dep_hour * 60 + dep_minute - buffer_mins
    if total < 0:
        total = 0
    h, mn = divmod(total, 60)
    suffix = "AM" if h < 12 else "PM"
    display_h = h if 1 <= h <= 12 else (12 if h == 0 else h - 12)
    return f"{display_h}:{mn:02d} {suffix}"


# Pre-computed at import time — one static string per tier.
# This guarantees identical byte sequences across all requests on the same tier,
# enabling Azure OpenAI prefix caching (needs ≥1024 tokens at the system prefix).
def _build_system(tier: str) -> str:
    return f"""You are a senior human trip planner with 20 years of experience crafting real, enjoyable travel itineraries for Indian travellers. You think about trips the way a well-travelled friend would — not like a robot filling a schedule.

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
User's tier: {tier}. Every meal's `approx_cost` and every `meal_options` alternative MUST be within the {tier} tier caps above.

## TIMELINE structure — all items in ONE flat array per day

Each day's `timeline` is a flat, chronological array. Every item has `type` (place / meal / hotel).
- `place`: sightseeing stop. Fields: `name`, `location` (**MUST be "City, State" format — e.g. "Goa, Goa" or "Rome, Italy"; NEVER a neighbourhood, area, or descriptive phrase**), `reason`, `activities`, `rating`, `opening_hours`, `duration`, `suggested_time`, `travel_from_prev`, `suitable_trip_types`, `suitable_group_types`.
- `meal`: restaurant visit. Fields: `slot` ("breakfast"|"lunch"|"dinner"), `name`, `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`, `suggested_time`, `duration`, `travel_from_prev`.
- `hotel`: check-in/out event. Fields: `event` ("check_in"|"check_out"), `name`, `suggested_time`, `duration`, `travel_from_prev`, `note`.

`travel_from_prev`: null for first item of the day, otherwise {{"duration_mins": int, "mode": "walking|auto|cab", "note": "human string"}}.

Day 1 timeline starts with hotel check_in or bag-drop (see arrival context in the user message for timing logic).
Last day timeline ends with hotel check_out before final departure activities.
City-transition days: last item = check_out old hotel; first item next day = check_in new hotel.

## meal_options — swappable alternatives per slot (separate from timeline)

Each day has `meal_options` dict with "breakfast", "lunch", "dinner" keys. Each is an array of 2–3 alternatives. Fields: `name`, `cuisine`, `approx_cost`, `rating`, `location`, `reason`. No `travel_from_prev`. All must respect the {tier} tier cap.

## Output Format

Exact values for `total_days`, `from_day`, `to_day` come from the trip duration in the user message (use that number, not the N below).

{{
  "name": "Destination Name",
  "description": "2–3 sentence overview of the trip",
  "city": "City Name",
  "state": "State Name",
  "total_days": N,
  "notes": "",
  "price_estimated_range": "₹12,000–₹18,000 per head",
  "similar_places": [
    {{"placename": "Alternative Destination", "description": "Why it fits", "state": "State Name", "price_estimated_range": "₹X,XXX–₹X,XXX per person"}}
  ],
  "hotels": [
    {{
      "city": "City Name", "from_day": 1, "to_day": N,
      "selected": {{"name": "Best Hotel", "type": "{tier}", "price_range": "₹X–₹Y/night", "rating": "4.3", "location": "City, State", "reason": "Best match for your tier", "link": "https://..."}},
      "alternatives": [
        {{"name": "Alt Option 1", "type": "{tier}", "price_range": "₹X–₹Y/night", "rating": "4.0", "location": "City, State", "reason": "Good alternative", "link": "https://..."}},
        {{"name": "Alt Option 2", "type": "{tier}", "price_range": "₹X–₹Y/night", "rating": "3.9", "location": "City, State", "reason": "Another option", "link": "https://..."}}
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
        {{"type": "place", "name": "Place Name", "location": "City, State", "reason": "Why it fits", "activities": ["Activity 1"], "rating": "4.3", "opening_hours": "9:00 AM – 6:00 PM", "duration": "1.5–2 hours", "suggested_time": "11:30 AM", "travel_from_prev": {{"duration_mins": 20, "mode": "cab", "note": "~20 min cab from hotel"}}, "suitable_trip_types": ["leisure", "honeymoon"], "suitable_group_types": ["couples", "friends"]}},
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
    }},
    {{
      "day": 2,
      "theme": "Short day theme",
      "day_summary": "One-line summary of day 2",
      "timeline": [
        {{"type": "place", "name": "Place Name", "reason": "Why it fits", "activities": ["Activity 1"], "opening_hours": "9:00 AM – 5:00 PM", "duration": "2 hours", "suggested_time": "9:30 AM", "travel_from_prev": null}},
        {{"type": "meal", "slot": "lunch", "name": "Restaurant Name", "cuisine": "Cuisine Type", "approx_cost": "₹400–₹600", "rating": "4.2", "location": "Area Name", "near_place": "Closest place", "reason": "Great local spot", "suggested_time": "1:00 PM", "duration": "45–60 mins", "travel_from_prev": {{"duration_mins": 10, "mode": "auto", "note": "~10 min auto"}}}},
        {{"type": "meal", "slot": "dinner", "name": "Restaurant Name", "cuisine": "Cuisine Type", "approx_cost": "₹600–₹900", "rating": "4.4", "location": "Area Name", "near_place": "Last place", "reason": "Relaxed dinner", "suggested_time": "8:00 PM", "duration": "60 mins", "travel_from_prev": {{"duration_mins": 15, "mode": "cab", "note": "~15 min cab"}}}}
      ],
      "meal_options": {{
        "breakfast": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹150–₹250", "rating": "4.1", "location": "Area", "reason": "Quick and nearby"}}],
        "lunch": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹350–₹500", "rating": "4.3", "location": "Area", "reason": "Popular local"}}],
        "dinner": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹500–₹800", "rating": "4.4", "location": "Area", "reason": "Rooftop view"}}]
      }}
    }}
  ]
}}

## Restaurant Dataset Notes
- Prefer restaurants with Votes > 100 (more reviews = more reliable rating).
- Use the `suitable_slots` column to assign each restaurant to the correct meal slot.
- Cost column is 'cost for two' in INR — a Cost of 400 means ₹200 per person.
- If a slot has fewer than 3 dataset options, supplement with your own knowledge but still keep costs within the {tier} tier caps above."""


_SYSTEM = {t: _build_system(t) for t in ('budget', 'mid', 'high', 'luxury')}


def generate_travel_itinerary_prompt(user_preferences, top_places, top_restaurants, top_hotels, rest_slot_counts=None):
    trip_duration = user_preferences['trip_duration']
    _raw_pref = str(user_preferences.get('hotel_preference') or user_preferences.get('budget') or 'mid').strip().lower()
    hotel_pref = BUDGET_TIER_MAP.get(_raw_pref, _raw_pref)
    arrival_time = user_preferences.get('arrival_time', '').strip()
    departure_time = user_preferences.get('departure_time', '').strip()
    arr_hour = _parse_hour(arrival_time)
    dep_hour = _parse_hour(departure_time)
    dep_minute = _parse_minute(departure_time) if departure_time else 0

    arrival_block = ""
    if arrival_time:
        if arr_hour is not None and arr_hour >= 10:
            if arr_hour >= 20:
                # Very late night — no activities at all on Day 1
                arrival_block = (
                    f"**Day 1 late-night arrival at {arrival_time} — overrides Rule 4b for Day 1 only:** "
                    f"Do NOT add breakfast, lunch, or any place/activity to the Day 1 timeline. "
                    f"Omit breakfast and lunch from `meal_options` for Day 1 — `meal_options` for Day 1 must only have 'dinner' key. "
                    f"The ONLY items on Day 1: hotel check_in at {arrival_time} (first item, travel_from_prev: null), then optionally a quick dinner if nearby. "
                    f"No sightseeing, no place visits on Day 1 — the user is arriving very late.\n"
                )
            elif arr_hour >= 14:
                skip, first = "breakfast and lunch", "dinner"
                arrival_block = (
                    f"**Day 1 arrival at {arrival_time} — overrides Rule 4b for Day 1 only:** "
                    f"Do NOT add {skip} to the Day 1 timeline or `meal_options`. "
                    f"First meal on Day 1 is {first}. "
                    f"First timeline item: hotel check_in at {arrival_time} (travel_from_prev: null). "
                    f"No activities scheduled before {arrival_time}.\n"
                )
            else:
                skip, first = "breakfast", "lunch"
                arrival_block = (
                    f"**Day 1 arrival at {arrival_time} — overrides Rule 4b for Day 1 only:** "
                    f"Do NOT add {skip} to the Day 1 timeline or `meal_options`. "
                    f"First meal on Day 1 is {first}. "
                    f"First timeline item: hotel check_in at {arrival_time} (travel_from_prev: null). "
                    f"No activities scheduled before {arrival_time}.\n"
                )
        elif arr_hour is not None and arr_hour < 7:
            arrival_block = (
                f"**Day 1 very early arrival at {arrival_time}:** "
                f"Bag-drop at hotel on arrival (check_in, note as bag-drop). "
                f"REST until ~8:00–9:00 AM — no activity or sightseeing before 8 AM. "
                f"Breakfast at ~8:30 AM (timeline item). Proper check-in at ~10:00–11:00 AM (second check_in item). "
                f"First timeline item: hotel check_in at {arrival_time}, travel_from_prev: null.\n"
            )
        else:
            arrival_block = (
                f"**Day 1 morning arrival at {arrival_time}:** "
                f"Bag-drop at hotel, breakfast nearby, then explore. "
                f"First timeline item: hotel check_in at {arrival_time}, travel_from_prev: null.\n"
            )

    if departure_time and dep_hour is not None:
        latest_co = _checkout_deadline(dep_hour, dep_minute)
        if dep_hour < 9:
            arrival_block += (
                f"**Last day early departure at {departure_time} — hard rules:** "
                f"Do NOT add any place visits, meals, or activities on the last day. "
                f"The ONLY item on the last day is hotel check_out — it MUST be by {latest_co}. "
                f"Note on the check_out item: 'Early departure — grab something at the airport/station.'\n"
            )
        elif dep_hour < 12:
            arrival_block += (
                f"**Last day departure at {departure_time} — hard rules:** "
                f"Skip lunch and dinner from the last-day timeline. "
                f"Hotel check_out MUST be by {latest_co}. "
                f"The check_out object is the LAST element in the last day's `timeline` array. "
                f"After writing check_out, close the timeline array immediately: `...check_out item` ]. "
                f"Do NOT write any place, meal, or activity JSON object after check_out. "
                f"Last day order: breakfast → at most 1 short activity → check_out (array ends). "
                f"Note on the check_out item: 'Heading home after a quick morning.'\n"
            )
        elif dep_hour < 15:
            arrival_block += (
                f"**Last day departure at {departure_time} — hard rules:** "
                f"Skip dinner from the last-day timeline. "
                f"Hotel check_out MUST be by {latest_co}. "
                f"The check_out object is the LAST element in the last day's `timeline` array. "
                f"After writing check_out, close the timeline array immediately: `...check_out item` ]. "
                f"Do NOT write any place, meal, or activity JSON object after check_out. "
                f"Note on the check_out item: 'Heading home — grab a quick bite near the station/airport.'\n"
            )
        else:
            arrival_block += (
                f"**Last day departure at {departure_time} — hard rules:** "
                f"Hotel check_out MUST be by {latest_co}. Do NOT schedule any activity or meal ending after {latest_co}. "
                f"hotel check_out is the LAST hotel item — nothing scheduled after the departure buffer. "
                f"Include dinner only if it genuinely ends before {latest_co}. "
                f"Note on last timeline item if dinner is skipped: 'Heading home — grab a quick bite near the station/airport.'\n"
            )
    elif departure_time:
        arrival_block += (
            f"**Last day departure at {departure_time}:** "
            f"Add hotel check_out, don't over-schedule the last day.\n"
        )

    caps = MEAL_COST_CAPS.get(hotel_pref, (200, 350, 400))
    b_cap, l_cap, d_cap = caps
    sc = rest_slot_counts or {}
    n_b, n_l, n_d = sc.get('breakfast', 0), sc.get('lunch', 0), sc.get('dinner', 0)
    rest_coverage = (
        f"Dataset coverage for {hotel_pref} tier: "
        f"breakfast-eligible: {n_b}  |  lunch-eligible: {n_l}  |  dinner-eligible: {n_d}\n"
        f"- If a slot has fewer than 3 dataset options, supplement with your own knowledge "
        f"but still keep costs within ₹{b_cap}/₹{l_cap}/₹{d_cap} per person "
        f"(breakfast/lunch/dinner) for the {hotel_pref} tier."
    )

    food_type = user_preferences.get('food_type', '').strip()
    accom_pref = user_preferences.get('accommodation_preference', '').strip()

    hard_constraints = []
    if food_type:
        hard_constraints.append(
            f"- DIETARY RESTRICTION: \"{food_type}\". You MUST ONLY recommend restaurants that serve {food_type} food. "
            f"Never suggest a restaurant that does not cater to this dietary requirement. "
            f"This applies to every meal in every day's timeline and meal_options."
        )
    if accom_pref:
        hard_constraints.append(
            f"- ACCOMMODATION TYPE: \"{accom_pref}\". You MUST suggest {accom_pref}-type properties only. "
            f"The Hotels Dataset is a reference — judge each listed option on quality and fit. "
            f"If the dataset is thin or empty, use your knowledge of real well-reviewed {accom_pref} stays "
            f"at {hotel_pref} budget for this destination. Never substitute a different property type."
        )
    hard_constraints.append(
        f"- ACTIVITIES: The user selected these activities: {', '.join(user_preferences['preferred_activities'])}. "
        f"You MUST include at least one of these activity types in each day's place visits. "
        f"Do not fill days with activities unrelated to the user's selections."
    )
    hard_constraints.append(
        f"- TRIP TYPE: This is a \"{user_preferences['trip_type']}\" trip. All place suggestions, restaurant ambience, "
        f"and hotel selection MUST align with this trip type. Do not suggest venues that contradict the trip type."
    )
    hard_constraints.append(
        f"- GROUP TYPE: The group is \"{user_preferences['travel_group_type']}\". Tailor all suggestions — venues, "
        f"activities, accommodation — to be appropriate and enjoyable for this group type."
    )
    hard_constraints_block = "\n".join(hard_constraints)

    user_content = f"""## Request context
- Trip duration: {trip_duration} days — output exactly {trip_duration} day objects (day 1 through day {trip_duration}). `suggested_places` are hints — fit them within the fixed days. Do NOT extend the day count.
- **DESTINATION LOCK — CRITICAL:** The destination is "{user_preferences['places_of_interest']}". Your `city` and `state` fields in the JSON output MUST reflect this destination. Do NOT substitute, replace, or default to any other city or destination — even if the Recommended Places dataset below is empty. If the dataset is empty, use your own knowledge about "{user_preferences['places_of_interest']}" exclusively.
{arrival_block}
## MANDATORY USER CONSTRAINTS — follow these in every day, no exceptions:
{hard_constraints_block}

Generate a COMPLETE {trip_duration}-day travel itinerary with ALL {trip_duration} days fully populated. Do not stop after day 1.

### User Preferences
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Places of interest: {user_preferences['places_of_interest']}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}{f"{chr(10)}- Dietary restriction (food type): {food_type}" if food_type else ""}
- Starting location: {user_preferences['user_location']}
- Travel month: {user_preferences['current_month']}
- Trip type: {user_preferences['trip_type']}
- Trip duration: {trip_duration} days
- Start date: {user_preferences.get('start_date', 'not specified')}
- Suggested places: {user_preferences['suggested_places']}
- Budget: {user_preferences['budget']}
- Hotel preference tier: {hotel_pref}{f"{chr(10)}- Accommodation type preference: {accom_pref}" if accom_pref else ""}

### Recommended Places (use first; supplement with your knowledge)
{top_places.to_csv(index=False, na_rep='null')}

### Restaurants Dataset
{top_restaurants.to_csv(index=False, na_rep='null')}

{rest_coverage}

### Hotels Dataset
{top_hotels.to_csv(index=False, na_rep='null')}

### Rules

0. **DESTINATION IS FIXED:** Generate the itinerary for "{user_preferences['places_of_interest']}" and only that destination. Never change, substitute, or default to another city. The `city` and `state` in the output JSON must reflect "{user_preferences['places_of_interest']}" — not Goa, not Mumbai, not any other city.
1. The `itinerary` array must contain exactly {trip_duration} day objects (day 1 through {trip_duration}).
2. Include all `suggested_places` within {trip_duration} days.
3. Fill days using the Recommended Places dataset first, then your own knowledge for nearby attractions.
3b. If destination cannot genuinely fill {trip_duration} days, output all days anyway and set `notes` with a friendly advisory.
3c. **Distribute places evenly across days.**
3d. For every `place` item set `suitable_trip_types` (array, subset of: leisure, honeymoon, adventure, spiritual, pilgrimage, family, workation, wellness, backpacking, weekend_getaway) and `suitable_group_types` (array, subset of: couples, friends, family_with_children, family_without_children, solo) based on the place's character. Use your knowledge — e.g. a beach → ["honeymoon","leisure","weekend_getaway"], a temple → ["spiritual","pilgrimage"], a zoo → ["family"]. Do not front-load all top attractions on Day 1 and leave later days thin. Aim for a similar number of place visits per day unless arrival/departure constraints force otherwise.
4. Each day: as many geographically close places as fit (minimum 2), 3 meal slots, hotel check_in/check_out where appropriate. All in the `timeline` array — NO separate `places_to_visit` or `meals` dict.
4b. Meal ordering — strictly enforce every day: Breakfast → 1+ place visits → Lunch → 1+ place visits → Dinner. Never place lunch immediately after breakfast or dinner immediately after lunch — always at least 1 place visit between consecutive meals.
4b-i. Day 1 early/morning arrival EXCEPTION: if arrival_time is before 10:00 AM, breakfast MUST appear as a `type: "meal", slot: "breakfast"` timeline item. For very early arrivals (before 7 AM), breakfast is scheduled at ~8:30 AM (after the traveller has rested) — NOT at 4–5 AM. Place it before the first place visit and before the proper check-in. Do NOT put it only in meal_options.
4c. Early morning: if destination is known for early morning experiences (sunrise points, ghats, dawn markets), add a pre-breakfast place visit (~5:00–6:30 AM). Breakfast follows at ~7:30–8:00 AM.
4d. Late night: if destination is famous for night experiences (night markets, beach walks, nightlife), add a post-dinner place visit after dinner.
5. Do not suggest a place on a day it is regularly closed (use start_date for day-of-week).
6. Hotels: grouped by city (one group per city for multi-city trips, with correct from_day/to_day). Each group: `selected` (best pick) + `alternatives` (1–2 options of the SAME accommodation type — all 3 must be the same property type as the user requested). Pick from Hotels Dataset; use own knowledge if dataset is thin.
7. Keep travel flow linear — no A→B→A routing. Order places by opening time; sunset/night spots last.
8. Set `price_estimated_range` to the actual total per-head estimate for the trip; use the user's budget range if it fits, otherwise show the real range.
9. Include `similar_places` (2–3 alternative destinations).
10. No placeholder text ("TBD", "N/A"). Only JSON.
11. The `itinerary` array MUST have exactly {trip_duration} fully populated day objects. Day 2 through day {trip_duration} follow the exact same structure as day 1 — do not stop early.

"""

    checkout_verify = ""
    if departure_time and dep_hour is not None and dep_hour < 15:
        latest_co = _checkout_deadline(dep_hour, dep_minute)
        checkout_verify = (
            f" Also verify: does day {trip_duration}'s timeline array end with the hotel check_out item "
            f"(by {latest_co}) and nothing after it? If not, remove all items after check_out."
        )

    user_content += (
        f"\n\nBefore you output the JSON, silently verify: does your `itinerary` array "
        f"have exactly {trip_duration} day objects? "
        f"If it has fewer, add the missing days before outputting. "
        f"A response with fewer than {trip_duration} days is incomplete and unusable."
        f"{checkout_verify}"
    )

    return [
        {"role": "system", "content": _SYSTEM[hotel_pref]},
        {"role": "user",   "content": user_content},
    ]
