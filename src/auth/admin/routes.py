"""Admin auth routes: OTP login (public) and me (token-protected).

Mounted at /admin/auth/*. Login is email-OTP based (no password):
    POST /admin/auth/request-otp  {email}          -> sends a one-time code
    POST /admin/auth/verify-otp   {email, otp}      -> {token, admin}
    GET  /admin/auth/me                             -> current admin (Bearer)

The two POST routes must be reachable without a token so an admin can obtain
one; all of /admin is already exempt from the global user/device guard (see
auth/guard.py), and these two are the only /admin routes without admin_required.
"""
from flask import Blueprint, request, jsonify

from core.db import is_db_ready
from auth.admin import service
from auth.admin.jwt_utils import admin_required, super_admin_required

admin_auth_bp = Blueprint("admin_auth", __name__)


@admin_auth_bp.before_request
def check_db_ready():
    if not is_db_ready():
        return jsonify({"message": "Database is connecting, please try again shortly"}), 503


@admin_auth_bp.after_request
def add_status(response):
    """Inject a top-level boolean `status` (true on 2xx) into every JSON dict
    response, unless the handler already set one (request-otp sets its own)."""
    if response.content_type and "application/json" in response.content_type:
        data = response.get_json(silent=True)
        if isinstance(data, dict) and "status" not in data:
            data["status"] = 200 <= response.status_code < 300
            response.data = jsonify(data).data
    return response


@admin_auth_bp.route("/admin/auth/request-otp", methods=["POST"])
def request_otp():
    """Request an admin login OTP
    ---
    tags:
      - Admin Auth
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [email]
          properties:
            email:
              type: string
              example: "travelens.ai@gmail.com"
    responses:
      200:
        description: >
          Registered → {"message":"An OTP send successfully","status":true};
          not registered → {"message":"invalid email id","status":false}
    """
    data = request.json or {}
    email = data.get("email")
    if not email:
        return jsonify({"message": "email is required", "status": False}), 400

    registered, (status, msg, code) = service.request_login_otp(email)
    return jsonify({"message": msg, "status": registered}), code


@admin_auth_bp.route("/admin/auth/verify-otp", methods=["POST"])
def verify_otp():
    """Verify an admin login OTP and receive a JWT
    ---
    tags:
      - Admin Auth
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [email, otp]
          properties:
            email:
              type: string
              example: "travelens.ai@gmail.com"
            otp:
              type: string
              example: "123456"
    responses:
      200:
        description: Returns a JWT and the admin profile
      400:
        description: Invalid or expired OTP
    """
    data = request.json or {}
    email = data.get("email")
    otp = data.get("otp")
    if not email or not otp:
        return jsonify({"message": "email and otp are required"}), 400

    result, (status, msg, code) = service.verify_login_otp(email, otp)
    if result is not None:
        return jsonify(result), code
    return jsonify({"message": msg}), code


@admin_auth_bp.route("/admin/admins", methods=["GET"])
@admin_required
def list_admins():
    """List all admins
    ---
    tags:
      - Admin Management
    security:
      - Bearer: []
    responses:
      200:
        description: '{data:[{id,name,email,role,status}], status:true}'
      401:
        description: Missing or invalid admin token
    """
    admins, (_status, msg, code) = service.list_admins()
    if admins is not None:
        return jsonify({"data": admins}), code
    return jsonify({"message": msg}), code


@admin_auth_bp.route("/admin/admins", methods=["POST"])
@super_admin_required
def add_admin():
    """Add an admin (super admin only)
    ---
    tags:
      - Admin Management
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [name, email]
          properties:
            name:
              type: string
              example: "New Admin"
            email:
              type: string
              example: "new.admin@example.com"
            status:
              type: string
              enum: [admin, "super admin"]
              default: admin
    responses:
      201:
        description: '{data:{id,name,email,role,status}, status:true}'
      400:
        description: Invalid input or duplicate email
      401:
        description: Missing or invalid admin token
      403:
        description: Super admin privilege required
    """
    data = request.json or {}
    result, (_status, msg, code) = service.create_admin(
        data.get("name"), data.get("email"), data.get("status", "admin")
    )
    if result is not None:
        return jsonify({"data": result}), code
    return jsonify({"message": msg}), code


@admin_auth_bp.route("/admin/admins/<int:admin_id>", methods=["DELETE"])
@super_admin_required
def remove_admin(admin_id):
    """Delete an admin (super admin only)
    ---
    tags:
      - Admin Management
    security:
      - Bearer: []
    parameters:
      - in: path
        name: admin_id
        type: integer
        required: true
    responses:
      200:
        description: '{message:"Deleted", status:true}'
      400:
        description: Cannot delete self or the last super admin
      401:
        description: Missing or invalid admin token
      403:
        description: Super admin privilege required
      404:
        description: Admin not found
    """
    ok, (_status, msg, code) = service.delete_admin(admin_id, requester_id=request.admin_id)
    if ok:
        return jsonify({"message": "Deleted"}), code
    return jsonify({"message": msg}), code


@admin_auth_bp.route("/admin/auth/me", methods=["GET"])
@admin_required
def me():
    """Current admin
    ---
    tags:
      - Admin Auth
    security:
      - Bearer: []
    responses:
      200:
        description: The authenticated admin's profile
      401:
        description: Missing or invalid token
    """
    admin = service.get_admin(request.admin_id)
    if not admin:
        return jsonify({"message": "Admin not found"}), 404
    return jsonify(admin), 200
