"""App configuration served to the client.

Drives onboarding pages, screen copy, and the bottom tab bar. The lookup lists
(group types, food preferences, activities) are read from the database so they
can be changed without a deploy.
"""

from core.db import fetch_dicts
from core.ads import get_ads_config, interleave_ads, get_inline_ads_config
from core.images import with_image_urls

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
}


def _load_lookups():
    """Fetch the lookup lists from the database. Returns empty lists for any
    table that errors so the rest of the config still serves."""
    try:
        group_types = [r["name"] for r in fetch_dicts(
            "SELECT name FROM group_types ORDER BY id"
        )]
    except Exception as e:
        print(f"[config] failed to load group_types: {e}")
        group_types = []

    try:
        food_preferences = [r["name"] for r in fetch_dicts(
            "SELECT name FROM food_preferences ORDER BY id"
        )]
    except Exception as e:
        print(f"[config] failed to load food_preferences: {e}")
        food_preferences = []

    try:
        activities = [
            {"id": r["ref_id"], "name": r["name"], "icon": r["icon"]}
            for r in fetch_dicts("SELECT ref_id, name, icon FROM activities ORDER BY id")
        ]
    except Exception as e:
        print(f"[config] failed to load activities: {e}")
        activities = []

    try:
        # Reuse the places service so the popularity ranking stays in one place.
        from features.places.service import query_popular_states
        popular_states = query_popular_states(10)
    except Exception as e:
        print(f"[config] failed to load popular_states: {e}")
        popular_states = []

    return group_types, food_preferences, activities, popular_states


def get_config():
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
    config["ads"] = get_ads_config()
    return config
