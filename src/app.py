import multiprocessing as mp
import os
import socket
import subprocess
import sys

from apscheduler.schedulers.background import BackgroundScheduler
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
from features.user import user_bp
from features.weather import weather_bp
from features.search import search_bp
from features.config import config_bp

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
app.register_blueprint(user_bp)
app.register_blueprint(weather_bp)
app.register_blueprint(search_bp)
app.register_blueprint(config_bp)

init_db_async()
initialize_recommender()
load_city_coords()

# ---------------------------------------------------------------------------
# Background data-fill cron jobs (APScheduler)
# Each script exits immediately when nothing is left to fill — no wasted calls.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")

def _run(script, args=[]):
    path = os.path.join(_SCRIPTS_DIR, script)
    subprocess.run([sys.executable, path] + args,
                   cwd=os.path.dirname(_SCRIPTS_DIR))

_scheduler = BackgroundScheduler(daemon=True)
_TEST_MODE = os.getenv("CRON_TEST_MODE", "0") == "1"

if _TEST_MODE:
    # Test mode: both jobs fire every 5 minutes with 10 items each
    print("[cron] TEST MODE — running every 5 minutes with batch=10")
    _scheduler.add_job(lambda: _run("update_google_ratings.py", ["--batch", "10"]),
                       "interval", minutes=5, id="google_ratings")
    _scheduler.add_job(lambda: _run("fill_missing_images.py", ["--limit", "10"]),
                       "interval", minutes=5, id="image_fill")
else:
    # Production: google ratings at 3am (fills lat/lon too), images 4x daily
    _scheduler.add_job(lambda: _run("update_google_ratings.py", ["--batch", "200"]),
                       "cron", hour=3, minute=0, id="google_ratings")
    _scheduler.add_job(lambda: _run("fill_missing_images.py", ["--limit", "100"]),
                       "cron", hour="1,7,13,19", minute=30, id="image_fill")
    # Pre-warm Google Places SQLite cache for top-10 popular cities after a fresh deploy
    _scheduler.add_job(lambda: _run("warm_places_cache.py"),
                       "cron", hour=2, minute=30, id="warm_places_cache")

_scheduler.start()


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
