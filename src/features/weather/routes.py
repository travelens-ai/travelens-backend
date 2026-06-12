from flask import Blueprint, request, jsonify

import features.weather.service as weather_service

weather_bp = Blueprint("weather", __name__)


@weather_bp.route("/weather", methods=["GET"])
def get_weather():
    """Get weather forecast or climate normals for a city and date range
    ---
    tags:
      - Weather
    parameters:
      - in: query
        name: city
        type: string
        required: true
        description: City name (e.g. Shimla, Delhi, Manali)
        example: Shimla
      - in: query
        name: start_date
        type: string
        required: true
        description: Trip start date in YYYY-MM-DD format
        example: "2026-06-20"
      - in: query
        name: days
        type: integer
        required: false
        default: 3
        description: Number of days (1-16, default 3)
        example: 5
    responses:
      200:
        description: Weather data — real forecast (within 16 days) or climate averages (beyond 16 days)
        schema:
          type: object
          properties:
            city:
              type: string
            is_forecast:
              type: boolean
              description: true = real forecast, false = historical climate averages
            weather:
              type: array
              items:
                type: object
                properties:
                  date:
                    type: string
                  condition:
                    type: string
                  emoji:
                    type: string
                  temp_max:
                    type: integer
                  temp_min:
                    type: integer
                  rain_chance:
                    type: integer
      400:
        description: Missing or invalid parameters
      404:
        description: City not found
      503:
        description: Weather API unavailable
    """
    city = request.args.get("city", "").strip()
    start_date = request.args.get("start_date", "").strip()
    days = request.args.get("days", 3, type=int)

    if not city:
        return jsonify({"status": "error", "message": "city is required"}), 400
    if not start_date:
        return jsonify({"status": "error", "message": "start_date is required (YYYY-MM-DD)"}), 400

    result, error = weather_service.get_weather(city, start_date, days)

    if error:
        if "not found" in error.lower():
            return jsonify({"status": "error", "message": error}), 404
        if "format" in error.lower():
            return jsonify({"status": "error", "message": error}), 400
        return jsonify({"status": "error", "message": error}), 503

    return jsonify({"status": "success", **result}), 200
