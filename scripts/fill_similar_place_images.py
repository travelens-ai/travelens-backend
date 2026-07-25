"""
Fill missing images in similar_places.csv by querying the DB for the
top-rated tourist spot in each city and using its existing CDN image.

No new image downloads or CDN uploads — only resolves images already in the DB.

Run from project root:
    PYTHONPATH=src python3 scripts/fill_similar_place_images.py           # fill all
    PYTHONPATH=src python3 scripts/fill_similar_place_images.py --dry-run # preview only
    PYTHONPATH=src python3 scripts/fill_similar_place_images.py --limit 10
"""

import argparse
import os
import sys

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'src'))

from models.recommendation.image_helpers import get_city_image
from models.recommendation.popular_places import update_similar_places


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing')
    parser.add_argument('--limit', type=int, default=0, help='Process at most N empty rows')
    args = parser.parse_args()

    csv_path = os.path.join(_PROJECT_ROOT, 'similar_places.csv')
    df = pd.read_csv(csv_path)

    empty_mask = df['image'].isna() | (df['image'].astype(str).str.strip() == '')
    empty_rows = df[empty_mask].copy()

    total_empty = len(empty_rows)
    print(f"Total rows: {len(df)} | Empty image: {total_empty} | Has image: {len(df) - total_empty}")

    if total_empty == 0:
        print("Nothing to do.")
        return

    if args.limit > 0:
        empty_rows = empty_rows.head(args.limit)
        print(f"Processing first {len(empty_rows)} empty rows (--limit {args.limit})")

    filled = 0
    for idx, row in empty_rows.iterrows():
        placename = str(row['placename']).strip()
        state = str(row.get('state', '') or '').strip()
        img = get_city_image(None, placename, state)
        status = f"  {placename!r:35s} -> {img!r}" if img else f"  {placename!r:35s} -> (no match)"
        print(status)
        if img and not args.dry_run:
            df.at[idx, 'image'] = img
            filled += 1

    if not args.dry_run:
        df.to_csv(csv_path, index=False)
        print(f"\nWrote {filled} images to {csv_path}")
        update_similar_places(None)
        print("similar_places.pkl regenerated.")
    else:
        print(f"\nDry run — would fill {sum(1 for _, r in empty_rows.iterrows() if get_city_image(None, str(r['placename']).strip(), str(r.get('state','') or '').strip()))} rows")


if __name__ == '__main__':
    main()
