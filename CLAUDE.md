# CLAUDE.md

Guidance for Claude Code in this repo. Keep this file **concise and stable** — it is
sent with every request and cached, so infrequent edits keep the cache warm and cut token
usage. Facts here save re-exploring the codebase each session.

## What this is

TraveLens backend — a Flask API for travel itinerary recommendations, place/hotel/restaurant
search, weather, auth, and AI image generation. Served via gunicorn (`src.app:app`).

## Layout

- `src/app.py` — Flask app entrypoint; registers blueprints, starts APScheduler, `init_db_async`.
- `src/core/` — cross-cutting: `config.py` (env via dotenv), `db.py` (Azure SQL pool),
  `images.py`, `ads.py`, `swagger_config.py`.
- `src/features/<name>/` — one package per feature (`itinerary`, `places`, `search`, `user`,
  `weather`, `images`, `config`), each with `routes.py` (blueprint) + `service.py` (logic).
- `src/auth/` — JWT + Google sign-in + email OTP.
- `src/integrations/` — `api_integrations.py` (external APIs), `generate_images.py` (Google GenAI).
- `src/models/` — recommendation model code.
- `scripts/` — standalone ops/cron jobs (image backfill, rating updates, CSV→DB migration).
- `migrations/` — one-off schema change scripts (run manually).

## Stack

- **DB**: Azure SQL via `pyodbc` + `SQLAlchemy` QueuePool with Azure AD token auth
  (`DefaultAzureCredential`). Borrow with `core.db.get_connection()`; caller **must** `close()`.
- **LLM/AI**: Azure OpenAI (chat + embeddings) via the `openai` SDK; Google GenAI for image
  generation. Config in `core/config.py` (`AZURE_OPENAI_*`, endpoint normalization helper).
- **Data**: pandas + numpy; precomputed `.pkl` embeddings/coords at repo root (gitignored).

## Running

- Local: `PYTHONPATH=src venv/bin/python -m src.app` (or use the `/run` skill).
- Prod: `Procfile` → `gunicorn --workers 1 --threads 4 --timeout 120 src.app:app`.
- Env: `.env` (gitignored). `core/config.py` calls `load_dotenv()` on import — import it first.

## Conventions

- New endpoints: add to the relevant `features/<name>/routes.py`, logic in `service.py`,
  register the blueprint in `app.py`.
- DB access goes through `core.db` — do not open bare `pyodbc` connections.
- Docstrings on non-obvious code explain **why** (see `_normalize_azure_endpoint`); match that.
- Secrets stay in `.env`; never hardcode keys.

## Token / cache efficiency

- Prefer targeted `grep`/Glob + reading specific line ranges over reading whole large files
  (embeddings `.pkl`, CSVs, and `generated_images/` are large — never dump them).
- Trust this file instead of re-scanning layout each session; update it only when structure
  actually changes.
