from flask import Blueprint, request, jsonify

from core.db import is_db_ready
from features.search.service import search as do_search

search_bp = Blueprint("search", __name__)


@search_bp.before_request
def check_db():
    if not is_db_ready():
        return jsonify({"error": "Service initializing, please retry shortly"}), 503


@search_bp.route("/search", methods=["GET"])
def search():
    """Search cities and places by name prefix
    ---
    tags:
      - Search
    parameters:
      - name: q
        in: query
        required: true
        schema:
          type: string
        description: Search query (min 2 characters)
      - name: limit
        in: query
        required: false
        schema:
          type: integer
          default: 10
        description: Max results (1–20)
    responses:
      200:
        description: List of matching cities and places
      400:
        description: Missing or too-short query
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "q is required"}), 400
    if len(q) < 2:
        return jsonify({"error": "q must be at least 2 characters"}), 400

    try:
        limit = int(request.args.get("limit", 10))
    except ValueError:
        limit = 10

    results = do_search(q, limit)
    return jsonify({"results": results}), 200
