"""Admin resource registry.

One entry per admin-managed resource, mapping the REST slug to the app's real
Azure SQL table and its ACTUAL columns (introspected from the live DB — no
invented fields). The generic CRUD service (`service.py`) is driven entirely by
these configs, so adding a resource is a data change, not new query code.

Fields per resource:
  slug        URL path segment (/admin/<slug>)
  table       real table name (trusted — used verbatim in SQL)
  pk          primary key column (identity, never written)
  writable    columns the admin may set on create/update (whitelist; anything
              else in the body is ignored — this is also the injection guard)
  search      text columns matched (case-insensitively) by ?search=
  hidden      columns stripped from every response (secrets)
  methods     which verbs are enabled for this resource
  order_by    ORDER BY clause for list paging (T-SQL OFFSET needs an order)

Only resources with a real backing table are included. tabs/pages/ads/
notifications are intentionally omitted until their schema exists.
"""

# Standard full-CRUD verb set.
_FULL = ("list", "get", "create", "update", "delete")

RESOURCES = {
    "food-preferences": {
        "table": "food_preferences",
        "pk": "id",
        "writable": ["name"],
        "search": ["name"],
        "hidden": [],
        "methods": _FULL,
        "order_by": "id DESC",
    },
    "group-types": {
        "table": "group_types",
        "pk": "id",
        "writable": ["name"],
        "search": ["name"],
        "hidden": [],
        "methods": _FULL,
        "order_by": "id DESC",
    },
    "states": {
        "table": "states",
        "pk": "id",
        "writable": ["name", "country_id"],
        "search": ["name"],
        "hidden": [],
        "methods": _FULL,
        "order_by": "name ASC",
    },
    "activities": {
        "table": "activities",
        "pk": "id",
        "writable": ["ref_id", "name", "icon"],
        "search": ["name", "ref_id"],
        "hidden": [],
        "methods": _FULL,
        "order_by": "id DESC",
    },
    "users": {
        "table": "users",
        "pk": "id",
        # password_hash / reset_token are never accepted or returned here.
        "writable": [
            "name", "email", "phone", "age", "gender",
            "group_type", "food_preference", "activities",
            "google_id", "profile_picture", "is_verified",
        ],
        "search": ["name", "email", "phone"],
        "hidden": ["password_hash", "reset_token", "reset_token_expiry"],
        "methods": _FULL,
        "order_by": "id DESC",
    },
    "places": {
        "table": "places",
        "pk": "id",
        # places has ~90 columns; expose all on read, allow editing the
        # human-maintained business fields (the rest are Google-synced).
        "writable": [
            "city_id", "name", "type", "rating", "num_ratings", "best_month",
            "famous_activities", "lat", "lon", "full_address",
            "prefer_friends", "prefer_couple",
            "prefer_family_children", "prefer_family_no_children",
        ],
        "search": ["name", "type", "full_address"],
        "hidden": [],
        "methods": _FULL,
        "order_by": "id DESC",
    },
    "itineraries": {
        "table": "itineraries",
        "pk": "id",
        "writable": ["request_json", "response_json", "status", "input_token", "output_token"],
        "search": ["status"],
        "hidden": [],
        "methods": _FULL,
        "order_by": "id DESC",
    },
    "feedback": {
        "table": "feedback",
        "pk": "id",
        # Admins review and remove feedback; they don't author or rewrite it.
        "writable": [],
        "search": ["type", "message", "name", "email"],
        "hidden": [],
        "methods": ("list", "get", "delete"),
        "order_by": "id DESC",
        # Custom LIST: joins users + itineraries so the panel gets author and
        # itinerary details inline. See service.list_feedback_rows.
        "list_fn": "list_feedback_rows",
    },
}
