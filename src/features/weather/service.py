from datetime import date, datetime, timedelta
import threading

import requests as http_requests

_weather_cache = {}
_weather_cache_lock = threading.Lock()
CACHE_TTL = 7200  # 2 hours

# WMO weather code → (label, emoji)
_WMO = {
    0:  ("Clear Sky",     "☀️"),
    1:  ("Mainly Clear",  "🌤"),
    2:  ("Partly Cloudy", "⛅"),
    3:  ("Overcast",      "☁️"),
    45: ("Foggy",         "🌫"),
    48: ("Foggy",         "🌫"),
    51: ("Drizzle",       "🌦"),
    53: ("Drizzle",       "🌦"),
    55: ("Drizzle",       "🌦"),
    61: ("Rain",          "🌧"),
    63: ("Rain",          "🌧"),
    65: ("Heavy Rain",    "🌧"),
    71: ("Snow",          "❄️"),
    73: ("Snow",          "❄️"),
    75: ("Heavy Snow",    "❄️"),
    80: ("Showers",       "🌦"),
    81: ("Showers",       "🌦"),
    82: ("Heavy Showers", "🌧"),
    95: ("Thunderstorm",  "⛈"),
    96: ("Thunderstorm",  "⛈"),
    99: ("Thunderstorm",  "⛈"),
}


def _wmo_label(code):
    return _WMO.get(code, ("Unknown", "🌡"))


def _get_coords(city):
    from features.places.service import _city_coords_cache
    return _city_coords_cache.get(city)


def _cache_get(key):
    with _weather_cache_lock:
        entry = _weather_cache.get(key)
    if not entry:
        return None
    ts, result = entry
    if (datetime.now().timestamp() - ts) < CACHE_TTL:
        return result
    with _weather_cache_lock:
        _weather_cache.pop(key, None)
    return None


def _cache_set(key, result):
    with _weather_cache_lock:
        _weather_cache[key] = (datetime.now().timestamp(), result)


def _fetch_forecast(lat, lon, start_date, end_date):
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode"
        "&timezone=Asia/Kolkata"
        f"&start_date={start_date}&end_date={end_date}"
    )
    resp = http_requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json().get("daily", {})

    days = []
    for i, d in enumerate(data.get("time", [])):
        code = data["weathercode"][i] if data.get("weathercode") else 0
        label, emoji = _wmo_label(int(code))
        days.append({
            "date": d,
            "condition": label,
            "emoji": emoji,
            "temp_max": round(data["temperature_2m_max"][i]) if data.get("temperature_2m_max") else None,
            "temp_min": round(data["temperature_2m_min"][i]) if data.get("temperature_2m_min") else None,
            "rain_chance": data["precipitation_probability_max"][i] if data.get("precipitation_probability_max") else 0,
        })
    return days


def _fetch_climate_normals(lat, lon, start_date, num_days):
    import calendar
    # ERA5 historical data — fetch that calendar month from 2022 as a reference year
    trip_month = start_date.month
    ref_year = 2022
    last_day = calendar.monthrange(ref_year, trip_month)[1]
    ref_start = f"{ref_year}-{trip_month:02d}-01"
    ref_end = f"{ref_year}-{trip_month:02d}-{last_day:02d}"

    url = (
        "https://climate-api.open-meteo.com/v1/climate"
        f"?latitude={lat}&longitude={lon}"
        f"&start_date={ref_start}&end_date={ref_end}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
    )
    resp = http_requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("daily", {})

    def _mean(lst):
        vals = [v for v in lst if v is not None]
        return round(sum(vals) / len(vals)) if vals else None

    temp_max = _mean(data.get("temperature_2m_max", []))
    temp_min = _mean(data.get("temperature_2m_min", []))
    precip_vals = [v for v in data.get("precipitation_sum", []) if v is not None]
    precip = sum(precip_vals) if precip_vals else 0  # total monthly precip

    # Derive rain_chance from monthly precip (>100mm = likely rainy)
    rain_chance = min(int((precip / 200) * 100), 90) if precip else 0

    # Pick a condition label based on precip
    if precip > 150:
        label, emoji = "Rain", "🌧"
    elif precip > 50:
        label, emoji = "Showers", "🌦"
    elif temp_max and temp_max < 5:
        label, emoji = "Snow", "❄️"
    else:
        label, emoji = "Partly Cloudy", "⛅"

    days = []
    for i in range(num_days):
        d = start_date + timedelta(days=i)
        days.append({
            "date": d.strftime("%Y-%m-%d"),
            "condition": label,
            "emoji": emoji,
            "temp_max": temp_max,
            "temp_min": temp_min,
            "rain_chance": rain_chance,
        })
    return days


def get_weather_by_coords(lat, lon, start_date_str, days=1):
    """Fetch weather for exact coordinates — skips city name lookup.
    Uses the same 2-hour in-memory cache as get_weather(), keyed by rounded
    lat/lon (~1 km grid) so nearby places on the same day share one API call."""
    cache_key = f"{round(lat, 2)}|{round(lon, 2)}|{start_date_str}|{days}"
    cached = _cache_get(cache_key)
    if cached:
        return cached, None

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        return None, "start_date must be in YYYY-MM-DD format."

    days = max(1, min(days, 16))
    days_away = (start_date - date.today()).days

    try:
        if days_away <= 16:
            end_date = (start_date + timedelta(days=days - 1)).strftime("%Y-%m-%d")
            weather_days = _fetch_forecast(lat, lon, start_date_str, end_date)
            is_forecast = True
        else:
            weather_days = _fetch_climate_normals(lat, lon, start_date, days)
            is_forecast = False
    except Exception as e:
        return None, f"Weather API error: {str(e)}"

    result = {"is_forecast": is_forecast, "weather": weather_days}
    _cache_set(cache_key, result)
    return result, None


def get_weather(city, start_date_str, days):
    city = city.strip().lower()
    cache_key = f"{city}|{start_date_str}|{days}"
    cached = _cache_get(cache_key)
    if cached:
        return cached, None

    coords = _get_coords(city)
    if not coords:
        return None, f"City '{city}' not found. Check spelling or use a major city name."

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        return None, "start_date must be in YYYY-MM-DD format."

    days = max(1, min(days, 16))
    lat, lon = coords
    days_away = (start_date - date.today()).days

    try:
        if days_away <= 16:
            end_date = (start_date + timedelta(days=days - 1)).strftime("%Y-%m-%d")
            weather_days = _fetch_forecast(lat, lon, start_date_str, end_date)
            is_forecast = True
        else:
            weather_days = _fetch_climate_normals(lat, lon, start_date, days)
            is_forecast = False
    except Exception as e:
        return None, f"Weather API error: {str(e)}"

    result = {
        "city": city,
        "is_forecast": is_forecast,
        "weather": weather_days,
    }
    _cache_set(cache_key, result)
    return result, None
