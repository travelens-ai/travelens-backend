import ast
import os
import pickle
import pandas as pd
from numpy.linalg import norm
from core.db import fetch_dicts
from core.config import (
    AZURE_OPENAI_MAX_OUTPUT_TOKENS,
    AZURE_OPENAI_MAX_OUTPUT_TOKENS_DAY,
    AZURE_OPENAI_MAX_OUTPUT_TOKENS_SKELETON,
)

try:
    from langfuse import get_client as _lf_get_client
except ImportError:
    def _lf_get_client(): return None

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))


def coerce_numeric(df, columns):
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def load_places_df(system):
    try:
        rows = fetch_dicts(
            """
            SELECT p.name AS name, p.type AS type,
                   c.name AS city, st.name AS state,
                   p.rating AS rating, p.num_ratings AS [no of rating],
                   p.best_month AS [best month to visit],
                   p.famous_activities AS [famous activities],
                   p.famous_activities_rating AS [famous activities with rating],
                   (SELECT TOP 1 i.image_name FROM place_image_map pim
                      JOIN images i ON pim.image_id = i.id
                     WHERE pim.place_id = p.id
                     ORDER BY CASE WHEN i.image_name LIKE '%\\_0.webp' ESCAPE '\\' THEN 0 ELSE 1 END) AS image,
                   p.dist_airport AS [distance from airport],
                   p.dist_bus_stand AS [distance from bus stand],
                   p.dist_railway AS [distance from railway station],
                   p.prefer_friends AS prefer_friends,
                   p.prefer_couple AS prefer_couple,
                   p.prefer_family_children AS prefer_family_children,
                   p.prefer_family_no_children AS prefer_family_no_children,
                   p.opening_hours AS opening_hours,
                   p.website_uri AS website_uri,
                   p.phone_number AS phone_number,
                   p.place_types AS place_types,
                   p.lat AS lat, p.lon AS lon,
                   p.display_name AS display_name,
                   p.google_rating AS google_rating,
                   p.google_rating_count AS google_rating_count,
                   p.primary_type_name AS primary_type_name,
                   p.short_formatted_address AS short_formatted_address,
                   p.editorial_summary AS editorial_summary,
                   p.review_summary AS review_summary,
                   p.google_maps_uri AS google_maps_uri,
                   p.google_photo_refs AS google_photo_refs,
                   p.business_status AS business_status
            FROM places p
            LEFT JOIN cities c ON p.city_id = c.id
            LEFT JOIN states st ON c.state_id = st.id
            """
        )
        if rows:
            df = pd.DataFrame(rows)
            df = coerce_numeric(df, ['rating', 'no of rating', 'google_rating', 'google_rating_count'])
            print(f"  Loaded {len(df)} places from DB.")
            return df
        print("  places table empty — falling back to CSV.")
    except Exception as e:
        print(f"  DB read for places failed ({e}) — falling back to CSV.")
    return pd.read_csv(os.path.join(_PROJECT_ROOT, 'indian_travel_places.csv'))


def load_hotels_df(system):
    try:
        rows = fetch_dicts(
            """SELECT address, area, city, state, country, hotel_star_rating,
                      pageurl, property_name, property_type, site_review_rating
               FROM hotels"""
        )
        if rows:
            df = pd.DataFrame(rows)
            df = coerce_numeric(df, ['hotel_star_rating', 'site_review_rating'])
            print(f"  Loaded {len(df)} hotels from DB.")
            return df
        print("  hotels table empty — falling back to CSV.")
    except Exception as e:
        print(f"  DB read for hotels failed ({e}) — falling back to CSV.")
    return pd.read_csv(os.path.join(_PROJECT_ROOT, 'indian_hotels.csv'))


def load_restaurants_df(system):
    try:
        rows = fetch_dicts(
            """SELECT name AS Name, location AS Location, locality AS Locality,
                      city AS City, cuisine AS Cuisine, rating AS Rating,
                      votes AS Votes, cost AS Cost
               FROM restaurants"""
        )
        if rows:
            df = pd.DataFrame(rows)
            df = coerce_numeric(df, ['Rating', 'Votes', 'Cost'])
            print(f"  Loaded {len(df)} restaurants from DB.")
            return df
        print("  restaurants table empty — falling back to CSV.")
    except Exception as e:
        print(f"  DB read for restaurants failed ({e}) — falling back to CSV.")
    return pd.read_csv(os.path.join(_PROJECT_ROOT, 'indian_restaurants.csv'))


def preprocess_places_data(system, df):
    df = df.rename(columns={'name': 'placename'})
    df = df.drop_duplicates(subset=['placename'], keep='first')

    if 'google_rating' in df.columns:
        df['rating'] = df['google_rating'].where(df['google_rating'].notna(), df['rating'])
    if 'google_rating_count' in df.columns:
        df['no of rating'] = df['google_rating_count'].where(
            df['google_rating_count'].notna(), df['no of rating']
        )

    df['famous activities'] = df['famous activities'].str.replace('/', ',')
    df['best month to visit'] = df['best month to visit'].str.replace('/', ',')

    df['famous activities with rating'] = df['famous activities with rating'].apply(
        lambda x: ast.literal_eval(x) if isinstance(x, str) and x.startswith('{') else {}
    )

    if 'display_name' in df.columns:
        df['effective_name'] = df['display_name'].where(
            df['display_name'].notna() & (df['display_name'].astype(str).str.strip() != ''),
            df['placename']
        )
    else:
        df['effective_name'] = df['placename']

    _pref_map = {
        'prefer_friends': 'friends',
        'prefer_couple': 'couples',
        'prefer_family_children': 'family with kids',
        'prefer_family_no_children': 'family without kids',
    }

    def _suitable_for_label(row):
        labels = [label for col, label in _pref_map.items() if col in row and row[col]]
        return ', '.join(labels) if labels else ''

    df['suitable_for'] = df.apply(_suitable_for_label, axis=1)
    return df


def setup_models(system):
    try:
        print("Configuring Azure OpenAI client...")
        system.max_tokens = AZURE_OPENAI_MAX_OUTPUT_TOKENS
        system.max_tokens_day = AZURE_OPENAI_MAX_OUTPUT_TOKENS_DAY
        system.max_tokens_skeleton = AZURE_OPENAI_MAX_OUTPUT_TOKENS_SKELETON
        system.temperature = 0.5
        system.top_p = 0.5
        print("Azure OpenAI client configured successfully.")
    except Exception as e:
        print(f"Error configuring Azure OpenAI: {str(e)}")
        raise


def load_data(system):
    try:
        print("Loading data...")
        system.places_df = load_places_df(system)
        system.hotels_df = load_hotels_df(system)
        system.restaurants_df = load_restaurants_df(system)

        print("Data loaded successfully.")
        system.places_df = preprocess_places_data(system, system.places_df)

        system.activity_embeddings = {}
        system.place_type_embeddings = {}
        try:
            with open(os.path.join(_PROJECT_ROOT, 'activity_embeddings.pkl'), 'rb') as f:
                system.activity_embeddings = pickle.load(f)
            with open(os.path.join(_PROJECT_ROOT, 'place_type_embeddings.pkl'), 'rb') as f:
                system.place_type_embeddings = pickle.load(f)
            print("Loaded existing embeddings from pickle files")
        except FileNotFoundError:
            print("Generating new embeddings...")
            lf = _lf_get_client()
            _lf_trace = lf.trace(name="startup_embedding_generation", tags=["startup"]) if lf else None
            try:
                all_activities = set()
                all_place_types = set()
                for _, row in system.places_df.iterrows():
                    activities = row['famous activities with rating'].keys()
                    all_activities.update(activities)
                    all_place_types.add(row['type'])

                activity_list = list(all_activities)
                for i in range(0, len(activity_list), 50):
                    batch = activity_list[i:i + 50]
                    embeddings = system._encode(batch)
                    for text, emb in zip(batch, embeddings):
                        system.activity_embeddings[text] = emb / norm(emb)
                    print(f"  Encoded activities {i + 1}-{min(i + 50, len(activity_list))} of {len(activity_list)}")

                place_type_list = list(all_place_types)
                for i in range(0, len(place_type_list), 50):
                    batch = place_type_list[i:i + 50]
                    embeddings = system._encode(batch)
                    for text, emb in zip(batch, embeddings):
                        system.place_type_embeddings[text] = emb / norm(emb)

                with open(os.path.join(_PROJECT_ROOT, 'activity_embeddings.pkl'), 'wb') as f:
                    pickle.dump(system.activity_embeddings, f)
                with open(os.path.join(_PROJECT_ROOT, 'place_type_embeddings.pkl'), 'wb') as f:
                    pickle.dump(system.place_type_embeddings, f)
                print("Generated and saved new embeddings")
            finally:
                if _lf_trace:
                    _lf_trace.update(output={
                        "activities": len(system.activity_embeddings),
                        "place_types": len(system.place_type_embeddings),
                    })

    except FileNotFoundError as e:
        raise Exception(f"Required data files not found: {str(e)}")
