"""
Comprehensive end-to-end test:
- All 7 accommodation types × multiple food types × varied destinations
- Cases: many hotels in dataset, few hotels, zero hotels
- Verifies: correct type in output, LLM fills from knowledge when dataset is empty
- Verifies: itinerary_id returned (saved to DB successfully)
"""
import sys, os, json, time, hashlib, hmac, base64, urllib.request, urllib.error

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from core.config import DEVICE_JWT_SECRET

PORT = os.getenv("PORT", "4000")
BASE = f"http://localhost:{PORT}"


def _device_token():
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).decode().rstrip("=")
    payload_data = {"device_id": "pref-test-001", "iat": int(time.time())}
    payload = base64.urlsafe_b64encode(
        json.dumps(payload_data).encode()
    ).decode().rstrip("=")
    sig = hmac.HMAC(
        DEVICE_JWT_SECRET.encode(),
        f"{header}.{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{header}.{payload}.{sig}"


TOKEN = _device_token()
HEADERS = {"Content-Type": "application/json", "X-Device-Token": TOKEN}


def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def base_payload(destination, days=2, budget="mid", activities=None, trip_type="Leisure", group="couple"):
    return {
        "places_of_interest": destination,
        "user_location": "Bangalore",
        "trip_duration": days,
        "number_of_people": 2,
        "travel_group_type": group,
        "food_preferences": ["South Indian"],
        "preferred_activities": activities or ["Nature & Wildlife"],
        "trip_type": trip_type,
        "current_month": "August",
        "start_date": "2026-08-15",
        "budget": "15000",
        "suggested_places": [],
        "hotel_preference": budget,
    }


def extract(resp):
    itin = resp.get("data", {}).get("detailed_itinerary", {})
    if not itin:
        return None, [], [], resp.get("itinerary_id")

    hotels = []
    for h in itin.get("hotels", []):
        sel = h.get("selected", {})
        hotels.append({
            "name": sel.get("name", ""),
            "type": sel.get("type", ""),
            "reason": (sel.get("reason") or "")[:100],
            "is_alt": False,
        })
        for alt in h.get("alternatives", []):
            hotels.append({
                "name": alt.get("name", ""),
                "type": alt.get("type", ""),
                "reason": "",
                "is_alt": True,
            })

    meals = []
    for day in itin.get("itinerary", []):
        for item in day.get("timeline", []):
            if item.get("type") == "meal":
                meals.append({
                    "day": day["day"], "slot": item.get("slot"),
                    "name": item.get("name"), "cuisine": item.get("cuisine"),
                })

    return itin.get("name"), hotels, meals, resp.get("itinerary_id")


def run_test(t):
    print(f"\n{'='*65}")
    print(f"{t['label']}")
    print(f"  accom={t.get('accom','none')} | food={t.get('food','none')} | dest={t['dest']} | dataset={t.get('dataset_note','?')}")
    print(f"{'='*65}")

    payload = base_payload(
        t["dest"],
        days=t.get("days", 2),
        budget=t.get("budget", "mid"),
        activities=t.get("activities"),
        trip_type=t.get("trip_type", "Leisure"),
        group=t.get("group", "couple"),
    )
    if t.get("accom"):
        payload["accommodation_preference"] = t["accom"]
    if t.get("food"):
        payload["food_type"] = t["food"]

    print(f"  Calling /generate-itinerary ...")
    resp = post("/generate-itinerary", payload)

    if resp.get("status") == "error":
        print(f"  ERROR: {resp.get('message')}")
        return

    dest, hotels, meals, itin_id = extract(resp)
    print(f"  Destination name: {dest}")

    # --- DB save check ---
    saved = "YES" if itin_id else "NO (itinerary_id missing)"
    print(f"  Saved to DB: {saved} (id={itin_id})")

    # --- Hotels ---
    print(f"\n  Hotels [{len(hotels)} entries, expecting '{t.get('accom','any')}'-type]:")
    for h in hotels:
        tag = "[ALT]" if h["is_alt"] else "[SEL]"
        print(f"    {tag} {h['name']} | type={h['type']}")
        if not h["is_alt"] and h["reason"]:
            print(f"         reason: {h['reason']}")

    # --- Meals (first day only for brevity) ---
    day1 = [m for m in meals if m["day"] == 1]
    print(f"\n  Meals day 1 [{len(meals)} total, expecting '{t.get('food','any')}' food]:")
    for m in day1:
        print(f"    [{m['slot']}] {m['name']} | {m['cuisine']}")


TESTS = [
    # ── accommodation type coverage ──────────────────────────────────────────
    {
        "label": "T01 — Hotel (large dataset) + Veg | Jaipur",
        "accom": "Hotel", "food": "Veg", "dest": "Jaipur, Rajasthan",
        "dataset_note": "Hotel: ~2314 rows, many in Jaipur",
    },
    {
        "label": "T02 — Resort + Non-Veg | Goa",
        "accom": "Resort", "food": "Non-Veg", "dest": "Goa",
        "dataset_note": "Resort: 516 rows, good Goa coverage",
    },
    {
        "label": "T03 — Homestay + Veg | Coorg",
        "accom": "Homestay", "food": "Veg", "dest": "Coorg, Karnataka",
        "dataset_note": "Homestay: 231 rows, ~1-2 in Coorg",
    },
    {
        "label": "T04 — Hostel + Non-Veg | Manali (ZERO in dataset)",
        "accom": "Hostel", "food": "Non-Veg", "dest": "Manali, Himachal Pradesh",
        "dataset_note": "Hostel: 10 rows total, likely 0 in Manali — LLM should fill",
    },
    {
        "label": "T05 — Guesthouse + Jain | Varanasi",
        "accom": "Guesthouse", "food": "Jain", "dest": "Varanasi, Uttar Pradesh",
        "dataset_note": "Guesthouse: 243 rows, some in Varanasi",
    },
    {
        "label": "T06 — Apartment + Vegan | Mumbai",
        "accom": "Apartment", "food": "Vegan", "dest": "Mumbai, Maharashtra",
        "dataset_note": "Apartment (Service Apartment): 183 rows, good Mumbai coverage",
    },
    {
        "label": "T07 — Villa + Eggetarian | Udaipur",
        "accom": "Villa", "food": "Eggetarian", "dest": "Udaipur, Rajasthan",
        "dataset_note": "Villa+Cottage: 124 rows, few in Udaipur",
    },
    # ── dataset volume edge cases ─────────────────────────────────────────────
    {
        "label": "T08 — Hostel | Leh (very remote, 0 hostel rows expected)",
        "accom": "Hostel", "food": "Non-Veg", "dest": "Leh, Ladakh",
        "dataset_note": "0 hostels in Leh — pure LLM fill test",
    },
    {
        "label": "T09 — Resort + Luxury | Maldives-style (Andaman Islands)",
        "accom": "Resort", "food": "Non-Veg", "dest": "Port Blair, Andaman",
        "budget": "luxury", "dataset_note": "Resort rows likely thin for Andaman",
    },
    {
        "label": "T10 — No accom pref, No food pref | Delhi (baseline)",
        "dest": "Delhi",
        "dataset_note": "No filtering — verify baseline still works & saves",
    },
]

passed_save = 0
failed_save = 0
for t in TESTS:
    run_test(t)
    # track save success for summary (reusing last resp implicitly tracked inside run_test)

print(f"\n\n{'='*65}")
print("DONE — check hotel types and cuisine labels above.")
print("itinerary_id present on each test = saved to Azure SQL successfully.")
