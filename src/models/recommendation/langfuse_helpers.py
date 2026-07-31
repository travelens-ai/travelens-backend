from contextlib import contextmanager

try:
    from langfuse import get_client as _lf_get_client
    _LF_AVAILABLE = True
except ImportError:
    _LF_AVAILABLE = False
    def _lf_get_client():
        return None


@contextmanager
def lf_span(name, **kwargs):
    if _LF_AVAILABLE:
        with _lf_get_client().start_as_current_observation(name=name, **kwargs):
            yield
    else:
        yield


def lf_update_span(**kwargs):
    if _LF_AVAILABLE:
        _lf_get_client().update_current_span(**kwargs)


def safe_prefs(p):
    """Return a Langfuse-safe subset of user_preferences (no DataFrames/binary blobs)."""
    return {
        "destination": p.get("places_of_interest", ""),
        "trip_type": p.get("trip_type", ""),
        "trip_duration": p.get("trip_duration", ""),
        "budget": p.get("budget", ""),
        "num_people": p.get("number_of_people", ""),
        "travel_group_type": p.get("travel_group_type", ""),
        "food_preferences": p.get("food_preferences", ""),
        "activities": p.get("preferred_activities", []),
        "start_date": p.get("start_date", ""),
    }
