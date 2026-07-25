"""
Enrich cities that have state_id=NULL by resolving them to (city, state, country) via
the Azure OpenAI LLM, then upserting the country→state→city chain in the DB.

Also handles:
- Sub-localities (e.g. "assi ghat") → remapped to their parent city ("varanasi")
- Bad noise cities — same resolution path, places get remapped to correct city_id
- Nominatim fallback when LLM returns null/unknown

Usage:
    PYTHONPATH=src python scripts/enrich_cities.py [--dry-run] [--batch 50]
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.config import (  # noqa: F401,F403 — loads .env before everything else
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_CHAT_DEPLOYMENT,
)
from core.db import new_connection

try:
    from openai import AzureOpenAI
except ImportError:
    print("openai package not installed — pip install openai")
    sys.exit(1)

_client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_version=AZURE_OPENAI_API_VERSION,
)

_SYSTEM_PROMPT = (
    "You are a geography expert. Given a place name, return its canonical city, "
    "state/province, and country as JSON. "
    "If the place is a neighbourhood, landmark, area, or sub-locality rather than a city itself, "
    "return the city it belongs to. "
    "Return {\"city\": null, \"state\": null, \"country\": null} only if you genuinely "
    "cannot determine the location. Never guess."
)

_EXAMPLES = (
    'Place: "assi ghat"\n'
    'Return: {"city": "Varanasi", "state": "Uttar Pradesh", "country": "India"}\n\n'
    'Place: "trastevere"\n'
    'Return: {"city": "Rome", "state": "Lazio", "country": "Italy"}\n\n'
    'Place: "malabar hill"\n'
    'Return: {"city": "Mumbai", "state": "Maharashtra", "country": "India"}\n\n'
    'Place: "bandra west"\n'
    'Return: {"city": "Mumbai", "state": "Maharashtra", "country": "India"}\n\n'
    'Place: "dal lake"\n'
    'Return: {"city": "Srinagar", "state": "Jammu and Kashmir", "country": "India"}\n\n'
)


def _resolve_via_llm(city_name: str) -> dict | None:
    """Ask Azure OpenAI to resolve city_name → {city, state, country}. Returns None on failure."""
    user_msg = _EXAMPLES + f'Place: "{city_name}"\nReturn:'
    try:
        resp = _client.responses.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT,
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_output_tokens=200,
            reasoning={"effort": "low"},
            text={"format": {"type": "json_object"}},
        )
        raw = resp.output_text.strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        city = (data.get("city") or "").strip() or None
        state = (data.get("state") or "").strip() or None
        country = (data.get("country") or "").strip() or None
        if city and country:
            return {"city": city, "state": state, "country": country}
    except Exception as e:
        print(f"  [llm] error for '{city_name}': {e}")
    return None


def _resolve_via_nominatim(city_name: str) -> dict | None:
    """Fallback: geocode via Nominatim, parse display_name segments."""
    try:
        from integrations.api_integrations import NominatimClient
        result = NominatimClient().geocode(city_name)
        if not result:
            return None
        parts = [p.strip() for p in result["full_address"].split(",")]
        # Nominatim display_name: "Name, ..., State, Country"
        # Filter out purely numeric segments (postal codes, PIN codes)
        parts = [p for p in parts if p and not p.isdigit()]
        country = parts[-1] if len(parts) >= 1 else None
        state = parts[-2] if len(parts) >= 2 else None
        city = parts[0] if len(parts) >= 1 else None
        # Reject if country/state look like garbage (too short or non-alpha)
        if city and country and len(country) > 2 and country.replace(" ", "").isalpha():
            return {"city": city, "state": state, "country": country}
    except Exception as e:
        print(f"  [nominatim] error for '{city_name}': {e}")
    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_or_create_country(cursor, country_name: str) -> int:
    cursor.execute("SELECT id FROM country WHERE LOWER(name) = LOWER(?)", (country_name,))
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute(
        "INSERT INTO country (name) OUTPUT INSERTED.id VALUES (?)",
        (country_name.title(),),
    )
    cid = int(cursor.fetchone()[0])
    print(f"    [db] created country '{country_name.title()}' id={cid}")
    return cid


def _get_or_create_state(cursor, state_name: str, country_id: int) -> int:
    cursor.execute(
        "SELECT id FROM states WHERE LOWER(name) = LOWER(?) AND country_id = ?",
        (state_name, country_id),
    )
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute(
        "INSERT INTO states (name, country_id) OUTPUT INSERTED.id VALUES (?, ?)",
        (state_name.title(), country_id),
    )
    sid = int(cursor.fetchone()[0])
    print(f"    [db] created state '{state_name.title()}' id={sid} country_id={country_id}")
    return sid


def _get_or_create_city(cursor, city_name: str, state_id: int) -> int:
    cursor.execute("SELECT id FROM cities WHERE LOWER(name) = LOWER(?)", (city_name,))
    row = cursor.fetchone()
    if row:
        city_id = row[0]
        # Ensure state_id is set on the canonical city row too
        cursor.execute(
            "UPDATE cities SET state_id = ? WHERE id = ? AND state_id IS NULL",
            (state_id, city_id),
        )
        return city_id
    cursor.execute(
        "INSERT INTO cities (name, state_id) OUTPUT INSERTED.id VALUES (?, ?)",
        (city_name.lower(), state_id),
    )
    cid = int(cursor.fetchone()[0])
    print(f"    [db] created city '{city_name.lower()}' id={cid} state_id={state_id}")
    return cid


# ---------------------------------------------------------------------------
# Main enrichment logic
# ---------------------------------------------------------------------------

def enrich(dry_run: bool, batch: int):
    conn = new_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, name FROM cities WHERE state_id IS NULL ORDER BY id"
    )
    all_unlinked = cursor.fetchall()
    print(f"Found {len(all_unlinked)} unlinked cities. Processing up to {batch}.")

    to_process = all_unlinked[:batch]
    skipped, enriched, failed = 0, 0, 0

    for city_id, city_name in to_process:
        print(f"\n[{city_id}] '{city_name}'")

        resolved = _resolve_via_llm(city_name)
        time.sleep(0.8)  # stay within Azure OpenAI quota

        if not resolved:
            print(f"  [llm] no result — trying Nominatim fallback")
            resolved = _resolve_via_nominatim(city_name)

        if not resolved:
            print(f"  SKIP — could not resolve")
            failed += 1
            continue

        r_city = resolved["city"]
        r_state = resolved["state"]
        r_country = resolved["country"]
        print(f"  resolved → city='{r_city}', state='{r_state}', country='{r_country}'")

        if dry_run:
            enriched += 1
            continue

        try:
            country_id = _get_or_create_country(cursor, r_country)
            state_name = r_state or r_country  # fall back to country name as state
            state_id = _get_or_create_state(cursor, state_name, country_id)

            canonical_city_id = _get_or_create_city(cursor, r_city, state_id)

            # If the stored city is a sub-locality (different name from resolved city),
            # remap its linked places to the canonical city row.
            if city_name.lower() != r_city.lower():
                cursor.execute(
                    "SELECT COUNT(*) FROM places WHERE city_id = ?", (city_id,)
                )
                place_count = cursor.fetchone()[0]
                if place_count:
                    cursor.execute(
                        "UPDATE places SET city_id = ? WHERE city_id = ?",
                        (canonical_city_id, city_id),
                    )
                    print(f"  [db] remapped {place_count} place(s) from city_id={city_id} → {canonical_city_id}")

            # Update the stored (possibly sub-locality) city row's state_id so it's
            # no longer orphaned, even if its places were remapped.
            cursor.execute(
                "UPDATE cities SET state_id = ? WHERE id = ?",
                (state_id, city_id),
            )

            conn.commit()
            enriched += 1
        except Exception as e:
            print(f"  [db] error: {e}")
            conn.rollback()
            failed += 1

    cursor.close()
    conn.close()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Done. enriched={enriched} failed={failed} skipped={skipped}")
    print(f"Remaining unlinked: {len(all_unlinked) - batch} (run again with --batch to continue)")


def main():
    parser = argparse.ArgumentParser(description="Enrich cities with state_id=NULL via LLM")
    parser.add_argument("--dry-run", action="store_true", help="Print resolutions without writing to DB")
    parser.add_argument("--batch", type=int, default=50, help="Max cities to process (default 50)")
    args = parser.parse_args()
    enrich(dry_run=args.dry_run, batch=args.batch)


if __name__ == "__main__":
    main()
