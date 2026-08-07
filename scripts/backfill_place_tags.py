"""Backfill suitable_trip_types and suitable_group_types for existing places.

Loads all places WHERE suitable_trip_types IS NULL, sends them in batches of 30
to Azure OpenAI, and writes the JSON-array tags back to the DB.

Usage:
    venv/bin/python scripts/backfill_place_tags.py              # all untagged
    venv/bin/python scripts/backfill_place_tags.py --batch 50   # first 50 only (dry-run check)
    venv/bin/python scripts/backfill_place_tags.py --limit 50   # process only 50 rows total
"""
import argparse
import json
import os
import struct
import sys
import time

import pyodbc
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from core.config import (  # noqa: E402
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_CHAT_DEPLOYMENT,
)

_SQL_COPT_SS_ACCESS_TOKEN = 1256

VALID_TRIP_TYPES = [
    "leisure", "honeymoon", "adventure", "spiritual", "pilgrimage",
    "family", "workation", "wellness", "backpacking", "weekend_getaway",
]
VALID_GROUP_TYPES = [
    "couples", "friends", "family_with_children",
    "family_without_children", "solo",
]

_SYSTEM_PROMPT = (
    "You are a travel expert. For each place given, return suitable_trip_types and "
    "suitable_group_types based purely on the place's character. "
    "suitable_trip_types must be a subset of: " + ", ".join(VALID_TRIP_TYPES) + ". "
    "suitable_group_types must be a subset of: " + ", ".join(VALID_GROUP_TYPES) + ". "
    "Respond ONLY with a raw JSON array (no markdown, no explanation) where each element is: "
    '{"id": <place_id>, "suitable_trip_types": [...], "suitable_group_types": [...]}'
)

_USER_TEMPLATE = (
    "Tag each place below. Use your knowledge of the place — "
    "e.g. a beach → honeymoon, leisure, weekend_getaway; a temple → spiritual, pilgrimage; "
    "a national park → adventure, family; a fort → leisure, weekend_getaway.\n\n"
    "Places:\n{places_json}"
)


def _connect():
    credential = DefaultAzureCredential()
    token = credential.get_token("https://database.windows.net//.default")
    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={os.getenv('AZURE_SQL_SERVER')};"
        f"DATABASE={os.getenv('AZURE_SQL_DATABASE')};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    )
    return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def load_untagged(cursor, limit=None):
    q = "SELECT id, display_name, name, type, famous_activities, editorial_summary FROM places WHERE suitable_trip_types IS NULL"
    if limit:
        q = f"SELECT TOP ({limit}) id, display_name, name, type, famous_activities, editorial_summary FROM places WHERE suitable_trip_types IS NULL"
    cursor.execute(q)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _build_place_summary(p):
    label = (p.get("display_name") or p.get("name") or "").strip()
    parts = []
    if p.get("type"):
        parts.append(f"type: {p['type']}")
    if p.get("famous_activities"):
        parts.append(f"activities: {p['famous_activities'][:120]}")
    if p.get("editorial_summary"):
        parts.append(f"summary: {p['editorial_summary'][:150]}")
    detail = "; ".join(parts)
    return {"id": p["id"], "name": label, "detail": detail}


def call_llm(client, deployment, batch):
    place_summaries = [_build_place_summary(p) for p in batch]
    user_msg = _USER_TEMPLATE.format(places_json=json.dumps(place_summaries, ensure_ascii=False))
    resp = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        temperature=1,
        max_completion_tokens=2000,
    )
    content = resp.choices[0].message.content.strip()
    # Strip markdown fences if present
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    data = json.loads(content)
    if isinstance(data, list):
        return data
    # Handle {"results": [...]} or any wrapper key
    for key in data:
        if isinstance(data[key], list):
            return data[key]
    return []


def _sanitize(tags, valid_set):
    if not isinstance(tags, list):
        return []
    return [t for t in tags if isinstance(t, str) and t in valid_set]


def write_tags(cursor, results):
    updated = 0
    for item in results:
        pid = item.get("id")
        if not pid:
            continue
        stt = _sanitize(item.get("suitable_trip_types", []), set(VALID_TRIP_TYPES))
        sgt = _sanitize(item.get("suitable_group_types", []), set(VALID_GROUP_TYPES))
        if not stt and not sgt:
            continue
        cursor.execute(
            "UPDATE places SET suitable_trip_types=?, suitable_group_types=? WHERE id=?",
            (json.dumps(stt), json.dumps(sgt), pid),
        )
        updated += 1
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=30, help="Places per LLM call (default 30)")
    parser.add_argument("--limit", type=int, default=None, help="Total rows to process (default all)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between LLM calls (default 1)")
    args = parser.parse_args()

    client = AzureOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    deployment = AZURE_OPENAI_CHAT_DEPLOYMENT

    conn = _connect()
    cursor = conn.cursor()

    rows = load_untagged(cursor, limit=args.limit)
    print(f"Found {len(rows)} untagged places.")
    if not rows:
        print("Nothing to do.")
        conn.close()
        return

    total_updated = 0
    errors = 0
    batch_size = args.batch

    for i in range(0, len(rows), batch_size):
        batch = rows[i: i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(rows) + batch_size - 1) // batch_size
        print(f"Batch {batch_num}/{total_batches}: tagging {len(batch)} places …", end=" ", flush=True)
        try:
            results = call_llm(client, deployment, batch)
            updated = write_tags(cursor, results)
            conn.commit()
            total_updated += updated
            print(f"updated {updated}")
        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1
            conn.rollback()

        if i + batch_size < len(rows):
            time.sleep(args.delay)

    cursor.close()
    conn.close()
    print(f"\nDone. {total_updated} rows updated, {errors} batch error(s).")


if __name__ == "__main__":
    main()
