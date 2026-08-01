"""Admin authentication package.

Separate from the app-user auth in `auth/` — admins live in their own
`admin_users` table and carry their own JWT (claim `admin_id`, not `user_id`),
so an app user's token can never authenticate into the admin panel and vice
versa.
"""
from auth.admin.routes import admin_auth_bp
from auth.admin.jwt_utils import admin_required, super_admin_required

__all__ = ["admin_auth_bp", "admin_required", "super_admin_required"]
