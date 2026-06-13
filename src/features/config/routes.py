from flask import Blueprint, jsonify

from features.config.service import get_config

config_bp = Blueprint("config", __name__)


@config_bp.route("/configs", methods=["GET"])
def get_configs():
    """Get static app configuration (onboarding pages, screen copy, tabs)
    ---
    tags:
      - Config
    responses:
      200:
        description: App configuration with pages and tabs
    """
    return jsonify(get_config()), 200
