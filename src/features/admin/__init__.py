"""Admin panel resource CRUD feature.

Generic, registry-driven CRUD over the app's EXISTING tables (users, places,
itineraries, feedback, states, group_types, food_preferences, activities). No
new data tables — the only admin-owned table is `admin_users` (auth). See
resources.py for the resource→table mapping.
"""
from features.admin.routes import admin_bp

__all__ = ["admin_bp"]
