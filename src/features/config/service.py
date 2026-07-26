"""App configuration served to the client.

Drives onboarding pages, screen copy, and the bottom tab bar. The lookup lists
(group types, food preferences, activities) are read from the database so they
can be changed without a deploy.
"""

import os
import pickle
import threading
import time

from core.db import fetch_dicts
from core.ads import get_ads_config, interleave_ads, get_inline_ads_config
from core.images import with_image_urls

# Repo root — where the config snapshot .pkl files live (built by
# scripts/build_config_pkl.py). Reading these avoids a DB round-trip on /configs.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _load_pkl(name):
    """Load a config snapshot .pkl from repo root, or None if missing/unreadable."""
    path = os.path.join(_PROJECT_ROOT, f"{name}.pkl")
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"[config] failed to read {name}.pkl: {e}")
        return None

_config_cache_lock = threading.Lock()
_config_cache: dict | None = None
_config_cache_ts: float = 0.0
_CONFIG_TTL = 24 * 60 * 60  # seconds; data only changes on redeploy (process restart resets cache anyway)

APP_CONFIG = {
    "pages": [
        {
            "type": "GETTING_STARTED",
            "bg": "https://travelens.in/app/assets/welcome-bg.png",
            "title": "Discover India at your own pace",
            "desc": "Travel your way and discover, enjoy and explore India with comfort.",
            "ctaLabel": "Get Started",
            "isFirstTimeUser": False,
        },
        {
            "type": "LAUNCH",
            "bg": "",
            "topImage":"",
            "img": "introduce-yourself",
            "isFirstTimeUser": False,
            "apiResponseKey": "gender",
            "title": "Introduce Yourself",
            "desc": "Fill out the rest of your details so people know a little more about you",
            "ctaLabel": "Next",
            "skipLabel": "Skip",
            "cta": [
                {"size": "card", "img": "male", "label": "Male", "value": "male"},
                {"size": "card", "img": "female", "label": "Female", "value": "female"},
                {
                    "size": "card",
                    "img": "not_specified",
                    "label": "Prefer not to say",
                    "value": "not_specified",
                },
            ],
        },
        {
            "type": "LAUNCH",
            "bg": "",
            "isFirstTimeUser": False,
            "apiResponseKey": "age",
            "title": "How old are you?",
            "desc": "Tell us your age so we can personalize your travel experience",
            "ctaLabel": "Next",
            "skipLabel": "Skip",
            "inputType": "number",
            "placeholder": "Enter your age",
        },
        {
            "type": "LAUNCH",
            "bg": "",
            "isFirstTimeUser": False,
            "apiResponseKey": "interest",
            "title": "Let's select your interests.",
            "desc": "Please select two or more to proceed.",
            "ctaLabel": "Continue",
            "skipLabel": "Skip",
            "cta": [
                {"size": "text", "label": "Aviation"},
                {"size": "text", "label": "Art"},
                {"size": "text", "label": "Cars"},
                {"size": "text", "label": "Baking"},
                {"size": "text", "label": "Botany"},
                {"size": "text", "label": "Crypto"},
                {"size": "text", "label": "Real Estate"},
                {"size": "text", "label": "Technology"},
                {"size": "text", "label": "Fashion"},
                {"size": "text", "label": "Dogs"},
                {"size": "text", "label": "Birds"},
                {"size": "text", "label": "Health care"},
                {"size": "text", "label": "Geography"},
                {"size": "text", "label": "Finance"},
                {"size": "text", "label": "Cats"},
                {"size": "text", "label": "LGBTQ"},
                {"size": "text", "label": "Mental Health"},
                {"size": "text", "label": "Programming"},
                {"size": "text", "label": "Cinema"},
                {"size": "text", "label": "Sports"},
                {"size": "text", "label": "Travel"},
                {"size": "text", "label": "Gaming"},
                {"size": "text", "label": "Photography"},
                {"size": "text", "label": "Design"},
                {"size": "text", "label": "UFO"},
                {"size": "text", "label": "Music"},
            ],
        },
        {
            "type": "HOME",
            "bg": "",
            "texts": {
                "greeting": "Hi",
                "defaultName": "Traveler",
                "tripPlannerTitle": "AI Trip Planner",
                "tripPlannerSubtitle": "Let our AI build a personalized itinerary just for you",
                "tripPlannerCta": "Start Planning",
                "searchPlaceholder": "Search destinations...",
            },
        },
        {
            "type": "AI_TRIP_PLANNER",
            "bg": "",
            "texts": {
                "title": "Plan Your Trip",
                "subtitle": "Tell us where you want to go",
                "ctaLabel": "Next",
            },
        },
        {
            "type": "SEARCH",
            "bg": "",
            "texts": {
                "title": "Search",
                "placeholder": "Where do you want to go?",
                "emptyState": "Start typing to search destinations",
            },
        },
        {
            "type": "HISTORY",
            "bg": "",
            "texts": {
                "title": "Trip History",
                "emptyTitle": "Trip History",
                "emptySubtitle": "Your past itineraries will appear here",
            },
        },
        {
            "type": "FAVORITE",
            "bg": "",
            "texts": {
                "title": "Favorites",
                "emptyTitle": "Favorites",
                "emptySubtitle": "Your favorite destinations will appear here",
            },
        },
        {
            "type": "PROFILE",
            "bg": "",
            "texts": {
                "title": "My Account",
                "headerLink": "Help & Settings",
                "loginTitle": "Login to Travelens",
                "loginSubtitle": "Save your trips, get personalized recommendations and more",
                "loginCta": "Log In",
                "googleCta": "Continue with Google",
                "signupPrompt": "New here?",
                "signupLink": "Create Account",
                "editCta": "Edit Profile",
                "logoutCta": "Log Out",
            },
        },
    ],
    "tabs": [
        {"name": "Home", "icon": "home"},
        {"name": "Plan", "icon": "airplane"},
        {"name": "Favorite", "icon": "heart"},
        {"name": "History", "icon": "time"},
        {"name": "Profile", "icon": "person"},
    ],
    "itinerary": {
        "type": "stream"
    },
    # Rotating labels shown on the itinerary-generation loader. The client cycles
    # through these while the itinerary streams.
    "itinerary_loader_labels": [
        {"title": "Curating your dream journey...", "subTitle": "Powered by AI magic"},
        {"title": "Mapping your perfect route...", "subTitle": "Powered by AI magic"},
        {"title": "Handpicking must-see places...", "subTitle": "Powered by AI magic"},
        {"title": "Finding hidden gems for you...", "subTitle": "Powered by AI magic"},
        {"title": "Planning day-by-day adventures...", "subTitle": "Powered by AI magic"},
        {"title": "Booking the best experiences...", "subTitle": "Powered by AI magic"},
        {"title": "Discovering local flavors...", "subTitle": "Powered by AI magic"},
        {"title": "Pairing stays with your vibe...", "subTitle": "Powered by AI magic"},
        {"title": "Balancing your itinerary...", "subTitle": "Powered by AI magic"},
        {"title": "Optimizing travel times...", "subTitle": "Powered by AI magic"},
        {"title": "Sprinkling in some surprises...", "subTitle": "Powered by AI magic"},
        {"title": "Tailoring trips to your taste...", "subTitle": "Powered by AI magic"},
        {"title": "Scouting the finest spots...", "subTitle": "Powered by AI magic"},
        {"title": "Crafting unforgettable moments...", "subTitle": "Powered by AI magic"},
        {"title": "Aligning stars for your trip...", "subTitle": "Powered by AI magic"},
        {"title": "Matching places to your mood...", "subTitle": "Powered by AI magic"},
        {"title": "Weaving your travel story...", "subTitle": "Powered by AI magic"},
        {"title": "Adding a dash of adventure...", "subTitle": "Powered by AI magic"},
        {"title": "Fine-tuning every detail...", "subTitle": "Powered by AI magic"},
        {"title": "Almost ready to explore...", "subTitle": "Powered by AI magic"},
    ],
}


def _lookup(name, db_loader):
    """Return a config dataset, preferring the local .pkl snapshot and falling
    back to a live DB query when the snapshot is missing. Returns [] if both the
    pkl is absent and the DB query errors, so the rest of the config still serves.
    Rebuild the snapshots with scripts/build_config_pkl.py."""
    data = _load_pkl(name)
    if data is not None:
        return data
    try:
        return db_loader()
    except Exception as e:
        print(f"[config] failed to load {name} from DB: {e}")
        return []


def _load_lookups():
    """Fetch the lookup lists, preferring the .pkl snapshots (see
    scripts/build_config_pkl.py) with a per-dataset DB fallback."""
    group_types = _lookup(
        "group_types",
        lambda: [r["name"] for r in fetch_dicts("SELECT name FROM group_types ORDER BY id")],
    )
    food_preferences = _lookup(
        "food_preferences",
        lambda: [r["name"] for r in fetch_dicts("SELECT name FROM food_preferences ORDER BY id")],
    )
    activities = _lookup(
        "activities",
        lambda: [
            {"id": r["ref_id"], "name": r["name"], "icon": r["icon"]}
            for r in fetch_dicts("SELECT ref_id, name, icon FROM activities ORDER BY id")
        ],
    )
    # Reuse the places service so the popularity ranking stays in one place.
    from features.places.service import query_popular_states
    popular_states = _lookup("popular_states", lambda: query_popular_states(10))

    return group_types, food_preferences, activities, popular_states


def _build_config():
    group_types, food_preferences, activities, popular_states = _load_lookups()
    config = dict(APP_CONFIG)
    config["group_types"] = group_types
    config["food_preferences"] = food_preferences
    config["activities"] = activities
    config["budgetType"] = [
        {
            "name": "Budget",
            "value": "budget",
            "hotel": "Under ₹2000",
            "breakfast": "Under ₹100",
            "meals": "Under ₹200",
            "dinner": "Under ₹200",
        },
        {
            "name": "Mid Range",
            "value": "mid",
            "hotel": "₹1500 - ₹3000",
            "breakfast": "₹100 - ₹200",
            "meals": "₹200 - ₹300",
            "dinner": "₹200 - ₹300",
        },
        {
            "name": "High Range",
            "value": "high",
            "hotel": "₹3000 - ₹7000",
            "breakfast": "₹200 - ₹400",
            "meals": "₹300 - ₹600",
            "dinner": "₹300 - ₹600",
        },
        {
            "name": "Luxury",
            "value": "luxury",
            "hotel": "Above ₹7000",
            "breakfast": "Above ₹400",
            "meals": "Above ₹600",
            "dinner": "Above ₹600",
        },
    ]
    # Ad slots interleaved between the popular states, with the matching inline
    # slot config alongside them. Page-level (sticky/interstitial) ads stay in
    # the `ads` block; inline configs travel with the content that carries them.
    # URL-prefix each state's bare `image` name before interleaving ads.
    popular_states = with_image_urls(popular_states)
    config["popular_states"] = interleave_ads(popular_states, "popular_states")
    config["popular_states_ads"] = get_inline_ads_config("popular_states")
    # Single ad shown on the loader/generating screen.
    config["loader_ad"] = get_inline_ads_config("loader").get("loader")
    config["ads"] = get_ads_config()
    return config


def get_config() -> dict:
    global _config_cache, _config_cache_ts
    if _config_cache is not None and time.monotonic() < _config_cache_ts:
        return _config_cache  # fast path — no lock needed
    with _config_cache_lock:
        if _config_cache is not None and time.monotonic() < _config_cache_ts:
            return _config_cache  # another thread built it while we waited
        print("[config] cache miss — building")
        result = _build_config()
        _config_cache = result
        _config_cache_ts = time.monotonic() + _CONFIG_TTL
        return result


# Datasets snapshotted to .pkl, mapped to the DB query that produces them.
_SNAPSHOTS = {
    "group_types": lambda: [r["name"] for r in fetch_dicts("SELECT name FROM group_types ORDER BY id")],
    "food_preferences": lambda: [r["name"] for r in fetch_dicts("SELECT name FROM food_preferences ORDER BY id")],
    "activities": lambda: [
        {"id": r["ref_id"], "name": r["name"], "icon": r["icon"]}
        for r in fetch_dicts("SELECT ref_id, name, icon FROM activities ORDER BY id")
    ],
    "popular_states": lambda: __import__(
        "features.places.service", fromlist=["query_popular_states"]
    ).query_popular_states(10),
}


def build_config_snapshots(only_missing=False):
    """Query the DB and write each config dataset to `<name>.pkl` at repo root.
    With only_missing=True, skips datasets whose pkl already exists (used at
    startup to self-heal a fresh deploy without overwriting good snapshots)."""
    for name, loader in _SNAPSHOTS.items():
        if only_missing and _load_pkl(name) is not None:
            continue
        try:
            data = loader()
            path = os.path.join(_PROJECT_ROOT, f"{name}.pkl")
            with open(path, "wb") as f:
                pickle.dump(data, f)
            print(f"[config] snapshot {name}.pkl ({len(data)} rows)")
        except Exception as e:
            print(f"[config] failed to snapshot {name}: {e}")


def warm_config_cache():
    """Build any missing snapshots, then pre-build the config cache — both in a
    daemon thread at startup so a fresh deploy serves /configs from pkl."""
    def _warm():
        build_config_snapshots(only_missing=True)
        get_config()
    threading.Thread(target=_warm, daemon=True, name="warm-config").start()
