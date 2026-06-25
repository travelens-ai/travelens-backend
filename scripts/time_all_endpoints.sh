#!/usr/bin/env bash
# Run every Travelens API endpoint and print HTTP status + elapsed time.
# Usage: ./scripts/time_all_endpoints.sh [base_url]
# Requires: curl

BASE="${1:-http://localhost:4000}"
BODY_FILE="/tmp/travelens_curl_body.txt"

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

header() { echo -e "\n${CYAN}=== $1 ===${RESET}"; printf "%-55s  %-6s  %s\n" "Endpoint" "Status" "Time"; printf '%0.s-' {1..75}; echo; }

run() {
  local label="$1"; shift
  local result http_code time_total
  result=$(curl -s -o "$BODY_FILE" -w "%{http_code} %{time_total}" --max-time 35 "$@" 2>/dev/null)
  http_code=$(echo "$result" | awk '{print $1}')
  time_total=$(echo "$result" | awk '{printf "%.3f", $2}')
  local color=$GREEN
  [[ "$http_code" =~ ^4 ]] && color=$YELLOW
  [[ "$http_code" =~ ^5 ]] && color=$RED
  [[ -z "$http_code" ]] && { http_code="TIMEOUT"; color=$RED; }
  printf "%-55s  ${color}%-6s${RESET}  %ss\n" "$label" "$http_code" "$time_total"
}

echo -e "${CYAN}Travelens Endpoint Timing Report${RESET}"
echo "Base URL: $BASE"
echo "Time: $(date)"

# ── System ─────────────────────────────────────────────────────────────────────
header "System"
run "GET /health" \
  "$BASE/health"

# ── Config ─────────────────────────────────────────────────────────────────────
header "Config"
run "GET /configs" \
  "$BASE/configs"

# ── Auth ───────────────────────────────────────────────────────────────────────
header "Auth"
run "POST /send-otp" \
  -X POST "$BASE/send-otp" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","purpose":"signup"}'

run "POST /verify-otp (invalid)" \
  -X POST "$BASE/verify-otp" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","otp":"000000","purpose":"signup"}'

run "POST /signup (existing user attempt)" \
  -X POST "$BASE/signup" \
  -H "Content-Type: application/json" \
  -d '{"name":"Test User","email":"test@example.com","password":"Test1234!"}'

run "POST /login (invalid creds)" \
  -X POST "$BASE/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"WrongPass99!"}'

run "POST /forgot-password" \
  -X POST "$BASE/forgot-password" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com"}'

run "POST /reset-password (invalid OTP)" \
  -X POST "$BASE/reset-password" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","otp":"000000","new_password":"Test1234!"}'

run "POST /google-login (invalid token)" \
  -X POST "$BASE/google-login" \
  -H "Content-Type: application/json" \
  -d '{"id_token":"invalid_token"}'

run "PUT /update (no auth)" \
  -X PUT "$BASE/update" \
  -H "Content-Type: application/json" \
  -d '{"name":"Updated Name"}'

# ── Places ─────────────────────────────────────────────────────────────────────
header "Places"
run "GET /places?type=popular" \
  "$BASE/places?type=popular"

run "GET /places?type=trending" \
  "$BASE/places?type=trending"

run "GET /places?type=weekend&lat=28.6&long=77.2" \
  "$BASE/places?type=weekend&lat=28.6&long=77.2"

run "GET /places?type=nearby&lat=28.6&long=77.2" \
  "$BASE/places?type=nearby&lat=28.6&long=77.2"

run "GET /places?keyword=delhi" \
  "$BASE/places?keyword=delhi"

# ── Search ─────────────────────────────────────────────────────────────────────
header "Search"
run "GET /search?q=del" \
  "$BASE/search?q=del"

run "GET /search?q=mum&limit=5" \
  "$BASE/search?q=mum&limit=5"

# ── Weather ────────────────────────────────────────────────────────────────────
header "Weather"
run "GET /weather?city=Delhi&start_date=2026-06-25&days=3" \
  "$BASE/weather?city=Delhi&start_date=2026-06-25&days=3"

run "GET /weather?city=Mumbai&start_date=2026-06-25&days=7" \
  "$BASE/weather?city=Mumbai&start_date=2026-06-25&days=7"

# ── User ───────────────────────────────────────────────────────────────────────
header "User"
run "GET /favorite?user_id=1" \
  "$BASE/favorite?user_id=1"

run "POST /favorite" \
  -X POST "$BASE/favorite" \
  -H "Content-Type: application/json" \
  -d '{"itinerary_id":"test-itin-001","user_id":"1"}'

run "DELETE /favorite" \
  -X DELETE "$BASE/favorite" \
  -H "Content-Type: application/json" \
  -d '{"itinerary_id":"test-itin-001","user_id":"1"}'

run "GET /history?user_id=1" \
  "$BASE/history?user_id=1"

run "POST /history" \
  -X POST "$BASE/history" \
  -H "Content-Type: application/json" \
  -d '{"itinerary_id":"test-itin-001","user_id":"1"}'

# ── Popular Destinations ───────────────────────────────────────────────────────
header "Popular Destinations"
run "GET /popular-destination" \
  "$BASE/popular-destination"

# ── Itinerary (AI - slow) ──────────────────────────────────────────────────────
header "Itinerary (AI — may be slow)"
ITIN_BODY='{"places_of_interest":["Taj Mahal","Agra Fort"],"user_location":"Delhi","trip_duration":3,"number_of_people":2,"travel_group_type":"couple","food_preferences":["vegetarian"],"preferred_activities":["sightseeing"],"trip_type":"leisure","current_month":"June","start_date":"2026-06-28","budget":10000}'

run "POST /generate-itinerary" \
  -X POST "$BASE/generate-itinerary" \
  -H "Content-Type: application/json" \
  -d "$ITIN_BODY"

run "POST /generate-itinerary/stream (first chunk)" \
  -X POST "$BASE/generate-itinerary/stream" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  --max-time 30 \
  -d "$ITIN_BODY"

run "POST /edit-itinerary" \
  -X POST "$BASE/edit-itinerary" \
  -H "Content-Type: application/json" \
  -d '{"itinerary_id":"test-itin-001","places_of_interest":["Taj Mahal"],"user_location":"Delhi","trip_duration":2,"number_of_people":2,"travel_group_type":"couple","food_preferences":["vegetarian"],"preferred_activities":["sightseeing"],"trip_type":"leisure","current_month":"June","start_date":"2026-06-28","budget":8000}'

# ── Images (AI - slow) ─────────────────────────────────────────────────────────
header "Images (AI — may be slow)"
run "POST /generate-images" \
  -X POST "$BASE/generate-images" \
  -H "Content-Type: application/json" \
  -d '{"places":["Taj Mahal","Agra Fort"]}'

echo -e "\n${CYAN}Done.${RESET}"
rm -f "$BODY_FILE"
