import os
import pickle
import pandas as pd

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))


def schedule_similar_places(system):
    pass  # no-op — similar_places now lives in DB, no pkl warmup needed


def update_similar_places(system):
    pass  # no-op — kept for any remaining call sites; DB is source of truth now


def schedule_popular_destination(system):
    set_popular_destination(system)


def set_popular_destination(system):
    try:
        C = system.places_df['rating'].mean()
        system.places_df['weighted_rating'] = system.places_df.apply(
            lambda x: system.weighted_place_rating(x, C), axis=1
        )
        top_destinations = system.places_df.sort_values('weighted_rating', ascending=False).head(10)
        top_destinations = top_destinations.fillna('')

        with open(os.path.join(_PROJECT_ROOT, 'popular_destination.csv'), 'w') as f:
            f.truncate(0)
        top_destinations.to_csv(os.path.join(_PROJECT_ROOT, 'popular_destination.csv'), index=False)

        with open(os.path.join(_PROJECT_ROOT, 'popular_destination.pkl'), 'wb') as f:
            pickle.dump(top_destinations, f)
        print("Popular destinations saved successfully.")
    except Exception as e:
        print(f"Error generating popular destinations: {str(e)}")


def get_popular_destination(system):
    try:
        from features.places.service import query_popular
        return query_popular()
    except Exception as e:
        print(f"Error fetching popular destinations: {e}")
        return []


def get_similar_places(system):
    """Return {city_name: {description, price_range}} from DB."""
    try:
        from core.db import fetch_dicts
        rows = fetch_dicts(
            "SELECT c.name AS placename, sp.description, sp.price_range "
            "FROM similar_places sp JOIN cities c ON sp.city_id = c.id"
        )
        return {r["placename"]: r for r in rows}
    except Exception as e:
        print(f"[get_similar_places] DB error: {e}")
        return {}


def save_similar_places(system, similar_places):
    """Upsert LLM-generated similar places into the DB, deduplicating via city_id."""
    try:
        from core.db import new_connection
        from models.recommendation.image_helpers import _candidate_city_keys
        conn = new_connection()
        cursor = conn.cursor()
        inserted = 0
        for place in similar_places:
            placename = str(place.get('placename') or '').strip()
            if not placename:
                continue
            description = str(place.get('description') or '').strip() or None
            price_range = str(place.get('price_estimated_range') or '').strip() or None

            # Resolve to a canonical city_id
            city_id = None
            for key in _candidate_city_keys(placename):
                cursor.execute("SELECT id FROM cities WHERE LOWER(name) = LOWER(?)", (key,))
                row = cursor.fetchone()
                if row:
                    city_id = row[0]
                    break

            if city_id is None:
                continue  # compound/international name — can't deduplicate, skip

            cursor.execute(
                """
                MERGE similar_places AS tgt
                USING (SELECT ? AS city_id) AS src ON tgt.city_id = src.city_id
                WHEN NOT MATCHED THEN
                    INSERT (city_id, description, price_range)
                    VALUES (?, ?, ?);
                """,
                (city_id, city_id, description, price_range),
            )
            if cursor.rowcount:
                inserted += 1

        conn.commit()
        cursor.close()
        conn.close()
        if inserted:
            print(f"[save_similar_places] inserted {inserted} new cities.")
    except Exception as e:
        print(f"[save_similar_places] error: {e}")
