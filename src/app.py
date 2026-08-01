import atexit
import multiprocessing as mp
import os
import socket
import subprocess
import sys
import time as _time

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify
from flask_cors import CORS
from flasgger import Swagger

# load_dotenv is called inside core/config.py — must be imported before anything else
from core import config as _config_init  # noqa: F401 (triggers load_dotenv)
from core.config import PORT

import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,  # 10% of requests for performance tracing
        send_default_pii=False,
    )
    print("[sentry] initialized")
from core.langfuse_client import get_langfuse as _get_langfuse

from core.swagger_config import swagger_template, swagger_config
from core.db import init_db_async
from auth import auth_bp
from auth.admin import admin_auth_bp
from auth.guard import authenticate_request
from features.admin import admin_bp
from features.itinerary import itinerary_bp
from features.itinerary.service import initialize_recommender, is_initialized
from features.places import places_bp
from features.places.service import load_city_coords, warm_enrich_cache
from features.config.service import warm_config_cache
from features.images import images_bp
from features.user import user_bp
from features.weather import weather_bp
from features.search import search_bp
from features.config import config_bp
from features.feedback import feedback_bp
from features.messaging import messaging_bp

app = Flask(__name__)

# CORS: allow the admin/frontend origins to call the API (incl. Authorization
# header + credentials). Origins come from CORS_ORIGINS (comma-separated) so
# prod can override without a code change; defaults cover the travelens domains.
_cors_origins = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS",
        "https://api.travelens.in,https://travelens.in,https://www.travelens.in",
    ).split(",")
    if o.strip()
]
CORS(
    app,
    resources={r"/*": {"origins": _cors_origins}},
    supports_credentials=True,
    allow_headers=["Content-Type", "Authorization", "X-Device-Token"],
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)

try:
    _server_ip = socket.gethostbyname(socket.gethostname())
except Exception:
    _server_ip = "unknown"


@app.before_request
def _require_auth():
    # Global auth gate: logged-in user JWT (Authorization: Bearer) OR device
    # JWT (X-Device-Token). Auth/login, health and docs paths are exempt.
    result = authenticate_request()
    if result is not None:
        return result


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
app.register_blueprint(feedback_bp)
app.register_blueprint(messaging_bp)
app.register_blueprint(admin_auth_bp)
app.register_blueprint(admin_bp)

init_db_async()
initialize_recommender()
load_city_coords()
warm_enrich_cache()
warm_config_cache()

# ---------------------------------------------------------------------------
# Background data-fill cron jobs (APScheduler)
# Each script exits immediately when nothing is left to fill — no wasted calls.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")

def _run(script, args=[]):
    path = os.path.join(_SCRIPTS_DIR, script)
    job_name = os.path.splitext(script)[0]
    lf = _get_langfuse()
    trace = lf.trace(
        name=f"cron.{job_name}",
        input={"script": script, "args": args},
        tags=["cron"],
    ) if lf else None

    t0 = _time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, path] + args,
            cwd=os.path.dirname(_SCRIPTS_DIR),
            capture_output=True,
            text=True,
        )
        duration = round(_time.monotonic() - t0, 2)
        success = proc.returncode == 0
        output = (proc.stdout or "")[-2000:] or (proc.stderr or "")[-2000:]
        if trace:
            trace.update(
                output={"returncode": proc.returncode, "output": output, "duration_s": duration},
                tags=["cron"] if success else ["cron", "cron-error"],
            )
        if not success:
            print(f"[cron] {job_name} exited {proc.returncode} after {duration}s\n{output}")
    except Exception as exc:
        duration = round(_time.monotonic() - t0, 2)
        if trace:
            trace.update(
                output={"error": str(exc), "duration_s": duration},
                tags=["cron", "cron-error"],
            )
        print(f"[cron] {job_name} failed to launch: {exc}")

_scheduler = BackgroundScheduler(daemon=True)
_CRON_MODE = os.getenv("CRON_MODE", "off")  # "prod" | "test" | "off"

if _CRON_MODE == "off":
    print("[cron] DISABLED — set CRON_MODE=prod or CRON_MODE=test to enable")
elif _CRON_MODE == "test":
    # Test mode: all jobs fire every 5 minutes with small batches
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
      503:
        description: Service unhealthy (DB unreachable)
    """
    db_ok = False
    try:
        from core.db import get_connection
        conn = get_connection()
        conn.cursor().execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception:
        pass

    status = "healthy" if db_ok else "degraded"
    http_code = 200 if db_ok else 503
    return jsonify({
        "status": status,
        "initialized": is_initialized(),
        "db": "ok" if db_ok else "unreachable",
    }), http_code


def _flush_langfuse():
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception:
        pass

atexit.register(_flush_langfuse)


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
