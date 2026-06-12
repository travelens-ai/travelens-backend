import requests as http_requests

from flask import Blueprint, request, jsonify

from core.db import is_db_ready
from auth.jwt_utils import token_required
import auth.service as service

auth_bp = Blueprint("auth", __name__)


@auth_bp.before_request
def check_db_ready():
    if not is_db_ready():
        return jsonify({"status": "error", "message": "Database is connecting, please try again shortly"}), 503


@auth_bp.route("/send-otp", methods=["POST"])
def send_otp():
    """Send OTP to email for verification
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
          properties:
            email:
              type: string
              example: john@example.com
            purpose:
              type: string
              enum: [signup, forgot_password]
              default: signup
              example: signup
    responses:
      200:
        description: OTP sent successfully
      409:
        description: Email already registered (for signup purpose)
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    email = data.get("email")
    purpose = data.get("purpose", "signup")

    if not email:
        return jsonify({"status": "error", "message": "Email is required"}), 400

    if purpose not in ("signup", "forgot_password"):
        return jsonify({"status": "error", "message": "Invalid purpose. Use 'signup' or 'forgot_password'"}), 400

    _, (status, message, code) = service.create_otp_record(email, purpose)
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/verify-otp", methods=["POST"])
def verify_otp():
    """Verify OTP sent to email
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
            - otp
          properties:
            email:
              type: string
              example: john@example.com
            otp:
              type: string
              example: "123456"
            purpose:
              type: string
              enum: [signup, forgot_password]
              default: signup
              example: signup
    responses:
      200:
        description: OTP verified successfully
      400:
        description: Invalid or expired OTP
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    email = data.get("email")
    otp = data.get("otp")
    purpose = data.get("purpose", "signup")

    if not email or not otp:
        return jsonify({"status": "error", "message": "Email and OTP are required"}), 400

    ok, (status, message, code) = service.verify_otp_record(email, otp, purpose)
    if ok:
        return jsonify({"status": status, "message": message, "email": email, "purpose": purpose}), code
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/signup", methods=["POST"])
def signup():
    """Register a new user (OTP must be verified first)
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - name
            - email
            - password
          properties:
            name:
              type: string
              example: John Doe
            email:
              type: string
              example: john@example.com
            password:
              type: string
              example: SecurePass123
            phone:
              type: string
              example: "+919876543210"
            age:
              type: integer
              example: 28
            gender:
              type: string
              enum: [male, female, other]
              example: male
            trip_type:
              type: string
              description: "solo, friends, family"
              example: solo
            trip_companion:
              type: string
              description: "male, female, mix, with_children, without_children"
              example: male
    responses:
      201:
        description: User registered successfully
      403:
        description: Email not verified via OTP
      409:
        description: Email already registered
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    name = data.get("name")
    email = data.get("email")
    password = data.get("password")

    if not name or not email or not password:
        return jsonify({"status": "error", "message": "Name, email and password are required"}), 400

    result, (status, message, code) = service.create_user(
        name=name,
        email=email,
        password=password,
        phone=data.get("phone"),
        age=data.get("age"),
        gender=data.get("gender"),
        trip_type=data.get("trip_type"),
        trip_companion=data.get("trip_companion"),
        device_id=data.get("device_id"),
    )
    if result:
        return jsonify({"status": status, "message": message, **result}), code
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/login", methods=["POST"])
def login():
    """Login with email and password
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
            - password
          properties:
            email:
              type: string
              example: john@example.com
            password:
              type: string
              example: SecurePass123
    responses:
      200:
        description: Login successful, returns JWT token
      401:
        description: Invalid email or password
      403:
        description: Email not verified
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"status": "error", "message": "Email and password are required"}), 400

    result, (status, message, code) = service.authenticate_user(email, password)
    if result:
        return jsonify({"status": status, "message": message, **result}), code
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    """Send OTP for password reset
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
          properties:
            email:
              type: string
              example: john@example.com
    responses:
      200:
        description: OTP sent to email
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    email = data.get("email")
    if not email:
        return jsonify({"status": "error", "message": "Email is required"}), 400

    _, (status, message, code) = service.send_otp_internal(email, "forgot_password")
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    """Reset password using OTP
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
            - otp
            - new_password
          properties:
            email:
              type: string
              example: john@example.com
            otp:
              type: string
              example: "123456"
            new_password:
              type: string
              example: NewSecurePass456
    responses:
      200:
        description: Password reset successfully
      400:
        description: Invalid or expired OTP
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    email = data.get("email")
    otp = data.get("otp")
    new_password = data.get("new_password")

    if not email or not otp or not new_password:
        return jsonify({"status": "error", "message": "Email, OTP and new password are required"}), 400

    _, (status, message, code) = service.reset_user_password(email, otp, new_password)
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/update", methods=["PUT"])
@token_required
def update_profile():
    """Update user profile or password
    ---
    tags:
      - User
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            name:
              type: string
              example: Updated Name
            phone:
              type: string
              example: "+919999999999"
            age:
              type: integer
              example: 30
            gender:
              type: string
              enum: [male, female, other]
            trip_type:
              type: string
              example: family
            trip_companion:
              type: string
              example: with_children
            profile_picture:
              type: string
              example: https://example.com/pic.jpg
            old_password:
              type: string
              description: Required for password change
              example: OldPass123
            new_password:
              type: string
              description: Required for password change
              example: NewPass456
    responses:
      200:
        description: Profile or password updated successfully
      401:
        description: Token missing/invalid or wrong old password
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    result, (status, message, code) = service.update_user_profile(request.user_id, data)
    if result is not None:
        return jsonify({"status": status, "message": message, **result}), code
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/google-login", methods=["POST"])
def google_login():
    """Login or register via Google
    ---
    tags:
      - Authentication
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - id_token
          properties:
            id_token:
              type: string
              description: Google ID token from frontend OAuth
              example: eyJhbGciOiJSUzI1NiIs...
    responses:
      200:
        description: Login successful
      201:
        description: New user registered via Google
      401:
        description: Invalid Google token
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    id_token = data.get("id_token")
    if not id_token:
        return jsonify({"status": "error", "message": "Google ID token is required"}), 400

    try:
        google_response = http_requests.get(f"https://oauth2.googleapis.com/tokeninfo?id_token={id_token}")
        if google_response.status_code != 200:
            return jsonify({"status": "error", "message": "Invalid Google token"}), 401

        google_data = google_response.json()
        google_id = google_data.get("sub")
        email = google_data.get("email")
        name = google_data.get("name", "")
        picture = google_data.get("picture", "")

        if not email:
            return jsonify({"status": "error", "message": "Could not get email from Google"}), 400
    except http_requests.RequestException:
        return jsonify({"status": "error", "message": "Failed to verify Google token"}), 500

    result, (status, message, code) = service.google_upsert_user(
        google_id=google_id, email=email, name=name, picture=picture,
        device_id=data.get("device_id"),
    )
    if result:
        is_new = result.pop("is_new", False)
        return jsonify({"status": status, "message": message, **result}), code
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/favorite", methods=["POST"])
def add_favorite():
    """Add itinerary to favorites
    ---
    tags:
      - Favorites
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - itinerary_id
            - user_id
          properties:
            itinerary_id:
              type: integer
              example: 1
            user_id:
              type: string
              description: Device ID (before signup) or user ID (after signup)
              example: "abc123-device-uuid"
    responses:
      201:
        description: Added to favorites
      409:
        description: Already in favorites
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    itinerary_id = data.get("itinerary_id")
    user_id = data.get("user_id")
    if not itinerary_id or not user_id:
        return jsonify({"status": "error", "message": "itinerary_id and user_id are required"}), 400

    _, (status, message, code) = service.add_favorite(user_id, itinerary_id)
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/favorite", methods=["GET"])
def get_favorites():
    """Get favorite itineraries by user_id
    ---
    tags:
      - Favorites
    parameters:
      - in: query
        name: user_id
        type: string
        required: true
        description: Device ID or user ID
    responses:
      200:
        description: List of favorite itineraries
      400:
        description: user_id is required
    """
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "user_id query param is required"}), 400

    favorites, (status, message, code) = service.get_favorites(user_id)
    if favorites is not None:
        return jsonify({"status": status, "favorites": favorites}), code
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/favorite", methods=["DELETE"])
def remove_favorite():
    """Remove itinerary from favorites
    ---
    tags:
      - Favorites
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - itinerary_id
            - user_id
          properties:
            itinerary_id:
              type: integer
              example: 1
            user_id:
              type: string
              example: "abc123-device-uuid"
    responses:
      200:
        description: Removed from favorites
      404:
        description: Favorite not found
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    itinerary_id = data.get("itinerary_id")
    user_id = data.get("user_id")
    if not itinerary_id or not user_id:
        return jsonify({"status": "error", "message": "itinerary_id and user_id are required"}), 400

    _, (status, message, code) = service.remove_favorite(user_id, itinerary_id)
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/history", methods=["POST"])
def add_history():
    """Add itinerary to history
    ---
    tags:
      - History
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - itinerary_id
            - user_id
          properties:
            itinerary_id:
              type: integer
              example: 1
            user_id:
              type: string
              description: Device ID (before signup) or user ID (after signup)
              example: "abc123-device-uuid"
    responses:
      201:
        description: Added to history
      400:
        description: Missing required fields
      404:
        description: Itinerary not found
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Request body is required"}), 400

    itinerary_id = data.get("itinerary_id")
    user_id = data.get("user_id")
    if not itinerary_id or not user_id:
        return jsonify({"status": "error", "message": "itinerary_id and user_id are required"}), 400

    _, (status, message, code) = service.add_history(user_id, itinerary_id)
    return jsonify({"status": status, "message": message}), code


@auth_bp.route("/history", methods=["GET"])
def get_history():
    """Get itinerary history by user_id
    ---
    tags:
      - History
    parameters:
      - in: query
        name: user_id
        type: string
        required: true
        description: Device ID or user ID
    responses:
      200:
        description: List of itinerary history
      400:
        description: user_id is required
    """
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "user_id query param is required"}), 400

    history, (status, message, code) = service.get_history(user_id)
    if history is not None:
        return jsonify({"status": status, "history": history}), code
    return jsonify({"status": status, "message": message}), code
