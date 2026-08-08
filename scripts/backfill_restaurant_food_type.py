"""Classify each restaurant as 'veg', 'non-veg', or 'both' using Azure OpenAI.

Fetches all rows where food_type IS NULL, sends them in batches to the LLM,
and writes the result back. Idempotent — safe to re-run after any bulk import.

Usage:
    venv/bin/python scripts/backfill_restaurant_food_type.py            # all unclassified
    venv/bin/python scripts/backfill_restaurant_food_type.py --limit 60 # first 60 only (dry-run check)
    venv/bin/python scripts/backfill_restaurant_food_type.py --batch 50 # larger batches
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

VALID_FOOD_TYPES = {"veg", "non-veg", "both"}

_SYSTEM_PROMPT = (
    "You are a food expert familiar with Indian restaurants. "
    "For each restaurant, classify its food_type as exactly one of: veg, non-veg, both. "
    "Rules: "
    "'veg' = serves only vegetarian food (no meat, no fish, no eggs). "
    "Signals: name contains 'Pure Veg', 'Shakahari', 'Vegetarian', 'Sattvic', 'Veg'; "
    "cuisine is purely South Indian / North Indian / Rajasthani sweets / Jain / Bakery with no non-veg cuisines. "
    "'non-veg' = primarily or exclusively non-vegetarian. "
    "Signals: cuisine includes Mughlai, Biryani, Chicken, Mutton, Seafood, Fish, Kebab, BBQ, Tandoori meat; "
    "name contains 'Chicken', 'Mutton', 'Fish', 'Biryani House', 'Non-Veg'. "
    "'both' = menu has both vegetarian and non-vegetarian options (most multi-cuisine restaurants). "
    "When in doubt between non-veg and both, prefer 'both'. "
    "Respond ONLY with a raw JSON array (no markdown, no explanation): "
    '[{"id": <id>, "food_type": "<veg|non-veg|both>"}, ...]'
)

_USER_TEMPLATE = (
    "Classify each restaurant below:\n\n{restaurants_json}"
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


def load_unclassified(cursor, limit=None):
    if limit:
        q = f"SELECT TOP ({limit}) id, name, cuisine FROM dbo.restaurants WHERE food_type IS NULL"
    else:
        q = "SELECT id, name, cuisine FROM dbo.restaurants WHERE food_type IS NULL"
    cursor.execute(q)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def call_llm(client, deployment, batch):
    summaries = [
        {"id": r["id"], "name": r["name"] or "", "cuisine": r["cuisine"] or ""}
        for r in batch
    ]
    user_msg = _USER_TEMPLATE.format(restaurants_json=json.dumps(summaries, ensure_ascii=False))
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
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    data = json.loads(content)
    if isinstance(data, list):
        return data
    for key in data:
        if isinstance(data[key], list):
            return data[key]
    return []


def write_food_types(cursor, results):
    updated = 0
    for item in results:
        rid = item.get("id")
        ft = str(item.get("food_type") or "").strip().lower()
        if not rid or ft not in VALID_FOOD_TYPES:
            continue
        cursor.execute(
            "UPDATE dbo.restaurants SET food_type = ? WHERE id = ?",
            (ft, rid),
        )
        updated += 1
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=30, help="Restaurants per LLM call (default 30)")
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

    rows = load_unclassified(cursor, limit=args.limit)
    print(f"Found {len(rows)} unclassified restaurants.")
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
        print(f"Batch {batch_num}/{total_batches}: classifying {len(batch)} restaurants …", end=" ", flush=True)
        try:
            results = call_llm(client, deployment, batch)
            updated = write_food_types(cursor, results)
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
