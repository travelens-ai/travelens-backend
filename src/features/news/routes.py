from flask import Blueprint, request, jsonify

import features.news.service as news_service

news_bp = Blueprint("news", __name__)


@news_bp.route("/news", methods=["GET"])
def get_news():
    """Get a news feed for a search query (Google News RSS as JSON)
    ---
    tags:
      - News
    parameters:
      - in: query
        name: q
        type: string
        required: true
        description: Search query (e.g. "chikamagalur news")
        example: chikamagalur news
    responses:
      200:
        description: Parsed news feed
        schema:
          type: object
          properties:
            status:
              type: string
            query:
              type: string
            count:
              type: integer
            feed:
              type: object
            articles:
              type: array
              items:
                type: object
                properties:
                  title:
                    type: string
                  link:
                    type: string
                  published:
                    type: string
                  description:
                    type: string
                  source:
                    type: string
      400:
        description: Missing query parameter
      503:
        description: News feed unavailable
    """
    query = request.args.get("q", "").strip()

    result, error = news_service.get_news(query)

    if error:
        if "required" in error.lower():
            return jsonify({"status": "error", "message": error}), 400
        return jsonify({"status": "error", "message": error}), 503

    return jsonify({"status": "success", **result}), 200
