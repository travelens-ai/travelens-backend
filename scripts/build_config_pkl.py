"""Snapshot the /configs lookup data from the DB into .pkl files at repo root.

The /configs endpoint serves four datasets that live in the database:
    group_types       -> group_types.pkl        (list[str])
    food_preferences  -> food_preferences.pkl    (list[str])
    activities        -> activities.pkl          (list[dict{id,name,icon}])
    popular_states    -> popular_states.pkl      (list[dict], top 10 by ratings)

At request time the config service reads these pkls instead of hitting the DB,
so /configs serves entirely from local files. This script regenerates the
snapshots; run it after the underlying tables change. The build logic itself
lives in features.config.service.build_config_snapshots (single source of
truth) — this is just a CLI wrapper, also wired as a cron job in app.py.

Run from project root (needs an authenticated Azure session for the DB):
    venv/bin/python scripts/build_config_pkl.py
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main():
    from features.config.service import build_config_snapshots
    print("[build_config_pkl] rebuilding all snapshots...")
    build_config_snapshots(only_missing=False)
    print("[build_config_pkl] done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[build_config_pkl] FAILED: {e}", file=sys.stderr)
        sys.exit(1)
