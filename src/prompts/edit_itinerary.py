import re as _re
from prompts.constants import BUDGET_TIER_MAP, MEAL_COST_CAPS, MEAL_TIER_TABLE


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
    total = dep_hour * 60 + dep_minute - buffer_mins
    if total < 0:
        total = 0
    h, mn = divmod(total, 60)
    suffix = "AM" if h < 12 else "PM"
    display_h = h if 1 <= h <= 12 else (12 if h == 0 else h - 12)
    return f"{display_h}:{mn:02d} {suffix}"


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
First try to fit all must-include places within the requested trip duration. If they don't fit, extend by the minimum extra days needed and update `total_days` with a friendly `notes` message.

## Restaurant Dataset Notes
- Prefer restaurants with Votes > 100 (more reviews = more reliable rating).
- Use the `suitable_slots` column to assign each restaurant to the correct meal slot.
- Cost column is 'cost for two' in INR — a Cost of 400 means ₹200 per person.
- If a slot has fewer than 3 dataset options, supplement with your own knowledge but still keep costs within the {tier} tier caps.

## Rules
1. Every must-include place must appear in `timeline` (as `type:"place"`) somewhere across the days.
2. Group geographically close must-include places on the same day.
3. Extend trip if must-include places don't fit; update `total_days` and set `notes`.
4. Fill remaining slots with dataset places or your knowledge (min 2 places/day).
4b. For every `place` item set `suitable_trip_types` (subset of: leisure, honeymoon, adventure, spiritual, pilgrimage, family, workation, wellness, backpacking, weekend_getaway) and `suitable_group_types` (subset of: couples, friends, family_with_children, family_without_children, solo) based on the place's character.
5. Each day: places + meals (3 slots) + hotel events all in `timeline`. Also include `meal_options`.
7. Day 1 starts with hotel check_in (or bag-drop). Last day ends with hotel check_out before departure activities.
8. No repeated places, hotels, or restaurants.
9. Linear travel flow — no A→B→A.
10. No placeholder text.

## Output Format

Exact values for `total_days`, `from_day`, `to_day` come from the trip duration in the user message (use that number, not the N below).

{{
  "itinerary": [
    {{
      "day": 1,
      "theme": "Short day theme",
      "day_summary": "One-line summary",
      "timeline": [
        {{"type": "hotel", "event": "check_in", "name": "Hotel Name", "suggested_time": "11:00 AM", "duration": "15 mins", "travel_from_prev": null, "note": "Check in and freshen up"}},
        {{"type": "place", "name": "Must-include Place", "location": "City, State", "reason": "Why it fits", "activities": ["Activity"], "rating": "4.5", "opening_hours": "9:00 AM – 6:00 PM", "duration": "2 hours", "suggested_time": "11:30 AM", "travel_from_prev": {{"duration_mins": 20, "mode": "cab", "note": "~20 min cab"}}, "suitable_trip_types": ["leisure","honeymoon"], "suitable_group_types": ["couples","friends"]}},
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
      "to_day": N,
      "selected": {{"name": "Best Hotel", "type": "{tier}", "price_range": "₹X–₹Y/night", "rating": "4.3", "location": "City, State", "reason": "Best match for your tier", "link": "https://..."}},
      "alternatives": [
        {{"name": "Alt Option 1", "type": "{tier}", "price_range": "₹X–₹Y/night", "rating": "4.0", "location": "City, State", "reason": "Good alternative", "link": "https://..."}},
        {{"name": "Alt Option 2", "type": "{tier}", "price_range": "₹X–₹Y/night", "rating": "3.9", "location": "City, State", "reason": "Another option", "link": "https://..."}}
      ]
    }}
  ],
  "name": "Destination Name",
  "description": "2–3 line description",
  "total_days": N,
  "notes": "",
  "price_estimated_range": "₹X,XXX–₹X,XXX per person",
  "state": "State Name",
  "city": "City Name",
  "similar_places": [
    {{"placename": "Alternative Destination", "description": "Why it fits", "state": "State Name", "price_estimated_range": "₹X,XXX–₹X,XXX per person"}}
  ]
}}"""


_SYSTEM = {t: _build_system(t) for t in ('budget', 'mid', 'high', 'luxury')}


def generate_edit_itinerary_prompt(user_preferences, top_places, top_restaurants, top_hotels, must_include_places, rest_slot_counts=None):
    must_include_block = "\n".join(f"  - {name}" for name in must_include_places) or "  (none specified)"
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
                arrival_block += (
                    f"**Day 1 late-night arrival at {arrival_time} — overrides Rule 4b for Day 1 only:** "
                    f"Do NOT add breakfast, lunch, or any place/activity to Day 1 timeline. "
                    f"Omit breakfast and lunch from `meal_options` for Day 1 — only 'dinner' key allowed. "
                    f"Day 1 only: hotel check_in at {arrival_time} (first item, travel_from_prev: null), then optionally a quick dinner. "
                    f"No sightseeing or place visits on Day 1.\n"
                )
            elif arr_hour >= 14:
                skip, first = "breakfast and lunch", "dinner"
                arrival_block += (
                    f"**Day 1 arrival at {arrival_time} — overrides Rule 4b for Day 1 only:** "
                    f"Do NOT add {skip} to the Day 1 timeline or `meal_options`. "
                    f"First meal on Day 1 is {first}. "
                    f"First timeline item: hotel check_in at {arrival_time} (travel_from_prev: null). "
                    f"No activities scheduled before {arrival_time}.\n"
                )
            else:
                skip, first = "breakfast", "lunch"
                arrival_block += (
                    f"**Day 1 arrival at {arrival_time} — overrides Rule 4b for Day 1 only:** "
                    f"Do NOT add {skip} to the Day 1 timeline or `meal_options`. "
                    f"First meal on Day 1 is {first}. "
                    f"First timeline item: hotel check_in at {arrival_time} (travel_from_prev: null). "
                    f"No activities scheduled before {arrival_time}.\n"
                )
        elif arr_hour is not None and arr_hour < 7:
            arrival_block += (
                f"**Day 1 very early arrival at {arrival_time}:** "
                f"Bag-drop at hotel on arrival (check_in, note as bag-drop). REST until ~8:00–9:00 AM. "
                f"Breakfast at ~8:30 AM (timeline item). Proper check-in at ~10:00–11:00 AM (second check_in). "
                f"First timeline item: hotel check_in at {arrival_time}, travel_from_prev: null.\n"
            )
        else:
            arrival_block += (
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
    arrival_section = ("## Arrival / Departure\n" + arrival_block) if arrival_block else ""

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
        f"- ACTIVITIES: The user selected: {', '.join(user_preferences['preferred_activities'])}. "
        f"Include at least one of these activity types in each day's place visits."
    )
    hard_constraints.append(
        f"- TRIP TYPE: This is a \"{user_preferences['trip_type']}\" trip. All suggestions MUST align with this trip type."
    )
    hard_constraints.append(
        f"- GROUP TYPE: The group is \"{user_preferences['travel_group_type']}\". Tailor all suggestions to be appropriate for this group type."
    )
    hard_constraints_block = "\n".join(hard_constraints)

    user_content = f"""## Request context
- Budget tier: {hotel_pref}
- Trip duration: {trip_duration} days (may extend if must-include places don't fit)
{arrival_section}
## MANDATORY USER CONSTRAINTS — follow in every day, no exceptions:
{hard_constraints_block}

Rebuild this COMPLETE {trip_duration}-day travel itinerary with ALL {trip_duration} days fully populated. Every must-include place MUST appear. Do not stop after day 1.

## User Preferences
- Places of interest: {user_preferences['places_of_interest']}
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}{f"{chr(10)}- Dietary restriction (food type): {food_type}" if food_type else ""}
- Starting location: {user_preferences['user_location']}
- Travel month: {user_preferences['current_month']}
- Trip type: {user_preferences['trip_type']}
- Trip duration: {trip_duration} days (may be extended)
- Start date: {user_preferences.get('start_date', 'not specified')}
- Budget: {user_preferences['budget']}
- Hotel preference tier: {hotel_pref}{f"{chr(10)}- Accommodation type preference: {accom_pref}" if accom_pref else ""}

## Places that MUST be included (hard requirement)
{must_include_block}

## Recommended Places
{top_places.to_csv(index=False, na_rep='null')}

## Restaurants Dataset
{top_restaurants.to_csv(index=False, na_rep='null')}

{rest_coverage}

## Hotels Dataset
{top_hotels.to_csv(index=False, na_rep='null')}

## Rules (dynamic)
6. Hotels grouped by city: `selected` (best pick) + `alternatives` (1–2 options of the SAME accommodation type — all 3 must be the same property type as the user requested). Multi-city = one group per city. Use your knowledge if dataset is thin.
11. The `itinerary` array MUST have exactly {trip_duration} fully populated day objects. Do not stop early.
"""
    return [
        {"role": "system", "content": _SYSTEM[hotel_pref]},
        {"role": "user",   "content": user_content},
    ]
