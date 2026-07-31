"""App configuration served to the client.

Drives onboarding pages, screen copy, and the bottom tab bar. The lookup lists
(group types, food preferences, activities) are read from the database so they
can be changed without a deploy.
"""

import threading
import time

from core.db import fetch_dicts
from core.ads import get_ads_config, interleave_ads, get_inline_ads_config
from core.images import with_image_urls

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
            "texts": {
                "title": "Discover India at your own pace",
                "desc": "Travel your way and discover, enjoy and explore India with comfort.",
                "getStarted": "Get Started",
                # Welcome screen variant copy
                "welcomeTitle": "Discover the India at your own place",
                "welcomeDescription": "Travel your way and discover, enjoy and explore the india with comfort",
            },
        },
        {
            "type": "LAUNCH",
            "bg": "",
            "topImage": "",
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
            "texts": {
                "next": "Next",
                "skip": "Skip",
                "enterYourAge": "Enter your age",
            },
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
                "defaultName": "Traveler",
                "greeting": "Hi",
                "searchPlaceholder": "Search destinations...",
                "tripPlannerCta": "Start Planning",
                "tripPlannerSubtitle": "Let our AI build a personalized itinerary just for you",
                "tripPlannerTitle": "AI Trip Planner",
                "createCustomItineraryCta": "Create Custom Itinerary",
                "tabPopular": "Popular",
                "tabNearby": "Nearby",
                "tabTrending": "Trending",
                "tabWeekend": "Weekend",
                "enableLocationCta": "Enable Location",
                "enableLocationNearbyPrompt": "Enable location permission to access the nearby places",
                "enableLocationWeekendPrompt": "Enable location permission to access the weekend places",
                "locationPermissionTitle": "Location Permission",
                "locationPermissionMessage": "This app needs access to your location to show nearby and weekend places.",
                "locationPermissionAllow": "Allow",
                "locationPermissionDeny": "Deny",
            },
        },
        {
            "type": "AI_TRIP_PLANNER",
            "bg": "",
            "texts": {
                "ctaLabel": "Next",
                "subtitle": "Tell us where you want to go",
                "title": "Plan Your Trip",
                # Planner step 1
                "step1Of3": "Step 1 of 3",
                "whereToGo": "Where do you want to go?",
                "enterDestination": "Enter destination...",
                "popularDestinations": "Popular Destinations",
                # Planner step 2
                "step2Of3": "Step 2 of 3",
                "whenAndWho": "Details?",
                "skip": "Skip",
                "selectDateRange": "Select date range",
                "numberOfPeople": "Number of people",
                "peoplePlaceholder": "e.g. 4",
                "budgetTypeOptional": "Budget type (optional)",
                "selectBudgetType": "Select budget type",
                "arrivalTimeOptional": "Arrival time (optional)",
                "departureTimeOptional": "Departure time (optional)",
                "selectTime": "Select time",
                "groupType": "Group type",
                "budgetTypes": "Budget types",
                "hotel": "Hotel",
                "breakfast": "Breakfast",
                "meals": "Meals",
                "dinner": "Dinner",
                "next": "Next",
                # Planner step 3
                "step3Of3": "Step 3 of 3",
                "preferencesAndActivities": "Preferences & Activities",
                "foodPreferences": "Food Preferences",
                "activities": "Activities",
                "tripSummary": "Trip Summary",
                "destination": "Destination",
                "dates": "Dates",
                "daysSuffix": "days",
                "threeDaysDefault": "3 days (default)",
                "people": "People",
                "twoDefault": "2 (default)",
                "group": "Group",
                "coupleDefault": "Couple (default)",
                "budget": "Budget",
                "anyDefault": "Any (default)",
                "arrival": "Arrival",
                "departure": "Departure",
                "food": "Food",
                "sightseeingDefault": "Sightseeing (default)",
                "tripType": "Trip type",
                "relaxDefault": "Relax (default)",
                "generateItinerary": "Generate Itinerary",
            },
        },
        {
            "type": "SEARCH",
            "bg": "",
            "texts": {
                "emptyState": "Start typing to search destinations",
                "placeholder": "Where do you want to go?",
                "title": "Search",
                "searchPlaceholder": "Search destinations...",
                "useThis": "Use this",
                "noDestinationsFound": "No destinations found",
                "searchEmptyPrompt": "Search for a city, state or place to start planning",
            },
        },
        {
            "type": "HISTORY",
            "bg": "",
            "texts": {
                "emptySubtitle": "Your past itineraries will appear here",
                "emptyTitle": "Trip History",
                "title": "Trip History",
                "loadErrorMessage": "Failed to load history",
                "loggedOutTitle": "Login to save the history",
                "loggedOutSubtitle": "Sign in to save and view your past itineraries",
                "dayLabelSingular": "Day",
                "dayLabelPlural": "Days",
                "tripNumberPrefix": "Trip #",
            },
        },
        {
            "type": "FAVORITE",
            "bg": "",
            "texts": {
                "emptySubtitle": "Your favorite destinations will appear here",
                "emptyTitle": "Favorites",
                "title": "Favorites",
                "loadErrorMessage": "Failed to load favorites",
                "loggedOutTitle": "Login to save the favorite",
                "loggedOutSubtitle": "Sign in to save and view your favorite destinations",
                "removeFavoriteTitle": "Remove Favorite",
                "removeFavoriteMessage": "Are you sure you want to remove this from favorites?",
                "cancelCta": "Cancel",
                "removeCta": "Remove",
                "dayLabelSingular": "Day",
                "dayLabelPlural": "Days",
            },
        },
        {
            "type": "PROFILE",
            "bg": "",
            "texts": {
                "editCta": "Edit Profile",
                "googleCta": "Continue with Google",
                "headerLink": "Help & Settings",
                "loginCta": "Log In",
                "loginSubtitle": "Save your trips, get personalized recommendations and more",
                "loginTitle": "Login to Travelens",
                "logoutCta": "Log Out",
                "signupLink": "Create Account",
                "signupPrompt": "New here?",
                "title": "My Account",
                # Sign-in flow alerts
                "signInFailedTitle": "Sign in failed",
                "googleSignInErrorMessage": "Could not sign in with Google. Please try again.",
                "loginRejectedMessage": "Login was rejected.",
                "genericSignInErrorMessage": "Something went wrong. Please try again.",
                # Permission alerts
                "permissionDeniedTitle": "Permission Denied",
                "notificationPermissionDeniedMessage": "Please enable notifications from your device settings.",
                "locationPermissionDeniedMessage": "Please enable location from your device settings.",
                # Logout confirm
                "logoutAlertTitle": "Logout",
                "logoutAlertMessage": "Are you sure you want to log out?",
                "cancelCta": "Cancel",
                "logoutConfirmCta": "Log Out",
                # Profile info row labels
                "phoneLabel": "Phone",
                "ageLabel": "Age",
                "genderLabel": "Gender",
                "groupTypeLabel": "Group Type",
                "foodPreferenceLabel": "Food Preference",
                "activitiesLabel": "Activities",
                # Rate us card
                "rateUsTitle": "RATE US",
                "rateUsSubtitle": "Enjoying Travelens? Rate us on the Play Store",
                "rateCta": "Rate",
                # Feedback card
                "feedbackTitle": "FEEDBACK",
                "feedbackSubtitle": "Have a suggestion or found an issue?",
                "feedbackCta": "Let us know",
                "sendFeedbackModalTitle": "Send us feedback",
                # Permissions section
                "permissionsSectionTitle": "Permissions",
                "notificationsEnabledTitle": "NOTIFICATIONS ENABLED",
                "notificationsDisabledTitle": "YOU'RE ALL CAUGHT UP",
                "notificationsEnabledSubtitle": "You'll receive trip updates and offers",
                "notificationsDisabledSubtitle": "Enable notifications to stay updated",
                "locationEnabledTitle": "LOCATION ENABLED",
                "locationDisabledTitle": "LOCATION ACCESS",
                "locationEnabledSubtitle": "Nearby destinations will be personalized",
                "locationDisabledSubtitle": "Allow location for nearby recommendations",
                "allowCta": "Allow",
                # Legal links
                "termsAndConditions": "Terms & Conditions",
                "privacyPolicy": "Privacy Policy",
            },
        },
        {
            "type": "ITINERARY",
            "bg": "",
            "texts": {
                # Loader
                "loaderTitle": "Curating your dream journey...",
                "loaderSubtitle": "Powered by AI magic",
                "craftingJourney": "Crafting your journey",
                "loaderIconDestinations": "Destinations",
                "loaderIconHotels": "Hotels",
                "loaderIconRestaurant": "Restaurant",
                # Meals
                "mealBreakfast": "Breakfast",
                "mealLunch": "Lunch",
                "mealDinner": "Dinner",
                # Errors
                "errorNoTripData": "No trip data provided",
                "errorFailedToGenerate": "Failed to generate itinerary",
                "errorNetwork": "Network error. Please try again.",
                "errorScreenTitle": "Couldn't create your trip",
                "errorScreenFallback": "Something went wrong",
                "errorGoBackButton": "Go Back",
                # Edit alerts
                "alertEditFailedTitle": "Edit failed",
                "alertEditFailedRetry": "Please try again.",
                "alertEditFailedNetwork": "Network error. Please try again.",
                # Favorite
                "toastLoginToFavorite": "Login to save the favorite",
                # Header / days
                "daysItinerarySuffix": "Days Itinerary",
                "dayTabFallback": "Day",
                "daySpanLabel": "Day",
                "tripDurationDaysSuffix": "days",
                # Timeline
                "sectionYourDay": "Your Day",
                "hotelCheckOut": "Check-out",
                "hotelCheckIn": "Check-in",
                "hotelDefaultEvent": "Hotel",
                "viewOnMap": "View on map",
                "hideOptions": "Hide options ▲",
                # Sections
                "sectionAccommodation": "Accommodation",
                "sectionDining": "Dining",
                "priceLabel": "Price:",
                "approxCostLabel": "Approx Cost:",
                "sectionSimilarDestinations": "Similar Destinations",
                "shareFeedbackButton": "Share Feedback",
                # Trip details modal
                "modalTripDetailsTitle": "Trip Details",
                "modalTripDetailsSubtitle": "The preferences used to generate this itinerary",
                "infoTripDuration": "Trip duration",
                "infoNumberOfPeople": "Number of people",
                "infoTravelGroup": "Travel group",
                "infoTripType": "Trip type",
                "infoFoodPreferences": "Food preferences",
                "infoPreferredActivities": "Preferred activities",
                "infoYourLocation": "Your location",
                "infoLocationNotShared": "Not shared",
                "editButton": "Edit",
            },
        },
        {
            "type": "EDIT_PROFILE",
            "bg": "",
            "texts": {
                "successTitle": "Success",
                "profileUpdatedSuccessfully": "Profile updated successfully",
                "errorTitle": "Error",
                "failedToUpdateProfile": "Failed to update profile",
                "networkErrorTryAgain": "Network error. Please try again.",
                "nameLabel": "Name",
                "enterName": "Enter name",
                "phoneLabel": "Phone",
                "enterPhoneNumber": "Enter phone number",
                "ageLabel": "Age",
                "enterAge": "Enter age",
                "genderLabel": "Gender",
                "genderMale": "Male",
                "genderFemale": "Female",
                "genderOther": "Other",
                "groupTypeLabel": "Group Type",
                "foodPreferencesLabel": "Food Preferences",
                "activitiesLabel": "Activities",
                "saving": "Saving...",
                "saveChanges": "Save Changes",
            },
        },
        {
            "type": "NOTIFICATIONS",
            "bg": "",
            "texts": {
                "headerTitle": "Notifications",
                "emptyTitle": "No notifications yet",
                "emptySubtitle": "We'll let you know when something arrives.",
                "timeJustNow": "Just now",
                "timeMinutesSuffix": "m ago",
                "timeHoursSuffix": "h ago",
                "timeDaysSuffix": "d ago",
            },
        },
        {
            "type": "FEEDBACK",
            "bg": "",
            "texts": {
                "defaultTitle": "Share your feedback",
                "enterFeedbackToast": "Please enter your feedback",
                "feedbackThanksToast": "Thanks for your feedback!",
                "feedbackErrorToast": "Could not send feedback. Please try again.",
                "nameLabel": "Name (optional)",
                "namePlaceholder": "Your name",
                "emailLabel": "Email (optional)",
                "emailPlaceholder": "you@example.com",
                "phoneLabel": "Phone (optional)",
                "phonePlaceholder": "Phone number",
                "messageLabel": "Message *",
                "messagePlaceholder": "Tell us what you think...",
                "submitButton": "Submit Feedback",
            },
        },
        {
            "type": "PLACE_DETAILS",
            "bg": "",
            "texts": {
                "whatPeopleSaySection": "What people say",
                "famousActivitiesSection": "Famous activities",
                "openingHoursSection": "Opening hours",
                "addressSection": "Address",
                "viewOnGoogleMaps": "View on Google Maps",
                "websiteLink": "Website",
            },
        },
        {
            "type": "GOOGLE_CONSENT",
            "bg": "",
            "texts": {
                "termsTitle": "Terms & Conditions",
                "agreeTermsLabel": "I have read and agree to the Terms & Conditions",
                "continueWithGoogle": "Continue with Google",
            },
        },
        {
            "type": "EDIT_ITINERARY",
            "bg": "",
            "texts": {
                "modalTitle": "Edit Itinerary",
                "tabWhenAndWho": "Details",
                "tabPreferences": "Preferences",
                "tabPlaces": "Places",
                "selectDateRangeLabel": "Select date range",
                "tripDurationLabel": "Trip duration (days)",
                "tripDurationPlaceholder": "e.g. 3",
                "numberOfPeopleLabel": "Number of people",
                "numberOfPeoplePlaceholder": "e.g. 4",
                "budgetTypeLabel": "Budget type",
                "selectBudgetTypePlaceholder": "Select budget type",
                "arrivalTimeLabel": "Arrival Time",
                "departureTimeLabel": "Departure Time",
                "selectTimePlaceholder": "Select time",
                "groupTypeLabel": "Group type",
                "foodPreferencesLabel": "Food preferences",
                "activitiesLabel": "Activities",
                "selectPlacesSubtitle": "Select the places to include in your itinerary",
                "tapForDetailsHint": "Tap for details",
                "noPlacesAvailable": "No places available.",
                "updateItineraryButton": "Update Itinerary",
            },
        },
        {
            "type": "AI_PLANNER_CARD",
            "bg": "",
            "texts": {
                "aiPlannerTitle": "AI Trip Planner",
                "aiPlannerSubtitle": "Plan your perfect trip with smart recommendations",
            },
        },
        {
            "type": "FREETEXT_INPUT",
            "bg": "",
            "texts": {
                "freetextLabel": "Or describe your dream trip",
                "freetextPlaceholder": "e.g. 5 day beach trip to Bali for 2 people...",
                "goButton": "Go",
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
        "type": "normal"
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


def warm_config_cache():
    """Pre-build the config cache in a daemon thread at startup."""
    threading.Thread(target=get_config, daemon=True, name="warm-config").start()
