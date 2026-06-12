import multiprocessing as mp
import socket

from flask import Flask, jsonify
from flask_cors import CORS
from flasgger import Swagger

# load_dotenv is called inside core/config.py — must be imported before anything else
from core import config as _config_init  # noqa: F401 (triggers load_dotenv)
from core.config import PORT

from core.swagger_config import swagger_template, swagger_config
from core.db import init_db_async
from auth import auth_bp
from features.itinerary import itinerary_bp
from features.itinerary.service import initialize_recommender, is_initialized
from features.places import places_bp
from features.places.service import load_city_coords
from features.images import images_bp

app = Flask(__name__)
CORS(app)

try:
    _server_ip = socket.gethostbyname(socket.gethostname())
except Exception:
    _server_ip = "unknown"


@app.after_request
def add_server_ip(response):
    if response.content_type and "application/json" in response.content_type:
        try:
            data = response.get_json(silent=True)
            if isinstance(data, dict):
                data["server_ip"] = _server_ip
                response.data = jsonify(data).data
        except Exception:
            pass
    return response


Swagger(app, template=swagger_template, config=swagger_config)

app.register_blueprint(auth_bp)
app.register_blueprint(itinerary_bp)
app.register_blueprint(places_bp)
app.register_blueprint(images_bp)

init_db_async()
initialize_recommender()
load_city_coords()


@app.route("/health", methods=["GET"])
def health_check():
    """Health check
    ---
    tags:
      - System
    responses:
      200:
        description: Service is healthy
    """
    return jsonify({"status": "healthy", "initialized": is_initialized()}), 200


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
