from flask import json
import pandas as pd
import requests
import numpy as np
import ast
import re
import pickle
import textwrap
from openai import AzureOpenAI
from numpy import dot
from numpy.linalg import norm
import schedule
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from integrations.generate_images import ImageGenerator
from integrations.api_integrations import GooglePlacesClient, ImageSearchClient, NominatimClient
from core.db import fetch_dicts, new_connection, is_db_ready
import os
import random
import copy

# PKL files live in the project root (one level above src/)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


class ItenaryRecommendationSystem:
    def __init__(self, client, chat_deployment, embedding_deployment):
        """
        Initialize the Itinerary Recommendation System

        Args:
            client (AzureOpenAI): Azure OpenAI client instance
            chat_deployment (str): Azure deployment name for chat/completion model
            embedding_deployment (str): Azure deployment name for embedding model
        """
        self.client = client
        self.chat_deployment = chat_deployment
        self.embedding_deployment = embedding_deployment
        self.image_generator = None
        self.places_df = None
        self.hotels_df = None
        self.restaurants_df = None
        self.places_client = GooglePlacesClient()
        self.image_client = ImageSearchClient()
        self.geocoder = NominatimClient()

    def initialize(self):
        """Initialize models and load data"""
        self._setup_models()
        print("Models initialized successfully.")
        self._load_data()
        self.schedule_popular_destination()
        self.schedule_similar_places()
        return True
    
    def schedule_similar_places(self):
        self.update_similar_places()  # Trigger immediately for the first time
        # schedule.every(1).hour.do(self.update_similar_places)
        # while True:
        #     schedule.run_pending()
        #     time.sleep(1)
        
    def update_similar_places(self):
        """Schedule the set_similar_places function to run every 1 day"""
        try:
            # Load similar_places.csv into a DataFrame
            similar_places_df = pd.read_csv(os.path.join(_PROJECT_ROOT, 'similar_places.csv'))

            final_places = {}
            for row in similar_places_df.itertuples(index=False):
                final_places[row.placename] = row._asdict()      
            # Save the DataFrame to a pickle file
            with open(os.path.join(_PROJECT_ROOT, 'similar_places.pkl'), 'wb') as f:
                pickle.dump(final_places, f)

            print("similar_places.pkl generated or updated successfully.")
        except Exception as e:
            print(f"Error generating or updating similar_places.pkl: {str(e)}")
            
    def schedule_popular_destination(self):
        """Schedule the set_popular_destination function to run every 1 day"""
        self.set_popular_destination()  # Trigger immediately for the first time
        # schedule.every(1).day.do(self.set_popular_destination)
        # while True:
        #     schedule.run_pending()
        #     time.sleep(1)

    def set_popular_destination(self):
        """Generate and save the most popular top 10 destinations"""
        try:
            # Calculate average rating for all places
            C = self.places_df['rating'].mean()

            # Calculate weighted rating score for each place
            self.places_df['weighted_rating'] = self.places_df.apply(
            lambda x: self.weighted_place_rating(x, C), axis=1
            )

            # Sort by weighted rating and select top 10 destinations
            top_destinations = self.places_df.sort_values('weighted_rating', ascending=False).head(10)

            # Replace NaN values with empty strings
            top_destinations = top_destinations.fillna('')

            # Remove previous data from the CSV file if it exists
            with open(os.path.join(_PROJECT_ROOT, 'popular_destination.csv'), 'w') as f:
                f.truncate(0)

            # Save the top destinations to a CSV file
            top_destinations.to_csv(os.path.join(_PROJECT_ROOT, 'popular_destination.csv'), index=False)

            # Save the preprocessed DataFrame to a pickle file
            with open(os.path.join(_PROJECT_ROOT, 'popular_destination.pkl'), 'wb') as f:
                pickle.dump(top_destinations, f)

            print("Popular destinations saved successfully.")
        except Exception as e:
            print(f"Error generating popular destinations: {str(e)}")
        
    def get_popular_destination(self):
        """Return city-level popular destination cards from the DB."""
        try:
            from features.places.service import query_popular
            return query_popular()
        except Exception as e:
            print(f"Error fetching popular destinations: {e}")
            return []
    
    def get_similar_places(self):
        try:
            if os.path.exists(os.path.join(_PROJECT_ROOT, 'similar_places.pkl')) and os.path.getsize(os.path.join(_PROJECT_ROOT, 'similar_places.pkl')) > 0:
                with open(os.path.join(_PROJECT_ROOT, 'similar_places.pkl'), 'rb') as f:
                    similar_places = pickle.load(f)
                return similar_places
            else:
                print("similar_places.pkl is missing or empty.")
                return {}
        except FileNotFoundError:
            print("No popular destination found. Please run set_popular_destination() first.")
            return None

    def _encode(self, texts):
        result = self.client.embeddings.create(
            model=self.embedding_deployment,
            input=texts,
        )
        return [np.array(e.embedding) for e in result.data]

    def _setup_models(self):
        """Setup Azure OpenAI models"""
        try:
            print("Configuring Azure OpenAI client...")
            self.max_tokens = 8192
            self.temperature = 0.5
            self.top_p = 0.5
            print("Azure OpenAI client configured successfully.")
        except Exception as e:
            print(f"Error configuring Azure OpenAI: {str(e)}")
            raise

    @staticmethod
    def _coerce_numeric(df, columns):
        """Convert DB NULLs (None) to NaN so float()/mean() behave like the CSV
        path. Without this, float(None) raises TypeError downstream."""
        for col in columns:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df

    def _load_places_df(self):
        """Load places from the DB, reconstructing CSV-equivalent columns
        (city/state via joins). Falls back to the CSV if the DB is unavailable
        or returns no rows."""
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
                df = self._coerce_numeric(df, ['rating', 'no of rating', 'google_rating', 'google_rating_count'])
                print(f"  Loaded {len(df)} places from DB.")
                return df
            print("  places table empty — falling back to CSV.")
        except Exception as e:
            print(f"  DB read for places failed ({e}) — falling back to CSV.")
        return pd.read_csv(os.path.join(_PROJECT_ROOT, 'indian_travel_places.csv'))

    def _get_images_for_places(self, names):
        """Return {lowercased place name -> [image_name, ...]} for the given place
        names, pulling ALL images per place from place_image_map. Used to build the
        multi-image galleries in the itinerary response. Returns {} on any DB error."""
        names = [str(n).strip().lower() for n in names if str(n).strip()]
        if not names:
            return {}
        try:
            placeholders = ",".join(["?"] * len(names))
            rows = fetch_dicts(
                f"""SELECT LOWER(COALESCE(p.display_name, p.name)) AS display, LOWER(p.name) AS canonical, i.image_name AS image
                    FROM places p
                    JOIN place_image_map pim ON pim.place_id = p.id
                    JOIN images i ON pim.image_id = i.id
                    WHERE LOWER(p.display_name) IN ({placeholders})
                       OR (p.display_name IS NULL AND LOWER(p.name) IN ({placeholders}))
                    ORDER BY CASE WHEN i.image_name LIKE '%\\_0.webp' ESCAPE '\\' THEN 0 ELSE 1 END""",
                tuple(names) + tuple(names),
            )
        except Exception as e:
            print(f"  _get_images_for_places failed ({e})")
            return {}

        result = {}
        for row in rows:
            # Index by both display_name and canonical name so either lookup hits
            for key in {row["display"], row["canonical"]}:
                result.setdefault(key, [])
                if row["image"] and row["image"] not in result[key]:
                    result[key].append(row["image"])
        return result

    def _search_images_by_keywords(self, keywords, limit=5):
        """Fallback image lookup: when a place has no mapped image, search the
        `images` table by keyword. Image names encode `Place_City_State` (e.g.
        'Hawa_Mahal_Jaipur_Rajasthan...webp'), so we LIKE-match each keyword
        (place name, then city, then state) against image_name. Keywords are
        tried in the given order (most specific first) and results accumulate,
        deduped, until `limit` images are collected. Returns a list of
        image_name strings (possibly empty)."""
        found = []
        for raw in keywords:
            if len(found) >= limit:
                break
            kw = str(raw).strip() if raw is not None else ""
            if not kw:
                continue
            # Names use spaces; image_name uses underscores — normalize.
            token = kw.lower().replace(" ", "_")
            escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            try:
                rows = fetch_dicts(
                    "SELECT TOP (?) image_name FROM images WHERE LOWER(image_name) LIKE ? "
                    "ORDER BY CASE WHEN image_name LIKE '%\\_0.webp' ESCAPE '\\' THEN 0 ELSE 1 END, id",
                    (limit, "%" + escaped + "%"),
                )
            except Exception as e:
                print(f"  _search_images_by_keywords failed for {kw!r} ({e})")
                continue
            for row in rows:
                name = row["image_name"]
                if name and name not in found:
                    found.append(name)
                    if len(found) >= limit:
                        break
        return found

    def _search_image_by_keywords(self, keywords):
        """Single-image variant of _search_images_by_keywords: returns the first
        matching image_name, or None if nothing matches."""
        images = self._search_images_by_keywords(keywords, limit=1)
        return images[0] if images else None

    def _load_hotels_df(self):
        """Load hotels from the DB (columns already match the CSV). Falls back
        to the CSV if the DB is unavailable or returns no rows."""
        try:
            rows = fetch_dicts(
                """SELECT address, area, city, state, country, hotel_star_rating,
                          pageurl, property_name, property_type, site_review_rating
                   FROM hotels"""
            )
            if rows:
                df = pd.DataFrame(rows)
                df = self._coerce_numeric(df, ['hotel_star_rating', 'site_review_rating'])
                print(f"  Loaded {len(df)} hotels from DB.")
                return df
            print("  hotels table empty — falling back to CSV.")
        except Exception as e:
            print(f"  DB read for hotels failed ({e}) — falling back to CSV.")
        return pd.read_csv(os.path.join(_PROJECT_ROOT, 'indian_hotels.csv'))

    def _load_restaurants_df(self):
        """Load restaurants from the DB, aliasing snake_case back to the
        CSV-style capitalized column names the recommender expects. Falls back
        to the CSV if the DB is unavailable or returns no rows."""
        try:
            rows = fetch_dicts(
                """SELECT name AS Name, location AS Location, locality AS Locality,
                          city AS City, cuisine AS Cuisine, rating AS Rating,
                          votes AS Votes, cost AS Cost
                   FROM restaurants"""
            )
            if rows:
                df = pd.DataFrame(rows)
                df = self._coerce_numeric(df, ['Rating', 'Votes', 'Cost'])
                print(f"  Loaded {len(df)} restaurants from DB.")
                return df
            print("  restaurants table empty — falling back to CSV.")
        except Exception as e:
            print(f"  DB read for restaurants failed ({e}) — falling back to CSV.")
        return pd.read_csv(os.path.join(_PROJECT_ROOT, 'indian_restaurants.csv'))

    def _load_data(self):
        """Load required datasets"""
        try:
            print("Loading data...")
            self.places_df = self._load_places_df()
            self.hotels_df = self._load_hotels_df()
            self.restaurants_df = self._load_restaurants_df()

            print("Data loaded successfully.")
            # Preprocess the places data
            self.places_df = self.preprocess_places_data(self.places_df)

            # Load or generate embeddings
            self.activity_embeddings = {}
            self.place_type_embeddings = {}
            try:
                # Try to load existing embeddings
                with open(os.path.join(_PROJECT_ROOT, 'activity_embeddings.pkl'), 'rb') as f:
                    self.activity_embeddings = pickle.load(f)
                with open(os.path.join(_PROJECT_ROOT, 'place_type_embeddings.pkl'), 'rb') as f:
                    self.place_type_embeddings = pickle.load(f)
                print("Loaded existing embeddings from pickle files")
            except FileNotFoundError:
                print("Generating new embeddings...")
                # Collect unique activities and place types
                all_activities = set()
                all_place_types = set()
                for _, row in self.places_df.iterrows():
                    activities = row['famous activities with rating'].keys()
                    all_activities.update(activities)
                    all_place_types.add(row['type'])

                # Batch encode activities (API supports up to 250 per call)
                activity_list = list(all_activities)
                for i in range(0, len(activity_list), 50):
                    batch = activity_list[i:i+50]
                    embeddings = self._encode(batch)
                    for text, emb in zip(batch, embeddings):
                        self.activity_embeddings[text] = emb / norm(emb)
                    print(f"  Encoded activities {i+1}-{min(i+50, len(activity_list))} of {len(activity_list)}")

                # Batch encode place types
                place_type_list = list(all_place_types)
                for i in range(0, len(place_type_list), 50):
                    batch = place_type_list[i:i+50]
                    embeddings = self._encode(batch)
                    for text, emb in zip(batch, embeddings):
                        self.place_type_embeddings[text] = emb / norm(emb)

                # Save embeddings to pickle files
                with open(os.path.join(_PROJECT_ROOT, 'activity_embeddings.pkl'), 'wb') as f:
                    pickle.dump(self.activity_embeddings, f)
                with open(os.path.join(_PROJECT_ROOT, 'place_type_embeddings.pkl'), 'wb') as f:
                    pickle.dump(self.place_type_embeddings, f)
                print("Generated and saved new embeddings")


        except FileNotFoundError as e:
            raise Exception(f"Required data files not found: {str(e)}")

    def preprocess_places_data(self, df):
        """Preprocess the dataset for better recommendations."""
        # Clean column names
        df = df.rename(columns={'name': 'placename'})
        df = df.drop_duplicates(subset=['placename'], keep='first')

        # Use Google rating/count as primary; fall back to internal values.
        # google_rating and google_rating_count are already coerced to numeric
        # by _load_places_df before this is called.
        if 'google_rating' in df.columns:
            df['rating'] = df['google_rating'].where(
                df['google_rating'].notna(), df['rating']
            )
        if 'google_rating_count' in df.columns:
            df['no of rating'] = df['google_rating_count'].where(
                df['google_rating_count'].notna(), df['no of rating']
            )

        # Replace '/' with ',' in comma-separated fields
        df['famous activities'] = df['famous activities'].str.replace('/', ',')
        df['best month to visit'] = df['best month to visit'].str.replace('/', ',')

        # Convert string representations of dictionaries to actual dictionaries
        df['famous activities with rating'] = df['famous activities with rating'].apply(
            lambda x: ast.literal_eval(x) if isinstance(x, str) and x.startswith('{') else {}
        )

        # Derive effective_name: prefer Google's official display_name over internal name
        if 'display_name' in df.columns:
            df['effective_name'] = df['display_name'].where(
                df['display_name'].notna() & (df['display_name'].astype(str).str.strip() != ''),
                df['placename']
            )
        else:
            df['effective_name'] = df['placename']

        # Derive suitable_for: human-readable label from the 4 prefer_* boolean flags
        _pref_map = {
            'prefer_friends': 'friends',
            'prefer_couple': 'couples',
            'prefer_family_children': 'family with kids',
            'prefer_family_no_children': 'family without kids',
        }
        def _suitable_for_label(row):
            labels = [label for col, label in _pref_map.items()
                      if col in row and row[col]]
            return ', '.join(labels) if labels else ''
        df['suitable_for'] = df.apply(_suitable_for_label, axis=1)

        return df

    def merge_list(self, activities):
        """Merge activities with commas and 'and' before the last one."""
        if len(activities) == 0:
            return ""  # Handle empty list
        if len(activities) == 1:
            return activities[0]  # Handle single activity
        return ", ".join(activities[:-1]) + " and " + activities[-1]

    _user_embedding_cache = {}  # keyed by (trip_type, sorted activities) — static per model deployment

    def _generate_user_embedding(self, user_preferences):
        """Generate user embedding based on user preferences"""
        cache_key = (
            user_preferences.get('trip_type', ''),
            tuple(sorted(str(a) for a in user_preferences.get('preferred_activities', [])))
        )
        if cache_key in self._user_embedding_cache:
            return self._user_embedding_cache[cache_key]
        query = 'The user prefers trips focused on ' + user_preferences['trip_type'] + '. They are also interested in activities such as ' + self.merge_list(user_preferences['preferred_activities']) + '.'
        user_activity_embedding = self._encode([query])[0]
        self._user_embedding_cache[cache_key] = user_activity_embedding
        return user_activity_embedding

    def compute_activity_score(self, activity_dict, user_activity_embedding):
        """Compute activity score based on user activity embedding"""
           # Check if activity_dict is None or empty
        if not activity_dict:
            return 0
        
        score = 0.0

        for activity, rating in activity_dict.items():           

            if not activity:
                continue
            # Use cached normalized embedding if available
            activity_embedding = self.activity_embeddings.get(activity)

            if activity_embedding is None:
                # Fallback: compute and normalize if not cached
                activity_embedding = self._encode([activity])[0]
                activity_embedding = activity_embedding / norm(activity_embedding)  # normalize once
                self.activity_embeddings[activity] = activity_embedding

            similarity = dot(activity_embedding, user_activity_embedding) 
            score += rating * similarity

        return score

    def compute_trip_type_score(self, place_type, user_trip_type_embedding):
        """Compute trip type score using BERT embeddings"""
        # Newly-added places can have a missing/blank type (NaN/None) — skip
        # embedding those (a blank string would error the embeddings API) and
        # treat them as a neutral 0 score.
        if place_type is None or (isinstance(place_type, float) and pd.isna(place_type)) \
                or not str(place_type).strip():
            return 0.0

        place_type_embedding = self.place_type_embeddings.get(place_type)
        if place_type_embedding is None:
            place_type_embedding = self._encode([place_type])[0]
            place_type_embedding = place_type_embedding / norm(place_type_embedding)
            self.place_type_embeddings[place_type] = place_type_embedding

        similarity = dot(place_type_embedding, user_trip_type_embedding)
        return similarity

    def weighted_place_rating(self, row, C):
        m=50
        R = float(row['rating'])
        v = float(row['no of rating'])
        return (v / (v + m)) * R + (m / (v + m)) * C

    def weighted_restaurants_rating(self, row, C):
        m=50
        R = float(row['Rating'])
        v = float(row['Votes'])
        return (v / (v + m)) * R + (m / (v + m)) * C


    def normalize(self, text):
        return re.sub(r'[^\w\s]', '', str(text).lower().strip())
    
    def getEditPlaces(self, city, state):

        # Step 1: Try to match on city first
        edit_places = self.places_df[
            self.places_df['city'].fillna('').astype(str).str.lower() == city.lower()
        ]

        if edit_places.empty:
            # If no city matches, fall back to matching by state
            edit_places = self.places_df[
                self.places_df['state'].fillna('').astype(str).str.lower() == state.lower()
            ]


        # Calculate average rating for all restaurants (or set a constant)
        C = edit_places['rating'].mean()

        # Calculate weighted rating score for each place for user preferences
        edit_places['rating_score'] = edit_places.apply(
            lambda x: self.weighted_place_rating(x, C), axis=1
        )

        # Sort places by final score in descending order
        edit_place_recommendation = edit_places.sort_values('rating_score', ascending=False)

        print("edit_place_recommendation", edit_place_recommendation)

        return edit_place_recommendation.head(20)
    
    def generate_itinerary(self, user_preferences):
        """
        Generate travel itinerary based on user preferences

        Args:
            user_preferences (dict): Dictionary containing user preferences
                {
                    'preferred_activities': list,
                    'places_of_interest': str,
                    'number_of_people': int,
                    'travel_group_type': str,
                    'food_preferences': str,
                    'user_location': str,
                    'current_month': str,
                    'trip_type': str,
                    'trip_duration': str,
                    'suggested_places': list (optional)
                    'budget': str (optional)
                }

        Returns:
            dict: Recommended itinerary with places, hotels, and restaurants
        """
        try:

            # Ensure optional key doesn't raise a KeyError
            user_preferences['suggested_places'] = user_preferences.get('suggested_places', [])
            user_preferences['budget'] = user_preferences.get('budget', "")

            # Fetch places, hotels, restaurants in parallel — they are independent
            with ThreadPoolExecutor(max_workers=3) as ex:
                fut_places = ex.submit(self._get_place_recommendations, user_preferences)
                fut_hotels = ex.submit(self._get_hotel_recommendations, user_preferences)
                fut_rests  = ex.submit(self._get_restaurant_recommendations, user_preferences)
                places                    = fut_places.result()
                hotels                    = fut_hotels.result()
                restaurants, rest_slots   = fut_rests.result()

            # Enrich images in background — benefits the next request; not needed for current response
            threading.Thread(target=self._enrich_place_images, args=(places,), daemon=True).start()

            # Generate detailed itinerary
            itinerary = self._generate_detailed_itinerary(
                user_preferences,
                places,
                hotels,
                restaurants,
                rest_slot_counts=rest_slots,
            )

            return self._finalize_itinerary(itinerary, places, start_date=user_preferences.get('start_date'), user_preferences=user_preferences)

        except (KeyError, IndexError, TypeError) as e:
            print("Error while processing:", e)
            return {
                'status': 'error',
                'message': str(e)
            }

    def generate_itinerary_stream(self, user_preferences):
        """Incremental itinerary generation. Yields events as each piece is
        ready so the client renders progressively:
            progress    — per stage
            info        — trip title/description/city/state/price/notes (once)
            images      — main destination gallery (once)
            hotels      — trip-level hotels (once)
            similar_place — one per similar place
            day_info    — one per day: {day, theme, day_summary}
            place       — one per place_to_visit: {day, item}
            ad          — section ad after a day's places
            meal        — one per breakfast/lunch/dinner: {day, slot, item}
            available_places — not-yet-used places (once, near the end)
            complete    — full assembled result dict (for persistence)
            error       — on failure
        Trip info + hotels come from one skeleton call; each day is generated by
        its own call so it can be finalized and streamed before the next."""
        try:
            user_preferences['suggested_places'] = user_preferences.get('suggested_places', [])
            user_preferences['budget'] = user_preferences.get('budget', "")

            yield {'event': 'progress', 'step': 'started', 'message': 'Starting your itinerary...'}

            # Fetch places, hotels, restaurants in parallel — they are independent
            with ThreadPoolExecutor(max_workers=3) as ex:
                fut_places = ex.submit(self._get_place_recommendations, user_preferences)
                fut_hotels = ex.submit(self._get_hotel_recommendations, user_preferences)
                fut_rests  = ex.submit(self._get_restaurant_recommendations, user_preferences)
                places                    = fut_places.result()
                hotels                    = fut_hotels.result()
                restaurants, rest_slots   = fut_rests.result()

            threading.Thread(target=self._enrich_place_images, args=(places,), daemon=True).start()
            yield {'event': 'progress', 'step': 'places', 'message': 'Found places to visit'}

            # Trimmed inputs reused for the skeleton and each per-day call.
            places_trimmed = self._trim_for_prompt(places, self._PLACE_COLS_PROMPT, 30)
            hotels_trimmed = self._trim_for_prompt(hotels, self._HOTEL_COLS_PROMPT, 10)
            rests_trimmed  = self._trim_for_prompt(restaurants, self._REST_COLS_PROMPT, 20)

            # 1. Trip skeleton: name/description/hotels/similar_places (one call).
            yield {'event': 'progress', 'step': 'info', 'message': 'Preparing your trip overview...'}
            itinerary = self._generate_trip_skeleton(
                user_preferences, places_trimmed, rests_trimmed, hotels_trimmed
            )
            itinerary.setdefault('itinerary', [])

            # Trip-level finalize (main gallery + similar_places images). Uses the
            # scored places DataFrame; on empty falls back inside the helper.
            self._finalize_trip_level(itinerary, places)

            try:
                total_days = int(user_preferences.get('trip_duration'))
            except (TypeError, ValueError):
                total_days = len(itinerary.get('itinerary', [])) or 1

            # 2. Info (title/description) — bare image names URL-prefixed by route.
            yield {
                'event': 'info',
                'name': itinerary.get('name', ''),
                'description': itinerary.get('description', ''),
                'city': itinerary.get('city', ''),
                'state': itinerary.get('state', ''),
                'price_estimated_range': itinerary.get('price_estimated_range', ''),
                'total_days': total_days,
                'notes': itinerary.get('notes', ''),
            }

            # 3. Main images gallery.
            yield {'event': 'images', 'images': itinerary.get('images', [])}

            # 4. Trip-level hotels (finalize lat/lon on them via a hotels-only ctx).
            # Hotels are now grouped: [{city, from_day, to_day, selected, alternatives}].
            trip_hotels = itinerary.get('hotels', []) or []
            if trip_hotels:
                hotel_ctx = {'city': itinerary.get('city'), 'state': itinerary.get('state'),
                             'itinerary': [], 'hotels': trip_hotels}
                self._attach_lat_long(hotel_ctx)
                trip_hotels = json.loads(json.dumps(trip_hotels, default=lambda x: '' if pd.isna(x) else x))
                itinerary['hotels'] = trip_hotels
                yield {'event': 'hotels', 'hotels': trip_hotels}

            # 5. Similar places, one per event.
            for similar in itinerary.get('similar_places', []) or []:
                yield {'event': 'similar_place', 'item': similar}

            # 6. Day by day: generate → finalize → stream, one day at a time.
            start_date = user_preferences.get('start_date')
            built_days = []
            used_places = []
            for day_no in range(1, total_days + 1):
                yield {'event': 'progress', 'step': f'day_{day_no}',
                       'message': f'Planning day {day_no} of {total_days}...'}
                # Generate this day, retrying once on an empty/failed result. A
                # single day's failure must NOT abort the whole trip — otherwise
                # a transient LLM hiccup on day 1 leaves the client with only
                # info/hotels/images. Retry, then skip the day if still empty.
                extra = None
                for attempt in range(2):
                    try:
                        extra = self._generate_extra_days(
                            user_preferences, places_trimmed, rests_trimmed, hotels_trimmed,
                            start_day=day_no, num_days=1, used_places=used_places,
                            itinerary=itinerary, rest_slot_counts=rest_slots,
                        )
                    except Exception as day_err:
                        print(f"[stream] day {day_no} attempt {attempt + 1} raised: {day_err}")
                        extra = None
                    if extra:
                        break
                    print(f"[stream] day {day_no} attempt {attempt + 1} returned nothing; retrying...")
                if not extra:
                    print(f"[stream] day {day_no} failed after retries; skipping this day.")
                    continue
                day = extra[0]
                day['day'] = day_no
                # Finalize just this day (images, lat/lon, travel times, weather).
                finalized = self._finalize_days(
                    itinerary, [day], places, start_date=start_date,
                    start_day_index=day_no - 1,
                )
                day = finalized[0] if finalized else day
                built_days.append(day)
                used_places.extend(self._collect_used_place_names([day]))

                # Stream this day's granular events.
                yield {'event': 'day_info', 'day': day_no,
                       'theme': day.get('theme', ''), 'day_summary': day.get('day_summary', '')}
                timeline = day.get('timeline', []) or []
                has_places = any(i.get('type') == 'place' for i in timeline)
                has_meals  = any(i.get('type') == 'meal'  for i in timeline)
                for item in timeline:
                    yield {'event': 'timeline_item', 'day': day_no, 'item': item}
                # Signal the route to insert a section ad after this day's content.
                if has_places and has_meals:
                    yield {'event': 'ad_slot', 'day': day_no}
                meal_options = day.get('meal_options', {})
                if meal_options:
                    yield {'event': 'meal_options', 'day': day_no, 'options': meal_options}

            itinerary['itinerary'] = built_days
            itinerary['total_days'] = len(built_days)

            # 7. Available (not-yet-used) places.
            try:
                available = self._get_available_places(itinerary, user_preferences, 30)
            except Exception as e:
                print(f"Warning: _get_available_places failed: {e}")
                available = []
            itinerary['available_places'] = available
            yield {'event': 'available_places', 'available_places': available}

            # 8. Assemble the full result for persistence (mirrors _finalize_itinerary).
            token_usage = itinerary.pop('_token_usage', None) if isinstance(itinerary, dict) else None
            result = {
                'status': 'success',
                'token_usage': token_usage,
                'data': {'detailed_itinerary': itinerary},
            }
            yield {'event': 'complete', 'data': result}

        except Exception as e:
            import traceback
            print("Error while streaming itinerary:", e)
            traceback.print_exc()
            yield {'event': 'error', 'message': str(e)}

    def edit_itinerary(self, user_preferences):
        """Regenerate an itinerary that MUST include an explicit list of places.

        Accepts the same payload as `generate_itinerary` plus one extra key,
        `places` — a list of place names (or dicts with a `name`) that must all
        appear in the resulting itinerary. Uses a dedicated edit prompt and then
        runs the exact same post-processing (images, persistence, similar
        places) as generation.
        """
        try:
            user_preferences['suggested_places'] = user_preferences.get('suggested_places', [])
            user_preferences['budget'] = user_preferences.get('budget', "")

            # The extra payload: places that MUST be included in the itinerary.
            edit_places = user_preferences.get('places', []) or []
            # Normalize to a list of plain name strings (accept strings or dicts).
            place_names = []
            for p in edit_places:
                if isinstance(p, dict):
                    name = p.get('name') or p.get('placename') or ''
                else:
                    name = str(p)
                name = name.strip()
                if name and name not in place_names:
                    place_names.append(name)

            with ThreadPoolExecutor(max_workers=3) as ex:
                fut_places = ex.submit(self._get_place_recommendations, user_preferences)
                fut_hotels = ex.submit(self._get_hotel_recommendations, user_preferences)
                fut_rests  = ex.submit(self._get_restaurant_recommendations, user_preferences)
                places                    = fut_places.result()
                hotels                    = fut_hotels.result()
                restaurants, rest_slots   = fut_rests.result()
            threading.Thread(target=self._enrich_place_images, args=(places,), daemon=True).start()

            places_trimmed = self._trim_for_prompt(places, self._PLACE_COLS_PROMPT, 60)
            hotels_trimmed = self._trim_for_prompt(hotels, self._HOTEL_COLS_PROMPT, 60)
            rests_trimmed  = self._trim_for_prompt(restaurants, self._REST_COLS_PROMPT, 60)
            messages = self.generate_edit_itinerary_prompt(
                user_preferences, places_trimmed, rests_trimmed, hotels_trimmed, place_names,
                rest_slot_counts=rest_slots,
            )
            print(f"Edit prompt length: {sum(len(m['content']) for m in messages)} chars")

            response = self.client.responses.create(
                model=self.chat_deployment,
                input=messages,
                max_output_tokens=self.max_tokens,
                text={"format": {"type": "json_object"}},
            )
            response_text = self._extract_completed_json(response)
            try:
                itinerary = self._parse_itinerary_json(response_text)
            except ValueError as e:
                self._log_json_decode_error(response, response_text, e)
                raise

            self._accumulate_usage(itinerary, response)

            return self._finalize_itinerary(itinerary, places, start_date=user_preferences.get('start_date'), user_preferences=user_preferences)

        except (KeyError, IndexError, TypeError) as e:
            print("Error while processing edit:", e)
            return {
                'status': 'error',
                'message': str(e)
            }

    def _get_available_places(self, itinerary, user_preferences, count):
        """Return up to `count` scored places not already in the itinerary,
        enriched to the same shape as places_to_visit entries."""
        used_names = set()
        for day in itinerary.get('itinerary', []):
            for item in day.get('timeline', []):
                if item.get('type') == 'place':
                    n = item.get('name', '').strip()
                    if n:
                        used_names.add(n.lower())

        scored_df = self._get_place_recommendations(user_preferences)
        remaining_df = scored_df[
            ~scored_df['effective_name'].str.strip().str.lower().isin(used_names)
        ].head(count)

        if remaining_df.empty:
            return []

        COLS = [
            'effective_name', 'placename', 'city', 'state',
            'primary_type_name', 'place_types', 'famous activities',
            'best month to visit', 'rating', 'google_rating', 'google_rating_count',
            'lat', 'lon', 'short_formatted_address', 'editorial_summary',
            'review_summary', 'opening_hours', 'website_uri', 'google_maps_uri',
        ]
        avail_cols = [c for c in COLS if c in remaining_df.columns]
        places_list = remaining_df[avail_cols].fillna('').to_dict(orient='records')

        for ap in places_list:
            ap['name'] = ap.pop('effective_name', ap.get('placename', ''))
            ap.setdefault('reason', '')
            ap.setdefault('activities', [])
            ap.setdefault('duration', '')
            ap.setdefault('suggested_start_time', '')
            ap.setdefault('travel_from_prev', None)
            ap.setdefault('images', [])

        image_map = self._get_images_for_places([ap['name'] for ap in places_list])
        for ap in places_list:
            ap['images'] = image_map.get(ap['name'].strip().lower(), [])

        _wrapper = {
            'city': itinerary.get('city', ''),
            'state': itinerary.get('state', ''),
            'itinerary': [{'timeline': [dict(p, type='place') for p in places_list]}],
            'hotels': [],
        }
        self._attach_lat_long(_wrapper)
        self._attach_db_fields(_wrapper)

        return places_list

    def _finalize_trip_level(self, itinerary, places):
        """Trip-level post-processing done ONCE per itinerary (not per day):
        resolves the main destination image gallery and attaches images to each
        similar_places entry (persisting them in a background thread). Mutates
        `itinerary` in place. Returns the `places` DataFrame (filled) so callers
        can reuse it for per-day finalize."""
        if places.empty:
            places = self.getEditPlaces(itinerary['city'], itinerary['state'])
        # Merge places + popular destinations to build a name -> image map used
        # to resolve the main destination image.
        merged_places = []
        merged_places.extend(places.to_dict(orient='records'))
        popular_destinations = self.get_popular_destination()
        if popular_destinations:
            merged_places.extend(popular_destinations)
        place_image_map = {}
        for place in merged_places:
            image = place.get('image')
            if image and pd.notna(image):
                pname = place.get('placename') or place.get('city') or place.get('name', '')
                if pname:
                    place_image_map[pname] = image

        placename = itinerary['name']

        try:
            with open(os.path.join(_PROJECT_ROOT, 'similar_places.pkl'), 'rb') as f:
                similar_places_data = pickle.load(f)
        except FileNotFoundError:
            similar_places_data = {}

        if placename not in place_image_map and placename in similar_places_data:
            place_image_map[placename] = similar_places_data.get(placename).get('image')
        if placename not in place_image_map:
            place_image_map[placename] = 'default' + str(random.randint(1, 7)) + '.webp'

        main_single_image = None
        if placename in place_image_map and pd.notna(place_image_map[placename]):
            main_single_image = place_image_map[placename]

        # Parent-level main destination gallery.
        main_images = self._get_images_for_places([placename]).get(
            str(placename).strip().lower(), []
        )
        if not main_images:
            main_images = self._search_images_by_keywords(
                [placename, itinerary.get('city'), itinerary.get('state')],
                limit=5,
            )
        if not main_images and main_single_image:
            main_images = [main_single_image]
        itinerary['images'] = main_images

        # Similar places: persist + attach images.
        similar = itinerary.get('similar_places') or []
        threading.Thread(target=self.save_similar_places, args=(similar,), daemon=True).start()
        for place in similar:
            sp_name = place.get('placename')
            matching_place = similar_places_data.get(sp_name)
            if matching_place and pd.notna(matching_place.get('image')):
                place['image'] = matching_place['image']
            else:
                place['image'] = 'default' + str(random.randint(1, 7)) + '.webp'

        # Stash the name->image map so per-day finalize can reuse it as a
        # last-resort image fallback without recomputing.
        self._place_image_fallback = {
            str(name).strip().lower(): img for name, img in place_image_map.items()
        }
        return places

    def _finalize_days(self, itinerary, days, places, start_date=None, start_day_index=0):
        """Per-day post-processing on a SUBSET of days: attaches 4-5 images to
        each place, resolves lat/lon, computes travel times, overrides with DB
        fields, attaches weather, and persists new entities. `itinerary` provides
        city/state/hotels context; `days` is the list of day dicts to finalize
        (mutated in place). `start_day_index` is the 0-based index of the first
        day in `days` within the whole trip (for correct weather dates). Returns
        the cleaned (NaN-stripped) list of days."""
        # Per-place images: batch-fetch from DB, keyword-search + in-memory
        # fallback map (built in _finalize_trip_level).
        fallback_map = getattr(self, '_place_image_fallback', {}) or {}
        all_place_names = [
            str(item.get('name', '')).strip()
            for day in days
            for item in day.get('timeline', [])
            if item.get('type') == 'place' and str(item.get('name', '')).strip()
        ]
        place_images_db = self._get_images_for_places(all_place_names) if all_place_names else {}
        for day in days:
            for item in day.get('timeline', []):
                if item.get('type') != 'place':
                    continue
                place_name = str(item.get('name', '')).strip()
                key = place_name.lower()
                images = place_images_db.get(key, [])[:5]
                if not images:
                    images = self._search_images_by_keywords([place_name], limit=5)
                if not images:
                    single = fallback_map.get(key)
                    if single and not (not isinstance(single, list) and pd.isna(single)):
                        images = [single]
                if not images:
                    images = ['default' + str(random.randint(1, 7)) + '.webp']
                item['images'] = images

        # Build a temporary itinerary containing ONLY these days so the existing
        # per-itinerary helpers (which iterate itinerary['itinerary']) operate on
        # just this subset. Include hotels so lat/lon is attached to them once.
        ctx = {
            'city': itinerary.get('city'),
            'state': itinerary.get('state'),
            'itinerary': days,
            'hotels': itinerary.get('hotels', []),
        }
        self._attach_lat_long(ctx)
        self._compute_travel_times(ctx)
        self._attach_db_fields(ctx)

        if start_date:
            # Offset the trip start so day dates are correct for this subset.
            from datetime import datetime, timedelta
            try:
                base = datetime.strptime(start_date, "%Y-%m-%d").date()
                subset_start = (base + timedelta(days=start_day_index)).strftime("%Y-%m-%d")
                self._attach_weather(ctx, subset_start)
            except ValueError:
                pass

        # Persist new places/hotels/restaurants for this subset (background).
        threading.Thread(target=self.save_new_places, args=(copy.deepcopy(ctx),), daemon=True).start()

        # NaN cleanup for these days.
        cleaned = json.loads(json.dumps(days, default=lambda x: '' if pd.isna(x) else x))
        return cleaned

    def _finalize_itinerary(self, itinerary, places, start_date=None, user_preferences=None):
        """Shared post-processing for generated/edited itineraries. Runs the
        trip-level finalize once, then the per-day finalize over all days, and
        builds the response dict. Kept as the single entry point for the
        non-stream path so its output is unchanged."""
        try:
            places = self._finalize_trip_level(itinerary, places)

            days = itinerary.get('itinerary', []) or []
            itinerary['itinerary'] = self._finalize_days(
                itinerary, days, places, start_date=start_date, start_day_index=0
            )

            # Ensure total_days is always an integer — LLM sometimes returns it
            # as a string or omits it entirely; fall back to actual day count.
            try:
                itinerary['total_days'] = int(itinerary['total_days'])
            except (TypeError, ValueError, KeyError):
                itinerary['total_days'] = len(itinerary.get('itinerary', []))

            # Lift the accumulated token usage out of the itinerary dict to the
            # result top level so store_itinerary can persist it.
            token_usage = itinerary.pop('_token_usage', None) if isinstance(itinerary, dict) else None

            if user_preferences:
                try:
                    itinerary['available_places'] = self._get_available_places(
                        itinerary, user_preferences, 30
                    )
                except Exception as e:
                    print(f"Warning: _get_available_places failed: {e}")
                    itinerary['available_places'] = []

            return {
                'status': 'success',
                'token_usage': token_usage,
                'data': {
                    'detailed_itinerary': itinerary,
                }
            }

        except Exception as e:
            import traceback
            print("Error while processing:", e)
            traceback.print_exc()
            return {
                'status': 'error',
                'message': str(e)
            }

    # Circuit-breaker: skip DB lat/lon lookups for 30s after a connection failure
    # so a single timeout doesn't cascade into 25× 10s stalls per request.
    _db_lat_long_dead_until = 0.0
    _db_lat_long_dead_lock = None

    def _db_lat_long(self, table, name):
        """Look up lat/lon/full_address for a row by name in
        places/hotels/restaurants. Returns a dict only when lat & lon are both
        present, else None. Short-circuits for 30s after a connection failure."""
        import time as _time
        if not name:
            return None
        if not is_db_ready():
            return None

        if ItenaryRecommendationSystem._db_lat_long_dead_lock is None:
            import threading
            ItenaryRecommendationSystem._db_lat_long_dead_lock = threading.Lock()

        with ItenaryRecommendationSystem._db_lat_long_dead_lock:
            if _time.monotonic() < ItenaryRecommendationSystem._db_lat_long_dead_until:
                return None

        name_col = "property_name" if table == "hotels" else "name"
        try:
            rows = fetch_dicts(
                f"SELECT TOP 1 lat, lon, full_address FROM {table} "
                f"WHERE LOWER({name_col}) = ? AND lat IS NOT NULL AND lon IS NOT NULL",
                (str(name).strip().lower(),),
            )
        except Exception as e:
            print(f"  _db_lat_long failed for {name!r} in {table} ({e})")
            with ItenaryRecommendationSystem._db_lat_long_dead_lock:
                ItenaryRecommendationSystem._db_lat_long_dead_until = _time.monotonic() + 30.0
            return None
        if rows:
            return {
                "lat": float(rows[0]["lat"]),
                "lon": float(rows[0]["lon"]),
                "full_address": rows[0].get("full_address") or "",
            }
        return None

    def _save_lat_long_to_db(self, table, name, lat, lon, full_address):
        """Write geocoded lat/lon/full_address back onto the matching DB row so
        we never geocode it again. Updates only rows that currently lack coords;
        no-op if the entity isn't in the table."""
        if not name:
            return
        name_col = "property_name" if table == "hotels" else "name"
        try:
            conn = new_connection()
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE {table} SET lat = ?, lon = ?, full_address = ? "
                f"WHERE LOWER({name_col}) = ? AND (lat IS NULL OR lon IS NULL)",
                (lat, lon, full_address, str(name).strip().lower()),
            )
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"  _save_lat_long_to_db failed for {name!r} in {table} ({e})")

    def _resolve_lat_long(self, table, name, location_hint=""):
        """Resolve lat/lon/full_address for an entity. DB-first; Nominatim
        fallback is disabled while the cron backfill (update_places_lat_lon.py)
        is running — once the DB is fully populated, re-enable the block below."""
        coords = self._db_lat_long(table, name)
        if coords is not None:
            return coords

        # --- Nominatim fallback (disabled: re-enable once DB backfill is complete) ---
        # name_str = str(name or "").strip()
        # hint = location_hint.strip().strip(",") if location_hint else ""
        # queries = []
        # if hint:
        #     queries.append(f"{name_str}, {hint}")
        # queries.append(name_str)
        # result = None
        # for q in queries:
        #     result = self.geocoder.geocode(q)
        #     if result is not None:
        #         break
        # if result is not None:
        #     self._save_lat_long_to_db(
        #         table, name, result["lat"], result["lon"], result.get("full_address", "")
        #     )
        #     return result
        # --- end Nominatim fallback ---

        return None

    def _attach_db_fields(self, itinerary):
        """Override LLM-generated fields with verified Google data from the DB.
        Also attaches UI-only fields (google_maps_uri, google_photo_refs) that
        are not passed to the prompt but are needed by the frontend. Silently
        skips places not found in the DB lookup."""
        if self.places_df is None or self.places_df.empty:
            return
        db_lookup = {
            str(r.get("placename") or r.get("name", "")).strip().lower(): r
            for r in self.places_df.to_dict(orient="records")
        }
        for day in itinerary.get("itinerary", []):
            for item in day.get("timeline", []):
                if item.get("type") != "place":
                    continue
                key = str(item.get("name", "")).strip().lower()
                db = db_lookup.get(key)
                if not db:
                    continue
                # Override LLM-generated fields with authoritative Google data
                if db.get("opening_hours"):
                    item["opening_hours"] = db["opening_hours"]
                if db.get("website_uri"):
                    item["website_uri"] = db["website_uri"]
                if db.get("phone_number"):
                    item["phone_number"] = db["phone_number"]
                # UI-only fields — never in the prompt, attached here for the frontend
                if db.get("google_maps_uri"):
                    item["google_maps_uri"] = db["google_maps_uri"]
                if db.get("full_address"):
                    item["full_address"] = db["full_address"]
                if db.get("good_for_children") is not None:
                    item["good_for_children"] = db["good_for_children"]
                if db.get("accessibility"):
                    item["accessibility"] = db["accessibility"]
                # Extra detail fields for place detail screen
                item["editorial_summary"] = db.get("editorial_summary") or ""
                item["review_summary"] = db.get("review_summary") or ""
                item["short_formatted_address"] = db.get("short_formatted_address") or ""
                item["google_rating"] = db.get("google_rating")
                item["google_rating_count"] = db.get("google_rating_count")

    def _attach_lat_long(self, itinerary):
        """Populate `lat`/`lon`/`full_address` on every place, restaurant and
        hotel in the itinerary. DB first, Nominatim as fallback. Caches within
        this call so the same entity isn't resolved twice."""
        seen = {}

        def resolve(table, name, location_hint=""):
            key = (table, str(name).strip().lower())
            if key not in seen:
                seen[key] = self._resolve_lat_long(table, name, location_hint)
            return seen[key]

        def apply(entity, table, default_hint):
            res = resolve(table, entity.get('name'), entity.get('location') or default_hint)
            if res:
                entity['lat'] = res['lat']
                entity['lon'] = res['lon']
                entity['full_address'] = res.get('full_address', '')
            else:
                entity['lat'] = None
                entity['lon'] = None
                entity['full_address'] = ''

        city = itinerary.get('city') or ''
        state = itinerary.get('state') or ''
        default_hint = ", ".join([p for p in [city, state] if p])

        for day in itinerary.get('itinerary', []):
            for item in day.get('timeline', []):
                t = item.get('type')
                if t == 'place':
                    apply(item, 'places', default_hint)
                elif t == 'meal':
                    apply(item, 'restaurants', default_hint)
                # hotel check_in/check_out items don't need lat/lon individually
            for slot_opts in day.get('meal_options', {}).values():
                for opt in (slot_opts or []):
                    if isinstance(opt, dict):
                        apply(opt, 'restaurants', default_hint)
        for hotel_group in itinerary.get('hotels', []):
            if isinstance(hotel_group, dict):
                sel = hotel_group.get('selected')
                if isinstance(sel, dict):
                    apply(sel, 'hotels', default_hint)
                for alt in hotel_group.get('alternatives', []):
                    if isinstance(alt, dict):
                        apply(alt, 'hotels', default_hint)

    def _compute_travel_times(self, itinerary):
        """Overwrite LLM-guessed travel_from_prev with real haversine estimates.
        Must run after _attach_lat_long so lat/lon are already set."""
        from math import radians, sin, cos, sqrt, atan2

        def haversine(lat1, lon1, lat2, lon2):
            R = 6371
            dlat = radians(lat2 - lat1)
            dlon = radians(lon2 - lon1)
            a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
            return R * 2 * atan2(sqrt(a), sqrt(1 - a))

        for day in itinerary.get('itinerary', []):
            # Only items with real lat/lon get overwritten haversine travel times.
            # Hotel items typically lack lat/lon so they're skipped gracefully.
            locatable = [
                item for item in day.get('timeline', [])
                if item.get('lat') is not None and item.get('lon') is not None
            ]
            for i, item in enumerate(locatable):
                if i == 0:
                    item['travel_from_prev'] = None
                    continue
                prev = locatable[i - 1]
                try:
                    dist_km = haversine(
                        float(prev['lat']), float(prev['lon']),
                        float(item['lat']), float(item['lon'])
                    )
                    mins = max(5, int(dist_km / 30 * 60))
                    mode = 'walking' if dist_km < 1.5 else 'auto' if dist_km < 4 else 'cab'
                    item['travel_from_prev'] = {
                        'duration_mins': mins,
                        'mode': mode,
                        'note': f'~{mins} min by {mode} from {prev["name"]}'
                    }
                except (TypeError, KeyError, ValueError):
                    pass

    def _attach_weather(self, itinerary, start_date_str):
        """Attach per-place weather to every place_to_visit using its lat/lon.
        Also sets a representative day-level weather summary on each day object.
        Requires _attach_lat_long to have already run. Silently skips places
        without coordinates. Uses the 2-hour in-memory cache in weather service.
        All weather fetches are parallelized via ThreadPoolExecutor."""
        from datetime import datetime, timedelta
        from features.weather.service import get_weather_by_coords

        try:
            trip_start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            return

        # Collect all tasks: (item_obj, day_index, day_date_str) — place items only
        tasks = []
        for i, day in enumerate(itinerary.get('itinerary', [])):
            day_date = (trip_start + timedelta(days=i)).strftime("%Y-%m-%d")
            for item in day.get('timeline', []):
                if item.get('type') == 'place':
                    tasks.append((item, i, day_date))

        if not tasks:
            return

        def _fetch_weather(task):
            place, day_idx, day_date = task
            lat = place.get('lat')
            lon = place.get('lon')
            if lat is None or lon is None:
                return None, day_idx
            try:
                result, _ = get_weather_by_coords(float(lat), float(lon), day_date, days=1)
                return result, day_idx
            except Exception:
                return None, day_idx

        with ThreadPoolExecutor(max_workers=8) as ex:
            weather_results = list(ex.map(_fetch_weather, tasks))

        day_weather = {}
        for (place, day_idx, _), (result, _) in zip(tasks, weather_results):
            if result and result.get('weather'):
                entry = result['weather'][0]
                place['weather'] = entry
                if day_idx not in day_weather:
                    day_weather[day_idx] = entry
            else:
                place['weather'] = None

        for i, day in enumerate(itinerary.get('itinerary', [])):
            day['weather'] = day_weather.get(i)

    def _get_place_recommendations(self, user_preferences):
        """Get recommended places based on user preferences"""
        # generate user embedding
        user_embedding = self._generate_user_embedding(user_preferences)
        user_embedding = user_embedding / norm(user_embedding)

        top_places = self.places_df.copy()

        top_places['image'] = top_places['image'].apply(
            lambda x: x if isinstance(x, str) and x.endswith('.webp') else None
        )

        # Calculate activity score for each place for user preferences
        top_places['activity_score'] = top_places['famous activities with rating'].apply(
            lambda x: self.compute_activity_score(x, user_embedding)
        )

        # Calculate trip type score for each place for user preferences
        top_places['trip_type_score'] = top_places['type'].apply(
            lambda x: self.compute_trip_type_score(x, user_embedding)
        )

        poi = user_preferences["places_of_interest"]
        preferred_location = (", ".join(poi) if isinstance(poi, list) else str(poi)).lower()

        # Split the preferred_location on comma and strip spaces
        location_parts = [part.strip() for part in preferred_location.split(",")]

        # Step 1: Try to match on city first (exact match to avoid e.g. "goa" matching "goalpara")
        city_matches = top_places[
            top_places['city'].fillna('').astype(str).str.lower().apply(
                lambda city: any(part == city for part in location_parts)
            )
        ]

        if not city_matches.empty:
            # If there are city matches, use them only
            top_places = city_matches.copy()
        else:
            # Otherwise fall back to matching by state (exact match)
            top_places = top_places[
                top_places['state'].fillna('').astype(str).str.lower().apply(
                    lambda state: any(part == state for part in location_parts))
            ].copy()

        # Calculate average rating for all restaurants (or set a constant)
        C = top_places['rating'].mean()

        # Calculate weighted rating score for each place for user preferences
        top_places['rating_score'] = top_places.apply(
            lambda x: self.weighted_place_rating(x, C), axis=1
        )

        # Normalize scores to a range of 0-1; guard against single-row or uniform-score sets
        for column in ['activity_score', 'trip_type_score', 'rating_score']:
            min_val = top_places[column].min()
            max_val = top_places[column].max()
            rng = max_val - min_val
            top_places[column] = (top_places[column] - min_val) / rng if rng > 0 else 1.0

        # Calculate final output score
        top_places['final_score'] = (
            0.5 * top_places['activity_score'] +
            0.3 * top_places['trip_type_score'] +
            0.2 * top_places['rating_score']
        )
        # Sort places by final score in descending order
        primary_place_recommendation = top_places.sort_values('final_score', ascending=False)

        # Get top 50 places
        top_places = primary_place_recommendation

        return top_places.head(100)


    def _enrich_place_images(self, places_df):
        """Fetch images from Pexels/Unsplash for places that don't have a local image."""
        if places_df.empty:
            return places_df
        for idx, row in places_df.iterrows():
            if not row.get('image'):
                url = self.image_client.get_place_image(
                    row.get('name', '') or row.get('placename', ''),
                    row.get('city', '')
                )
                if url:
                    places_df.at[idx, 'image'] = url
        return places_df

    # Star-rating bands that define each hotel tier (4 tiers).
    _HOTEL_TIER_STARS = {
        'budget':  (0, 2),
        'mid':     (3, 3),
        'high':    (4, 4),
        'luxury':  (5, 5),
    }

    # Per-meal cost caps in INR for hard restaurant pre-filtering by budget tier.
    # Tuple: (breakfast_max, lunch_max, dinner_max). NULL/0 cost rows are always kept.
    _MEAL_COST_CAPS = {
        'budget':  (200,  350,  400),
        'mid':     (400,  700,  700),
        'high':    (700,  1200, 1500),
        'luxury':  (1200, 2500, 9999),
    }

    def _get_hotel_recommendations(self, user_preferences):
        """Get hotel recommendations hard-filtered to the user's budget tier.

        A budget user never sees luxury properties in their options. Falls back
        to ±1 star if fewer than 3 hotels match the exact tier so the LLM
        always has enough data to populate all hotel groups.
        """
        poi = user_preferences["places_of_interest"]
        city = ", ".join(poi) if isinstance(poi, list) else str(poi)

        live_hotels = self.places_client.search_hotels(city)
        if live_hotels:
            return pd.DataFrame(live_hotels).head(100)

        # City-only match (state bleed excluded — see comment in git history).
        city_part = city.split(',')[0].strip().lower()
        top_hotels = self.hotels_df[
            self.hotels_df['city'].fillna('').astype(str).str.lower().str.contains(city_part, na=False) |
            self.hotels_df['address'].fillna('').astype(str).str.lower().str.contains(city_part, na=False)
        ].sort_values('site_review_rating', ascending=False)

        pref = str(user_preferences.get('hotel_preference') or '').strip().lower()
        if pref in self._HOTEL_TIER_STARS:
            lo, hi = self._HOTEL_TIER_STARS[pref]
            in_tier = top_hotels[top_hotels['hotel_star_rating'].between(lo, hi, inclusive='both')]
            # Fallback: widen by ±1 star so LLM always has ≥3 hotels to work with.
            if len(in_tier) < 3:
                in_tier = top_hotels[
                    top_hotels['hotel_star_rating'].between(max(0, lo - 1), min(5, hi + 1), inclusive='both')
                ]
            top_hotels = in_tier.reset_index(drop=True)

        return top_hotels.head(100)

    def _get_restaurant_recommendations(self, user_preferences):
        """Get restaurant recommendations filtered and annotated by budget tier.

        Returns a tuple (DataFrame, slot_counts) where:
          - DataFrame has a `suitable_slots` column ("breakfast,lunch,dinner" /
            "lunch,dinner" / "dinner") derived from the Cost column and the
            user's tier caps. Rows with no cost data keep all slots.
          - slot_counts is {"breakfast": n, "lunch": n, "dinner": n} — tells
            prompt-builders how many options exist per slot so the LLM knows
            when to supplement from its own knowledge.

        Cost note: the `Cost` column stores "cost for two" in INR. The
        breakfast/lunch/dinner caps in _MEAL_COST_CAPS are per-person, so we
        double them before comparing (cost-for-two ≤ 2 × per-person cap).
        """
        poi = user_preferences["places_of_interest"]
        city = ", ".join(poi) if isinstance(poi, list) else str(poi)
        cuisine_raw = user_preferences["food_preferences"]
        cuisine = ", ".join(cuisine_raw) if isinstance(cuisine_raw, list) else str(cuisine_raw)

        pref = str(user_preferences.get('hotel_preference') or '').strip().lower()
        caps = self._MEAL_COST_CAPS.get(pref)

        def _annotate_and_count(df):
            """Add suitable_slots column and return (df, slot_counts)."""
            df = df.copy()
            if caps and 'Cost' in df.columns:
                b_max, l_max, d_max = caps
                # Cost column is "cost for two" — double the per-person caps.
                b2, l2, d2 = b_max * 2, l_max * 2, d_max * 2
                cost_col = pd.to_numeric(df['Cost'], errors='coerce')
                unknown = cost_col.isna() | (cost_col == 0)

                # Hard drop: restaurants above 2× dinner_max are never affordable.
                df = df[unknown | (cost_col <= d2)].copy()
                cost_col = pd.to_numeric(df['Cost'], errors='coerce')
                unknown = cost_col.isna() | (cost_col == 0)

                def _slots(c):
                    if pd.isna(c) or c == 0: return 'breakfast,lunch,dinner'
                    if c <= b2:              return 'breakfast,lunch,dinner'
                    if c <= l2:             return 'lunch,dinner'
                    return 'dinner'
                df['suitable_slots'] = cost_col.apply(_slots)
            else:
                df['suitable_slots'] = 'breakfast,lunch,dinner'

            slots_str = df['suitable_slots'].astype(str)
            slot_counts = {
                'breakfast': int(slots_str.str.contains('breakfast').sum()),
                'lunch':     int(slots_str.str.contains('lunch').sum()),
                'dinner':    int(slots_str.str.contains('dinner').sum()),
            }
            return df, slot_counts

        live_restaurants = self.places_client.search_restaurants(city, cuisine)
        if live_restaurants:
            df = pd.DataFrame(live_restaurants).head(100)
            return _annotate_and_count(df)

        # Fallback to CSV/DB data — city-only match, same rationale as hotels.
        preferred_cuisines = [self.normalize(c) for c in cuisine.split(',')]
        city_part = self.normalize(city.split(',')[0])
        cuisine_pattern = '|'.join(preferred_cuisines)

        top_restaurants = self.restaurants_df[
            (self.restaurants_df['City'].apply(self.normalize).str.contains(city_part, na=False) |
            self.restaurants_df['Locality'].apply(self.normalize).str.contains(city_part, na=False)) &
            self.restaurants_df['Cuisine'].apply(self.normalize).str.contains(cuisine_pattern, na=False)
        ].sort_values('Rating', ascending=False)
        top_restaurants = top_restaurants.drop_duplicates(subset=['Name'], keep='first')

        C = top_restaurants['Rating'].mean()
        top_restaurants = top_restaurants.copy()
        top_restaurants['rating_score'] = top_restaurants.apply(
            lambda x: self.weighted_restaurants_rating(x, C), axis=1
        )
        top_restaurants = top_restaurants.sort_values('rating_score', ascending=False)

        return _annotate_and_count(top_restaurants.head(100))



    def _generate_destination_description(self, destination):
        """Generate a short, friendly 1-2 sentence description for a destination
        using a simple prompt. Used for the early `info` event so the user sees
        a description before the full day-by-day plan is built. Returns '' on
        any failure so streaming is never blocked."""
        destination = str(destination or "").strip()
        if not destination:
            return ""
        try:
            prompt = (
                f"Write a short, engaging 1-2 sentence travel description for "
                f"{destination}. Only return the description text — no titles, "
                f"quotes, markdown, or extra commentary."
            )
            response = self.client.responses.create(
                model=self.chat_deployment,
                input=[{"role": "user", "content": prompt}],
            )
            return (response.output_text or "").strip()
        except Exception as e:
            print(f"  _generate_destination_description failed for {destination!r}: {e}")
            return ""

    # Whitelist of columns passed to the LLM generation prompt — THE ONLY GATE.
    # _trim_for_prompt selects only these; the full DataFrame never reaches the LLM.
    # google_rating/google_rating_count are primary; internal rating kept as fallback.
    # editorial_summary and review_summary are included when available (partial fill is fine).
    _PLACE_COLS_PROMPT = [
        'effective_name',           # Google display_name where available, else internal name (95%)
        'short_formatted_address',  # replaces 'city' — neighbourhood-level context (93%)
        'primary_type_name',        # replaces 'type' — e.g. "Hindu Temple" vs "Religious Site" (75%)
        'place_types',              # multi-value: "monument, tourist_attraction" (96%)
        'google_rating',            # primary rating — higher review count than internal (81%)
        'google_rating_count',      # primary review count (81%)
        'rating',                   # fallback for 847 rows where google_rating is NULL (98%)
        'famous activities',        # curated activity list for preference matching (100%)
        'best month to visit',      # seasonal scheduling context (98%)
        'opening_hours',            # real hours for ordering by opening time — LLM hallucinates without it (43%)
        'editorial_summary',        # curated ~93-char description when available (31%)
        'review_summary',           # crowd sentiment ~326 chars when available (36%)
        'suitable_for',             # derived from prefer_* flags — group suitability (100%)
    ]
    _HOTEL_COLS_PROMPT = ['property_name', 'city', 'hotel_star_rating', 'site_review_rating', 'property_type', 'pageurl']
    _REST_COLS_PROMPT  = ['Name', 'City', 'Cuisine', 'Rating', 'Votes', 'Cost', 'Locality', 'suitable_slots']

    def _trim_for_prompt(self, df, cols, n):
        available = [c for c in cols if c in df.columns]
        return df[available].head(n)

    @staticmethod
    def _accumulate_usage(itinerary, response):
        """Add this LLM call's token usage to a running total stashed on the
        itinerary dict under `_token_usage`. Generation can make several calls
        (the main plan + follow-up top-up days), so usage is summed across all
        of them. Safe if `response.usage` is missing. The `_token_usage` key is
        lifted out in _finalize_itinerary and never persisted in response_json."""
        if not isinstance(itinerary, dict):
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        acc = itinerary.setdefault("_token_usage", {"input_token": 0, "output_token": 0})
        acc["input_token"] += int(getattr(usage, "input_tokens", 0) or 0)
        acc["output_token"] += int(getattr(usage, "output_tokens", 0) or 0)

    @staticmethod
    def _log_json_decode_error(response, response_text, err):
        """Dump the model output around a JSON parse failure so we can see WHY it
        failed (truncation vs malformed content) instead of guessing from the
        char offset. Prints total length, token usage, response status, and a
        window of the raw text around the error position."""
        pos = getattr(err, "pos", None)
        status = getattr(response, "status", None)
        usage = getattr(response, "usage", None)
        print("=" * 70)
        print(f"[itinerary JSON parse FAILED] {err}")
        print(f"  response.status = {status}")
        print(f"  incomplete_details = {getattr(response, 'incomplete_details', None)}")
        print(f"  usage = {usage}")
        print(f"  output text length = {len(response_text)} chars")
        if pos is not None:
            lo, hi = max(0, pos - 200), min(len(response_text), pos + 200)
            print(f"  --- text around char {pos} (showing {lo}:{hi}) ---")
            print(repr(response_text[lo:hi]))
        print(f"  --- last 200 chars of output ---")
        print(repr(response_text[-200:]))
        print("=" * 70)

    @staticmethod
    def _extract_completed_json(response):
        """Return the model's text output, stripped of ```json fences, after
        verifying the response wasn't truncated.

        Longer trips (trip_duration > 3) produce a bigger itinerary JSON. If the
        model hits its output-token cap it stops mid-JSON, and json.loads then
        fails deep in the string with a misleading "Expecting ',' delimiter"
        error. The Responses API flags this as an incomplete status with reason
        'max_output_tokens' — surface that as a clear error instead."""
        status = getattr(response, "status", None)
        if status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            if reason == "max_output_tokens":
                raise ValueError(
                    "Itinerary generation exceeded the model output limit "
                    "(response truncated). Try a shorter trip_duration or raise "
                    "max_tokens / the model's max output tokens."
                )
            raise ValueError(f"Itinerary generation did not complete (status=incomplete, reason={reason}).")

        return response.output_text

    @staticmethod
    def _find_json_objects(text):
        """Yield every balanced top-level {...} substring in `text`, scanning
        string-aware so braces inside string values don't throw off the depth
        count. The model sometimes emits more than one JSON document (e.g. a
        broken partial one, some stray text, then a fresh complete one inside a
        new ```json fence) — this lets us recover each candidate independently."""
        i, n = 0, len(text)
        while i < n:
            if text[i] != "{":
                i += 1
                continue
            depth = 0
            in_string = False
            escaped = False
            start = i
            j = i
            closed = False
            while j < n:
                ch = text[j]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            yield text[start:j + 1]
                            closed = True
                            break
                j += 1
            # If this `{` balanced, resume after it; otherwise (a corrupted /
            # unclosed document, e.g. junk emitted mid-stream) don't skip to the
            # end — advance one char and try the next `{`, so a well-formed
            # document appearing later in the text is still recovered.
            i = (j + 1) if closed else (start + 1)

    @staticmethod
    def _parse_itinerary_json(response_text):
        """Best-effort parse of the model's itinerary output.

        Handles the real-world failure modes we've observed:
          - ```json fences around the JSON,
          - `// ...` comments and trailing commas (via _sanitize_llm_json),
          - raw newlines/tabs inside string values (json.loads strict=False),
          - MULTIPLE documents in one response — a broken one plus a good one —
            by extracting each balanced {...} object and keeping the best valid
            itinerary (the one with the most days).

        Returns the parsed dict, or raises ValueError with the last decode error
        if nothing usable is found."""
        candidates = list(ItenaryRecommendationSystem._find_json_objects(response_text))
        if not candidates:
            # Fall back to the whole string (fences stripped) so the error path
            # still reports a sensible decode failure.
            candidates = [response_text]

        best = None
        best_days = -1
        last_err = None
        for raw in candidates:
            try:
                cleaned = ItenaryRecommendationSystem._sanitize_llm_json(raw.strip())
                parsed = json.loads(cleaned, strict=False)
            except ValueError as e:
                last_err = e
                continue
            if not isinstance(parsed, dict) or "itinerary" not in parsed:
                continue
            days = len(parsed.get("itinerary") or [])
            if days > best_days:
                best, best_days = parsed, days

        if best is not None:
            return best
        if last_err is not None:
            raise last_err
        raise ValueError("No itinerary JSON object found in model response.")

    @staticmethod
    def _sanitize_llm_json(text):
        """Repair the two malformed-JSON patterns the model reliably emits
        (both are shown in this prompt's own output-format example, so the model
        imitates them): `// ...` line comments after a value, and trailing commas
        before a closing } or ]. Either one makes json.loads fail with a
        misleading "Expecting ',' delimiter" deep in the string — most often on
        longer trips, where there are simply more items to trip over.

        Both edits are string-aware: they skip anything inside a JSON string
        literal, so a `//` or comma that is part of a real value (a URL, an
        address) is never touched."""
        out = []
        in_string = False
        escaped = False
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if in_string:
                out.append(ch)
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                i += 1
                continue
            # Not inside a string.
            if ch == '"':
                in_string = True
                out.append(ch)
                i += 1
            elif ch == "/" and i + 1 < n and text[i + 1] == "/":
                # Drop a // line comment up to (not including) the newline.
                while i < n and text[i] != "\n":
                    i += 1
            elif ch == ",":
                # Look ahead past whitespace/newlines; if the next real char
                # closes a container, this comma is trailing — skip it.
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] in "}]":
                    i += 1  # skip the trailing comma
                else:
                    out.append(ch)
                    i += 1
            else:
                out.append(ch)
                i += 1
        return "".join(out)

    def _generate_trip_skeleton(self, user_preferences, top_places, top_restaurants, top_hotels):
        """Generate ONLY the trip-level fields (no day-by-day itinerary):
        name, description, city, state, price_estimated_range, notes, hotels,
        similar_places. Used by the incremental stream so trip info + hotels can
        be shown instantly, before day-by-day generation. Returns a dict with an
        empty `itinerary` list plus a `_token_usage` running total."""
        messages = self.generate_trip_skeleton_prompt(
            user_preferences, top_places, top_restaurants, top_hotels
        )
        response = self.client.responses.create(
            model=self.chat_deployment,
            input=messages,
            max_output_tokens=self.max_tokens,
            text={"format": {"type": "json_object"}},
        )
        response_text = self._extract_completed_json(response)
        # The skeleton has NO `itinerary` key, so parse the first balanced JSON
        # object directly (can't use _parse_itinerary_json, which requires one).
        skeleton = None
        for raw in self._find_json_objects(response_text):
            try:
                obj = json.loads(self._sanitize_llm_json(raw.strip()), strict=False)
            except ValueError:
                continue
            if isinstance(obj, dict):
                skeleton = obj
                break
        if skeleton is None:
            try:
                skeleton = json.loads(self._sanitize_llm_json(response_text.strip()), strict=False)
            except ValueError as e:
                self._log_json_decode_error(response, response_text, e)
                raise
        if not isinstance(skeleton, dict):
            skeleton = {}
        skeleton.setdefault('itinerary', [])
        skeleton.setdefault('hotels', [])
        skeleton.setdefault('similar_places', [])
        self._accumulate_usage(skeleton, response)
        return skeleton

    def generate_trip_skeleton_prompt(self, user_preferences, top_places, top_restaurants, top_hotels):
        trip_duration = user_preferences['trip_duration']
        hotel_pref = str(user_preferences.get('hotel_preference') or 'mid').strip().lower()
        tier_table = (
            "| tier    | hotel/night      |\n"
            "|---------|------------------|\n"
            "| budget  | <₹1,500          |\n"
            "| mid     | ₹1,500–₹4,500    |\n"
            "| high    | ₹4,500–₹10,000   |\n"
            "| luxury  | ₹10,000+         |"
        )
        system_content = """You are a senior human trip planner. Produce ONLY the trip-level summary
for a multi-day trip — NOT the day-by-day plan.

Your entire response must be a single raw JSON object. Start with { and end with }. No markdown,
no code fences, no explanation. Use EXACT key names shown. No trailing commas. No NaN — use "" for
missing strings. No comments inside JSON."""

        user_content = f"""Create the trip-level summary for this trip.

## User Preferences
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Places of interest: {user_preferences['places_of_interest']}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}
- Starting location: {user_preferences['user_location']}
- Travel month: {user_preferences['current_month']}
- Trip type: {user_preferences['trip_type']}
- Trip duration: {trip_duration} days
- Budget: {user_preferences['budget']}
- Hotel preference tier: {hotel_pref}

## Hotels Dataset (prefer these; supplement with your knowledge)
{top_hotels}

## Budget tiers
{tier_table}
The user's preferred tier is "{hotel_pref}". Make `selected` the best match for that tier.

## Rules
1. Output ONLY the trip-level fields below — do NOT include any day-by-day `itinerary` array.
2. Hotels are GROUPED by city. If the trip covers one city, provide one group. If multi-city (e.g. Rajasthan covering Jaipur + Jodhpur), provide one group per city with the correct from_day/to_day range.
3. Each hotel group has `selected` (best pick for user's tier) and `alternatives` (1–2 other tier options). Pick from the Hotels Dataset; use your knowledge only if a tier has no dataset candidate.
4. `description` is a short, engaging 2-3 sentence overview of the whole {trip_duration}-day trip.
5. `price_estimated_range` is the total per-head estimate; keep within {user_preferences['budget']} if realistic, else show the real range.
6. Provide 2-4 `similar_places`.

## Output Format (JSON)

{{
  "name": "Destination Name",
  "description": "Short engaging overview of the whole trip",
  "city": "City Name",
  "state": "State Name",
  "total_days": {trip_duration},
  "notes": "",
  "price_estimated_range": "e.g. ₹12,000–₹18,000 per head",
  "hotels": [
    {{
      "city": "City Name",
      "from_day": 1,
      "to_day": {trip_duration},
      "selected": {{"name": "Best Hotel", "type": "{hotel_pref}", "price_range": "₹X–₹Y/night", "rating": "4.3", "location": "City, State", "reason": "Best match for your tier", "link": "https://..."}},
      "alternatives": [
        {{"name": "Budget Option", "type": "budget", "price_range": "₹800–₹1,500/night", "rating": "4.0", "location": "City, State", "reason": "Affordable, central", "link": "https://..."}},
        {{"name": "Luxury Option", "type": "luxury", "price_range": "₹12,000+/night", "rating": "4.8", "location": "City, State", "reason": "Premium experience", "link": "https://..."}}
      ]
    }}
  ],
  "similar_places": [
    {{"placename": "Alternative 1", "description": "Why it fits", "state": "State Name", "price_estimated_range": "₹..."}},
    {{"placename": "Alternative 2", "description": "Why it fits", "state": "State Name", "price_estimated_range": "₹..."}}
  ]
}}
"""
        return [
            {"role": "system", "content": system_content},
            {"role": "user",   "content": user_content},
        ]

    def _generate_detailed_itinerary(self, user_preferences, top_places, top_hotels, top_restaurants, rest_slot_counts=None):
        places_trimmed = self._trim_for_prompt(top_places, self._PLACE_COLS_PROMPT, 30)
        hotels_trimmed = self._trim_for_prompt(top_hotels, self._HOTEL_COLS_PROMPT, 10)
        rests_trimmed  = self._trim_for_prompt(top_restaurants, self._REST_COLS_PROMPT, 20)
        messages = self.generate_travel_itinerary_prompt(user_preferences, places_trimmed, rests_trimmed, hotels_trimmed, rest_slot_counts=rest_slot_counts)
        print(f"Prompt length: {sum(len(m['content']) for m in messages)} chars")

        response = self.client.responses.create(
            model=self.chat_deployment,
            input=messages,
            max_output_tokens=self.max_tokens,
            text={"format": {"type": "json_object"}},
        )
        response_text = self._extract_completed_json(response)
        try:
            itinerary = self._parse_itinerary_json(response_text)
        except ValueError as e:
            # flask.json.loads raises the stdlib json.JSONDecodeError (a
            # ValueError subclass) — catch ValueError so this works regardless
            # of which `json` is imported at module top.
            self._log_json_decode_error(response, response_text, e)
            raise

        self._accumulate_usage(itinerary, response)

        # The model routinely ignores the requested day count and stops early.
        # Enforce it programmatically: if it returned fewer days than asked,
        # generate the missing days with follow-up calls and merge them in.
        return self._ensure_full_days(
            itinerary, user_preferences, places_trimmed, rests_trimmed, hotels_trimmed,
            rest_slot_counts=rest_slot_counts,
        )

    def _ensure_full_days(self, itinerary, user_preferences, places_trimmed, rests_trimmed, hotels_trimmed, rest_slot_counts=None):
        """Guarantee the itinerary has `trip_duration` days.

        The LLM often returns fewer days than requested no matter how the prompt
        is worded. Rather than trust it, we top up: for each missing day we ask
        the model for just those extra days, passing the places already used so
        it doesn't repeat them, and append the results. Renumbers days 1..N at
        the end so the sequence is always contiguous."""
        try:
            target = int(user_preferences.get('trip_duration'))
        except (TypeError, ValueError):
            return itinerary
        if not isinstance(itinerary, dict):
            return itinerary

        days = itinerary.get('itinerary')
        if not isinstance(days, list):
            return itinerary

        attempts = 0
        # Cap follow-ups so a stubborn model can't loop forever; each call can
        # return several days, so a few attempts covers large gaps.
        while len(days) < target and attempts < target:
            attempts += 1
            used_places = self._collect_used_place_names(days)
            missing = target - len(days)
            extra = self._generate_extra_days(
                user_preferences, places_trimmed, rests_trimmed, hotels_trimmed,
                start_day=len(days) + 1, num_days=missing, used_places=used_places,
                itinerary=itinerary, rest_slot_counts=rest_slot_counts,
            )
            if not extra:
                print(f"[days] top-up call returned no new days (have {len(days)}/{target}); stopping.")
                break
            days.extend(extra)

        # Renumber contiguously and trim any overshoot.
        days = days[:target] if len(days) > target else days
        for idx, day in enumerate(days, start=1):
            if isinstance(day, dict):
                day['day'] = idx
        itinerary['itinerary'] = days
        if len(days) < target:
            print(f"[days] WARNING: could only build {len(days)}/{target} days after top-up.")
        return itinerary

    @staticmethod
    def _collect_used_place_names(days):
        """Names of all place-type items already placed across the given day
        objects, so follow-up generations can avoid repeating them."""
        names = []
        for day in days:
            if not isinstance(day, dict):
                continue
            for item in day.get('timeline', []) or []:
                if not isinstance(item, dict) or item.get('type') != 'place':
                    continue
                name = str(item.get('name', '')).strip()
                if name and name not in names:
                    names.append(name)
        return names

    def _generate_extra_days(self, user_preferences, top_places, top_restaurants, top_hotels,
                             start_day, num_days, used_places, itinerary=None, rest_slot_counts=None):
        """Ask the model for exactly `num_days` additional day objects, numbered
        from `start_day`, excluding already-used places. Returns a list of day
        dicts (possibly fewer than requested), or [] on failure. Token usage is
        added to the running total on `itinerary` when provided."""
        messages = self.generate_extra_days_prompt(
            user_preferences, top_places, top_restaurants, top_hotels,
            start_day=start_day, num_days=num_days, used_places=used_places,
            rest_slot_counts=rest_slot_counts,
        )
        print(f"[days] requesting {num_days} extra day(s) starting at day {start_day}")
        response = self.client.responses.create(
            model=self.chat_deployment,
            input=messages,
            max_output_tokens=self.max_tokens,
            text={"format": {"type": "json_object"}},
        )
        if itinerary is not None:
            self._accumulate_usage(itinerary, response)
        response_text = self._extract_completed_json(response)
        try:
            parsed = self._parse_days_json(response_text)
        except ValueError as e:
            self._log_json_decode_error(response, response_text, e)
            return []
        return parsed

    @staticmethod
    def _parse_days_json(response_text):
        """Parse a follow-up response that returns extra days. Accepts either a
        bare JSON array of day objects or an object with an `itinerary` array.
        Reuses the same tolerant extraction as the main parser."""
        # Try object-with-itinerary first (matches the main format).
        for raw in ItenaryRecommendationSystem._find_json_objects(response_text):
            try:
                obj = json.loads(ItenaryRecommendationSystem._sanitize_llm_json(raw.strip()), strict=False)
            except ValueError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get('itinerary'), list):
                return obj['itinerary']
        # Fall back to a top-level array.
        cleaned = ItenaryRecommendationSystem._sanitize_llm_json(response_text.strip())
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        parsed = json.loads(cleaned, strict=False)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and isinstance(parsed.get('itinerary'), list):
            return parsed['itinerary']
        raise ValueError("Follow-up response did not contain a days array.")

    def generate_extra_days_prompt(self, user_preferences, top_places, top_restaurants, top_hotels,
                                   start_day, num_days, used_places, rest_slot_counts=None):
        used_block = ", ".join(used_places) if used_places else "(none yet)"
        hotel_pref = str(user_preferences.get('hotel_preference') or 'mid').strip().lower()
        tier_table = (
            "| tier   | breakfast | lunch   | dinner  |\n"
            "|--------|-----------|---------|----------|\n"
            "| budget | <₹200     | <₹350   | <₹400    |\n"
            "| mid    | <₹400     | <₹700   | <₹700    |\n"
            "| high   | <₹700     | <₹1,200 | <₹1,500  |\n"
            "| luxury | <₹1,200   | <₹2,500 | ₹3,000+  |"
        )

        system_content = f"""You are a senior human trip planner extending an existing itinerary with additional days.

Your entire response must be a single raw JSON object with one key `itinerary` containing an array of the new day objects. Start with {{ and end with }}. No markdown, no code fences, no explanation, no preamble.

No trailing commas. No NaN — use "" for missing strings, null for missing numbers. No comments inside JSON.

## TIMELINE structure — all items in ONE flat array per day
Each day's `timeline` is chronological. Every item has `type` (place / meal / hotel).
- `place`: sightseeing stop
- `meal`: includes `slot` ("breakfast"|"lunch"|"dinner")
- `hotel`: check_in / check_out event with `event` field

Every item needs: `type`, `name`, `suggested_time`, `duration`, `travel_from_prev`.
Place items add: `location`, `reason`, `activities`, `rating`, `opening_hours`.
Meal items add: `slot`, `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`.
Hotel items add: `event`, `note`.

`travel_from_prev` = null for first item of the day, otherwise {{"duration_mins": int, "mode": "walking|auto|cab", "note": "string"}}.

## meal_options — swappable alternatives per slot
Each day also has `meal_options` with "breakfast", "lunch", "dinner" arrays (2–3 alternatives each). No `travel_from_prev` on these.

## Budget tier for meals: {hotel_pref}
{tier_table}
All `approx_cost` values (in timeline and meal_options) must stay within the "{hotel_pref}" tier caps above.

## Smart timing rules
- Sunrise spots (beaches, ghats, forts, hilltops): schedule before 6 AM if day warrants it; breakfast follows at ~7:30–8 AM.
- Nightlife days: push dinner to 8–9 PM, add late-night venue after 10 PM, breakfast next day at 9–10 AM."""

        caps = self._MEAL_COST_CAPS.get(hotel_pref, (200, 350, 400))
        b_cap, l_cap, d_cap = caps
        sc = rest_slot_counts or {}
        n_b, n_l, n_d = sc.get('breakfast', 0), sc.get('lunch', 0), sc.get('dinner', 0)
        rest_coverage = (
            f"Dataset coverage for {hotel_pref} tier: "
            f"breakfast-eligible: {n_b}  |  lunch-eligible: {n_l}  |  dinner-eligible: {n_d}\n"
            f"- Prefer restaurants with Votes > 100 (more reviews = more reliable rating).\n"
            f"- Use the `suitable_slots` column to assign each restaurant to the correct meal slot.\n"
            f"- Cost column is 'cost for two' in INR — a Cost of 400 means ₹200 per person.\n"
            f"- If a slot has fewer than 3 dataset options, supplement with your own knowledge "
            f"but still keep costs within ₹{b_cap}/₹{l_cap}/₹{d_cap} per person "
            f"(breakfast/lunch/dinner) for the {hotel_pref} tier."
        )

        user_content = f"""Extend the itinerary with exactly {num_days} new day object(s), numbered {start_day} through {start_day + num_days - 1}.

## Trip context
- Destination / places of interest: {user_preferences['places_of_interest']}
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}
- Trip type: {user_preferences['trip_type']}
- Budget: {user_preferences['budget']}
- Hotel preference tier: {hotel_pref}

## Places already used — DO NOT reuse any of these
{used_block}

## Recommended Places (prefer unused ones; supplement with your knowledge)
{top_places}

## Restaurants Dataset
{top_restaurants}

{rest_coverage}

## Hotels Dataset
{top_hotels}

## Rules
1. Generate EXACTLY {num_days} day object(s) numbered {start_day} to {start_day + num_days - 1}.
2. Never reuse a place from the already-used list or repeat within these new days.
3. Each day: as many geographically close places as fit (min 2), 3 meal slots in timeline, meal_options dict.
4. NO separate `places_to_visit` or `meals` dict — everything goes into `timeline`.
5. All meal costs must respect the {hotel_pref} tier caps.

## Output Format

{{"itinerary": [
  {{
    "day": {start_day},
    "theme": "Short day theme",
    "day_summary": "One-line summary of the day's flow",
    "timeline": [
      {{
        "type": "meal",
        "slot": "breakfast",
        "name": "Restaurant Name",
        "cuisine": "Cuisine Type",
        "approx_cost": "₹150–₹250",
        "rating": "4.1",
        "location": "Area Name",
        "near_place": "First place of the day",
        "reason": "Quick breakfast before heading out",
        "suggested_time": "8:00 AM",
        "duration": "30–45 mins",
        "travel_from_prev": null
      }},
      {{
        "type": "place",
        "name": "Place Name",
        "location": "City, State",
        "reason": "why it fits",
        "activities": ["Activity 1"],
        "rating": "4.3",
        "opening_hours": "9:00 AM – 6:00 PM",
        "duration": "1.5–2 hours",
        "suggested_time": "9:00 AM",
        "travel_from_prev": {{"duration_mins": 15, "mode": "cab", "note": "~15 min cab"}}
      }},
      {{
        "type": "meal",
        "slot": "lunch",
        "name": "Restaurant Name",
        "cuisine": "Cuisine Type",
        "approx_cost": "₹400–₹600",
        "rating": "4.2",
        "location": "Area Name",
        "near_place": "Closest place at midday",
        "reason": "Good spot near your midday stop",
        "suggested_time": "1:00 PM",
        "duration": "45–60 mins",
        "travel_from_prev": {{"duration_mins": 10, "mode": "auto", "note": "~10 min auto"}}
      }},
      {{
        "type": "meal",
        "slot": "dinner",
        "name": "Restaurant Name",
        "cuisine": "Cuisine Type",
        "approx_cost": "₹500–₹800",
        "rating": "4.3",
        "location": "Area Name",
        "near_place": "Last place of the day",
        "reason": "Relaxed dinner to end the day",
        "suggested_time": "8:00 PM",
        "duration": "60–90 mins",
        "travel_from_prev": {{"duration_mins": 20, "mode": "cab", "note": "~20 min cab"}}
      }}
    ],
    "meal_options": {{
      "breakfast": [
        {{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹100–₹200", "rating": "4.0", "location": "Area", "reason": "Budget-friendly"}},
        {{"name": "Alt 2", "cuisine": "Type", "approx_cost": "₹200–₹300", "rating": "4.2", "location": "Area", "reason": "Good South Indian"}}
      ],
      "lunch": [
        {{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹350–₹500", "rating": "4.1", "location": "Area", "reason": "Popular local"}},
        {{"name": "Alt 2", "cuisine": "Type", "approx_cost": "₹400–₹600", "rating": "4.3", "location": "Area", "reason": "Great thali"}}
      ],
      "dinner": [
        {{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹500–₹700", "rating": "4.2", "location": "Area", "reason": "Open late"}},
        {{"name": "Alt 2", "cuisine": "Type", "approx_cost": "₹600–₹900", "rating": "4.4", "location": "Area", "reason": "Sea view"}}
      ]
    }}
  }}
]}}
"""
        return [
            {"role": "system", "content": system_content},
            {"role": "user",   "content": user_content},
        ]

    def generate_travel_itinerary_prompt(self, user_preferences, top_places, top_restaurants, top_hotels, rest_slot_counts=None):
        trip_duration = user_preferences['trip_duration']
        hotel_pref = str(user_preferences.get('hotel_preference') or 'mid').strip().lower()
        arrival_time = user_preferences.get('arrival_time', '').strip()
        departure_time = user_preferences.get('departure_time', '').strip()

        arrival_block = ""
        if arrival_time:
            arrival_block = f"""
## ARRIVAL / DEPARTURE — smart planner context
- User arrives on Day 1 at {arrival_time}. Think like a well-travelled friend:
  - If hotel check-in is not until later (e.g. noon), do a quick bag-drop (5 min check_in note) and head out.
  - If arriving 6am, a sunrise spot / beach / market before check-in is perfect.
  - If arriving 2pm, have lunch on the way, keep bags if needed, then explore before formal check-in.
  - First timeline item on Day 1 is the hotel check_in (or bag-drop) — travel_from_prev is null.
"""
        if departure_time:
            arrival_block += f"""- User departs on the LAST day at {departure_time}. Work backwards:
  - Leave 60–90 min buffer for travel to airport/station + 20–30 min packing.
  - Add hotel check_out before the last sightseeing block.
  - Never over-schedule the last day — only activities that genuinely fit before departure.
"""

        tier_table = (
            "| tier    | hotel/night      | breakfast | lunch   | dinner  |\n"
            "|---------|-----------------|-----------|---------|----------|\n"
            "| budget  | <₹1,500         | <₹200     | <₹350   | <₹400    |\n"
            "| mid     | ₹1,500–₹4,500   | <₹400     | <₹700   | <₹700    |\n"
            "| high    | ₹4,500–₹10,000  | <₹700     | <₹1,200 | <₹1,500  |\n"
            "| luxury  | ₹10,000+        | <₹1,200   | <₹2,500 | ₹3,000+  |"
        )

        system_content = f"""You are a senior human trip planner with 20 years of experience crafting real, enjoyable travel itineraries for Indian travellers. You think about trips the way a well-travelled friend would — not like a robot filling a schedule.

Your entire response must be a single raw JSON object. Start with {{ and end with }}. No markdown, no code fences, no explanation, no preamble. Nothing outside the JSON.

🚨 Use EXACT key names as shown in the output format. Even a small key change will break the app.
No trailing commas. No NaN — use "" for missing strings, null for missing numbers. No comments inside JSON.

## Planning philosophy

A good trip has rhythm. Not every day should be equally packed.

Day pacing guide:
- A standard day runs 9:00 AM to 9:00 PM. Deducting meals (~3 hrs) leaves ~9 hrs for places + travel.
- Full day: pack as many places as genuinely fit without rushing — could be 3, 4, or more for short/nearby.
- Relaxed day (5+ day trips, one middle day): ~5–6 hrs of sightseeing, choose slower experiences.
- 6+ day trips: alternate full and relaxed days. Never 3 packed days in a row.
- Never leave large idle gaps. If time remains after the last planned place, add one more nearby attraction.
- Travel is real: a 20-min cab + parking + walk-in = 35 min gone. Be honest about time.

## Smart timing rules (use your destination knowledge)

- **Sunrise spots** (beaches, ghats, forts, hilltops, mountain passes, river fronts): schedule BEFORE 6:00 AM for days the traveller would wake early. Follow with breakfast at ~7:30–8:00 AM after returning.
- **Nightlife days** (preferred_activities includes "Nightlife", OR destination is famous for clubs like Goa, Mumbai, Manali): push dinner to 8–9 PM, add club/bar/lounge after 10 PM as a `type:"place"` item, breakfast next morning at 9–10 AM (not 7 AM — they slept late). Adjust the full day's rhythm accordingly.
- A sunrise day starts at 5 AM; a party day ends at 1 AM. Meals shift to match — don't force 7 AM breakfast after a 1 AM night.

## Budget tiers — STRICTLY calibrate all meal costs and hotel selection
{tier_table}
User's tier: **{hotel_pref}**. Every meal's `approx_cost` and every `meal_options` alternative MUST be within the tier's caps above. Do not suggest a restaurant above the cap.

## TIMELINE structure — all items in ONE flat array per day

Each day's `timeline` is a flat, chronological array. Every item has `type` (place / meal / hotel).
- `place` items: sightseeing stops
- `meal` items: breakfast / lunch / dinner (include `slot` field)
- `hotel` items: check_in / check_out events (include `event` field: "check_in" or "check_out")

Day 1: timeline starts with hotel check_in (or bag-drop note if arrival_time given).
Last day: timeline ends with hotel check_out before final departure activities.
City-transition days (multi-city trips): last item = check_out old hotel, first item of next day = check_in new hotel.

Every timeline item has: `type`, `name`, `suggested_time`, `duration`, `travel_from_prev`.
- `travel_from_prev`: null for first item of the day, otherwise {{"duration_mins": int, "mode": "walking|auto|cab", "note": "human string"}}
- Place items also: `location`, `reason`, `activities`, `rating`, `opening_hours`
- Meal items also: `slot` ("breakfast"|"lunch"|"dinner"), `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`
- Hotel items also: `event` ("check_in"|"check_out"), `note`

## meal_options — swappable alternatives per slot (separate from timeline)

Each day also has `meal_options` dict with "breakfast", "lunch", "dinner" keys. Each is an array of 2–3 alternative restaurants the user can swap to. Same fields as meal timeline items (except no `travel_from_prev`). All alternatives must also respect the budget tier cap.

## Day count (initial generation — non-negotiable)

Output exactly {trip_duration} day objects in the `itinerary` array (day 1 through day {trip_duration}).
`suggested_places` are hints — fit them within the fixed days. Do NOT extend the day count.
{arrival_block}"""

        caps = self._MEAL_COST_CAPS.get(hotel_pref, (200, 350, 400))
        b_cap, l_cap, d_cap = caps
        sc = rest_slot_counts or {}
        n_b, n_l, n_d = sc.get('breakfast', 0), sc.get('lunch', 0), sc.get('dinner', 0)
        rest_coverage = (
            f"Dataset coverage for {hotel_pref} tier: "
            f"breakfast-eligible: {n_b}  |  lunch-eligible: {n_l}  |  dinner-eligible: {n_d}\n"
            f"- Prefer restaurants with Votes > 100 (more reviews = more reliable rating).\n"
            f"- Use the `suitable_slots` column to assign each restaurant to the correct meal slot.\n"
            f"- Cost column is 'cost for two' in INR — a Cost of 400 means ₹200 per person.\n"
            f"- If a slot has fewer than 3 dataset options, supplement with your own knowledge "
            f"but still keep costs within ₹{b_cap}/₹{l_cap}/₹{d_cap} per person "
            f"(breakfast/lunch/dinner) for the {hotel_pref} tier."
        )

        user_content = f"""Generate a {trip_duration}-day travel itinerary using the preferences and datasets below.

### 👤 User Preferences
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Places of interest: {user_preferences['places_of_interest']}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}
- Starting location: {user_preferences['user_location']}
- Travel month: {user_preferences['current_month']}
- Trip type: {user_preferences['trip_type']}
- Trip duration: {trip_duration} days
- Start date: {user_preferences.get('start_date', 'not specified')}
- Suggested places: {user_preferences['suggested_places']}
- Budget: {user_preferences['budget']}
- Hotel preference tier: {hotel_pref}

### 📍 Recommended Places (Use only these when available!)
{top_places}

### 🍽️ Restaurants Dataset
{top_restaurants}

{rest_coverage}

### 🏨 Hotels Dataset (Use only real data when available)
{top_hotels}

### 🧠 Rules

1. Output must be 100% valid JSON.
2. 🚨 The `itinerary` array must contain exactly {trip_duration} day objects (day 1 through {trip_duration}). Non-negotiable.
3. Include all `suggested_places` within {trip_duration} days.
4. Fill all days using the Recommended Places dataset first, then your own knowledge for nearby attractions.
4b. If destination cannot genuinely fill {trip_duration} days, output all days anyway and set `notes` with a friendly advisory.
5. Each day: as many geographically close places as fit (minimum 2), 3 meal slots, hotel check_in/check_out where appropriate. All in the `timeline` array — NO separate `places_to_visit` or `meals` dict.
6. Place items in timeline: `name`, `location`, `reason`, `activities`, `rating`, `opening_hours`, `duration`, `suggested_time`, `travel_from_prev`. Do not suggest a place on a day it is regularly closed (use start_date for day-of-week).
7. Meal items in timeline: `name`, `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`, `suggested_time`, `duration`, `slot`. Costs MUST be within tier cap for {hotel_pref}.
8. Hotels: grouped by city. If single-city trip → one group. If multi-city → one group per city with correct from_day/to_day. Each group has `selected` (best match for "{hotel_pref}" tier) and `alternatives` (1–2 other options from other tiers). Pick from Hotels Dataset; use your knowledge only if a tier has no dataset candidate. Include `name`, `type`, `price_range`, `rating`, `location`, `reason`, `link`.
9. `meal_options` per day: 2–3 swappable alternatives per slot (breakfast/lunch/dinner). Must respect tier cap.
10. Keep travel flow linear — no A→B→A routing. Order places by opening time; sunset/night spots last.
11. The trip starts on "{user_preferences.get('start_date', '')}". Use this for day-of-week calculations.
12. No placeholder text ("TBD", "N/A"). No comments, markdown, or explanation — only JSON.
13. Include `similar_places` (2–3 alternative destinations).

### 📦 Output Format (JSON — single day shown, repeat for all {trip_duration} days)

{{
  "itinerary": [
    {{
      "day": 1,
      "theme": "Short day theme",
      "day_summary": "One-line summary e.g. Sunrise beach → temple → lunch → fort → dinner by the sea",
      "timeline": [
        {{
          "type": "hotel",
          "event": "check_in",
          "name": "Hotel Name",
          "suggested_time": "11:00 AM",
          "duration": "15 mins",
          "travel_from_prev": null,
          "note": "Check in and freshen up"
        }},
        {{
          "type": "place",
          "name": "Place Name",
          "location": "City, State",
          "reason": "Why it fits the trip",
          "activities": ["Activity 1", "Activity 2"],
          "rating": "4.3",
          "opening_hours": "9:00 AM – 6:00 PM",
          "duration": "1.5–2 hours",
          "suggested_time": "11:30 AM",
          "travel_from_prev": {{"duration_mins": 20, "mode": "cab", "note": "~20 min cab from hotel"}}
        }},
        {{
          "type": "meal",
          "slot": "lunch",
          "name": "Restaurant Name",
          "cuisine": "Cuisine Type",
          "approx_cost": "₹400–₹600",
          "rating": "4.2",
          "location": "Area Name",
          "near_place": "Closest place that day",
          "reason": "Great local spot near your midday stop",
          "suggested_time": "1:30 PM",
          "duration": "45–60 mins",
          "travel_from_prev": {{"duration_mins": 10, "mode": "auto", "note": "~10 min auto"}}
        }},
        {{
          "type": "meal",
          "slot": "dinner",
          "name": "Restaurant Name",
          "cuisine": "Cuisine Type",
          "approx_cost": "₹600–₹900",
          "rating": "4.4",
          "location": "Area Name",
          "near_place": "Last place of the day",
          "reason": "Relaxed dinner to end the day",
          "suggested_time": "8:00 PM",
          "duration": "60–90 mins",
          "travel_from_prev": {{"duration_mins": 15, "mode": "cab", "note": "~15 min cab"}}
        }}
      ],
      "meal_options": {{
        "breakfast": [
          {{"name": "Alt Restaurant 1", "cuisine": "Type", "approx_cost": "₹150–₹250", "rating": "4.1", "location": "Area", "reason": "Quick and nearby"}},
          {{"name": "Alt Restaurant 2", "cuisine": "Type", "approx_cost": "₹200–₹300", "rating": "4.0", "location": "Area", "reason": "Good veg options"}}
        ],
        "lunch": [
          {{"name": "Alt Restaurant 1", "cuisine": "Type", "approx_cost": "₹350–₹500", "rating": "4.3", "location": "Area", "reason": "Popular local choice"}},
          {{"name": "Alt Restaurant 2", "cuisine": "Type", "approx_cost": "₹400–₹600", "rating": "4.2", "location": "Area", "reason": "Good seafood"}}
        ],
        "dinner": [
          {{"name": "Alt Restaurant 1", "cuisine": "Type", "approx_cost": "₹500–₹800", "rating": "4.4", "location": "Area", "reason": "Rooftop view"}},
          {{"name": "Alt Restaurant 2", "cuisine": "Type", "approx_cost": "₹600–₹900", "rating": "4.3", "location": "Area", "reason": "Live music"}}
        ]
      }}
    }}
  ],
  "hotels": [
    {{
      "city": "City Name",
      "from_day": 1,
      "to_day": {trip_duration},
      "selected": {{
        "name": "Best Hotel for Tier",
        "type": "{hotel_pref}",
        "price_range": "₹X–₹Y/night",
        "rating": "4.3",
        "location": "City, State",
        "reason": "Best match for your {hotel_pref} tier preference",
        "link": "https://..."
      }},
      "alternatives": [
        {{"name": "Budget Option", "type": "budget", "price_range": "₹800–₹1,500/night", "rating": "4.0", "location": "City, State", "reason": "Affordable, central", "link": "https://..."}},
        {{"name": "Luxury Option", "type": "luxury", "price_range": "₹12,000+/night", "rating": "4.8", "location": "City, State", "reason": "Premium experience", "link": "https://..."}}
      ]
    }}
  ],
  "name": "Destination Name",
  "description": "Short description of the destination",
  "total_days": {trip_duration},
  "notes": "",
  "price_estimated_range": "give the total price range estimated per head. It should be in the range of {user_preferences['budget']} if the price range actually comes in the user's budget, otherwise show the actual price range.",
  "state": "State Name",
  "city": "City Name",
  "similar_places": [
    {{
      "placename": "Alternative Destination 1",
      "description": "Why this is a good fit based on user's preferences",
      "state": "State Name",
      "price_estimated_range": "₹X,XXX–₹X,XXX per person"
    }}
  ]
}}
"""
        return [
            {"role": "system", "content": system_content},
            {"role": "user",   "content": user_content},
        ]

    # kept for backward compat — not used by any caller after the refactor
    def _generate_travel_itinerary_prompt_legacy(self, user_preferences, top_places, top_restaurants, top_hotels):
        prompt = f"""placeholder"""

        return textwrap.dedent(prompt)

    def generate_edit_itinerary_prompt(self, user_preferences, top_places, top_restaurants, top_hotels, must_include_places, rest_slot_counts=None):
        must_include_block = "\n".join(f"  - {name}" for name in must_include_places) or "  (none specified)"
        trip_duration = user_preferences['trip_duration']
        hotel_pref = str(user_preferences.get('hotel_preference') or 'mid').strip().lower()
        arrival_time = user_preferences.get('arrival_time', '').strip()
        departure_time = user_preferences.get('departure_time', '').strip()

        arrival_block = ""
        if arrival_time:
            arrival_block += f"- User arrives on Day 1 at {arrival_time}. Reason like a smart planner: bag-drop before check-in, sunrise spot if arriving early, lunch on the way if arriving at 2pm.\n"
        if departure_time:
            arrival_block += f"- User departs on the last day at {departure_time}. Work backwards: 60–90 min buffer for transport + 20–30 min packing. Include hotel check_out item. Don't over-schedule.\n"

        tier_table = (
            "| tier   | breakfast | lunch   | dinner  |\n"
            "|--------|-----------|---------|----------|\n"
            "| budget | <₹200     | <₹350   | <₹400    |\n"
            "| mid    | <₹400     | <₹700   | <₹700    |\n"
            "| high   | <₹700     | <₹1,200 | <₹1,500  |\n"
            "| luxury | <₹1,200   | <₹2,500 | ₹3,000+  |"
        )

        system_content = f"""You are a senior human trip planner with 20 years of experience crafting real, enjoyable travel itineraries for Indian travellers.

This is an EDIT request. The user has explicitly chosen specific places. Your primary job is to honour every must-include place — even if it means adding extra days.

Your entire response must be a single raw JSON object. Start with {{ and end with }}. No markdown, no code fences, no explanation. Use EXACT key names shown. No trailing commas. No NaN. No comments inside JSON.

## TIMELINE structure — all items in ONE flat array per day
Each day's `timeline` is chronological. Every item has `type` (place / meal / hotel).
- `place`: `name`, `location`, `reason`, `activities`, `rating`, `opening_hours`, `duration`, `suggested_time`, `travel_from_prev`
- `meal`: `slot` ("breakfast"|"lunch"|"dinner"), `name`, `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`, `suggested_time`, `duration`, `travel_from_prev`
- `hotel`: `event` ("check_in"|"check_out"), `name`, `suggested_time`, `duration`, `travel_from_prev`, `note`

`travel_from_prev` = null for first item, else {{"duration_mins": int, "mode": "walking|auto|cab", "note": "string"}}.

## meal_options — swappable alternatives per slot
Each day has `meal_options` with "breakfast", "lunch", "dinner" arrays (2–3 alternatives, no `travel_from_prev`).

## Budget tier: {hotel_pref}
{tier_table}
All `approx_cost` values must stay within the "{hotel_pref}" tier caps.

## Smart timing
- Sunrise spots: before 6 AM; breakfast follows at ~7:30–8 AM.
- Nightlife: dinner at 8–9 PM, late-night venue after 10 PM, breakfast next day 9–10 AM.
{arrival_block}
## Day count (edit — flexible)
First try to fit all must-include places within {trip_duration} days. If they don't fit, extend by the minimum extra days needed and update `total_days` with a friendly `notes` message."""

        caps = self._MEAL_COST_CAPS.get(hotel_pref, (200, 350, 400))
        b_cap, l_cap, d_cap = caps
        sc = rest_slot_counts or {}
        n_b, n_l, n_d = sc.get('breakfast', 0), sc.get('lunch', 0), sc.get('dinner', 0)
        rest_coverage = (
            f"Dataset coverage for {hotel_pref} tier: "
            f"breakfast-eligible: {n_b}  |  lunch-eligible: {n_l}  |  dinner-eligible: {n_d}\n"
            f"- Prefer restaurants with Votes > 100 (more reviews = more reliable rating).\n"
            f"- Use the `suitable_slots` column to assign each restaurant to the correct meal slot.\n"
            f"- Cost column is 'cost for two' in INR — a Cost of 400 means ₹200 per person.\n"
            f"- If a slot has fewer than 3 dataset options, supplement with your own knowledge "
            f"but still keep costs within ₹{b_cap}/₹{l_cap}/₹{d_cap} per person "
            f"(breakfast/lunch/dinner) for the {hotel_pref} tier."
        )

        user_content = f"""Rebuild this travel itinerary. Every must-include place MUST appear in the final plan.

## User Preferences
- Places of interest: {user_preferences['places_of_interest']}
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}
- Starting location: {user_preferences['user_location']}
- Travel month: {user_preferences['current_month']}
- Trip type: {user_preferences['trip_type']}
- Trip duration: {trip_duration} days (may be extended)
- Start date: {user_preferences.get('start_date', 'not specified')}
- Budget: {user_preferences['budget']}
- Hotel preference tier: {hotel_pref}

## Places that MUST be included (hard requirement)
{must_include_block}

## Recommended Places
{top_places}

## Restaurants Dataset
{top_restaurants}

{rest_coverage}

## Hotels Dataset
{top_hotels}

## Rules
1. Every must-include place must appear in `timeline` (as `type:"place"`) somewhere across the days.
2. Group geographically close must-include places on the same day.
3. Extend trip if must-include places don't fit; update `total_days` and set `notes`.
4. Fill remaining slots with dataset places or your knowledge (min 2 places/day).
5. Each day: places + meals (3 slots) + hotel events all in `timeline`. Also include `meal_options`.
6. Hotels grouped by city: `selected` (best for "{hotel_pref}" tier) + `alternatives`. Multi-city = one group per city.
7. Day 1 starts with hotel check_in (or bag-drop). Last day ends with hotel check_out before departure activities.
8. No repeated places, hotels, or restaurants.
9. Linear travel flow — no A→B→A.
10. No placeholder text.

## Output Format

{{
  "itinerary": [
    {{
      "day": 1,
      "theme": "Short day theme",
      "day_summary": "One-line summary",
      "timeline": [
        {{"type": "hotel", "event": "check_in", "name": "Hotel Name", "suggested_time": "11:00 AM", "duration": "15 mins", "travel_from_prev": null, "note": "Check in and freshen up"}},
        {{"type": "place", "name": "Must-include Place", "location": "City, State", "reason": "Why it fits", "activities": ["Activity"], "rating": "4.5", "opening_hours": "9:00 AM – 6:00 PM", "duration": "2 hours", "suggested_time": "11:30 AM", "travel_from_prev": {{"duration_mins": 20, "mode": "cab", "note": "~20 min cab"}}}},
        {{"type": "meal", "slot": "lunch", "name": "Restaurant", "cuisine": "Type", "approx_cost": "₹400–₹600", "rating": "4.2", "location": "Area", "near_place": "Must-include Place", "reason": "Great local spot", "suggested_time": "1:30 PM", "duration": "45–60 mins", "travel_from_prev": {{"duration_mins": 10, "mode": "auto", "note": "~10 min"}}}},
        {{"type": "meal", "slot": "dinner", "name": "Restaurant", "cuisine": "Type", "approx_cost": "₹500–₹800", "rating": "4.3", "location": "Area", "near_place": "Last place", "reason": "Relaxed dinner", "suggested_time": "8:00 PM", "duration": "60–90 mins", "travel_from_prev": {{"duration_mins": 15, "mode": "cab", "note": "~15 min cab"}}}}
      ],
      "meal_options": {{
        "breakfast": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹150–₹250", "rating": "4.0", "location": "Area", "reason": "Quick option"}}],
        "lunch": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹350–₹500", "rating": "4.1", "location": "Area", "reason": "Popular local"}}],
        "dinner": [{{"name": "Alt 1", "cuisine": "Type", "approx_cost": "₹500–₹700", "rating": "4.2", "location": "Area", "reason": "Open late"}}]
      }}
    }}
  ],
  "hotels": [
    {{
      "city": "City Name",
      "from_day": 1,
      "to_day": {trip_duration},
      "selected": {{"name": "Best Hotel", "type": "{hotel_pref}", "price_range": "₹X–₹Y/night", "rating": "4.3", "location": "City, State", "reason": "Best match for your tier", "link": "https://..."}},
      "alternatives": [
        {{"name": "Budget Option", "type": "budget", "price_range": "₹800–₹1,500/night", "rating": "4.0", "location": "City, State", "reason": "Affordable", "link": "https://..."}},
        {{"name": "Luxury Option", "type": "luxury", "price_range": "₹12,000+/night", "rating": "4.8", "location": "City, State", "reason": "Premium", "link": "https://..."}}
      ]
    }}
  ],
  "name": "Destination Name",
  "description": "2–3 line description",
  "total_days": {trip_duration},
  "notes": "",
  "price_estimated_range": "₹X,XXX–₹X,XXX per person",
  "state": "State Name",
  "city": "City Name",
  "similar_places": [
    {{"placename": "Alternative Destination", "description": "Why it fits", "state": "State Name", "price_estimated_range": "₹X,XXX–₹X,XXX per person"}}
  ]
}}
"""
        return [
            {"role": "system", "content": system_content},
            {"role": "user",   "content": user_content},
        ]


    def save_similar_places(self, similar_places):
        csv_file = 'similar_places.csv'
        try:
            existing_df = pd.read_csv(csv_file)
        except FileNotFoundError:
            existing_df = pd.DataFrame(columns=['placename', 'description', 'state', 'image', 'price_estimated_range'])

        for place in similar_places:
            placename = place['placename']
            # Check if it's a new place or missing image
            if placename not in existing_df['placename'].values or not place.get('image'):
                # Image filling is handled by scripts/fill_missing_images.py (runs nightly).
                # The block below was the inline Gemini/Wikimedia approach — kept for reference.
                # try:
                #     image_path = self.image_generator.generate_and_save_image(placename)
                #     with open(image_path, 'rb') as image_file:
                #         response = requests.post(
                #             "https://travelens.in/app/upload.php",
                #             files={'file': image_file}
                #         )
                #         if response.status_code == 200:
                #             res = response.json()
                #             place['image'] = res['path']
                #         else:
                #             place['image'] = ''
                # except Exception as e:
                #     print(f"Error generating or uploading image for {placename}: {str(e)}")
                #     place['image'] = ''
                place['image'] = ''

        try:
            similar_places_df = pd.DataFrame(similar_places)
            # Ensure 'image' column has no NaNs or Nones
            similar_places_df['image'] = similar_places_df['image'].fillna('').replace({None: ''})

            # Filter out places already present
            new_places_df = similar_places_df[~similar_places_df['placename'].isin(existing_df['placename'])]

            # ✅ Save only if new data is available
            if not new_places_df.empty:
                updated_df = pd.concat([existing_df, new_places_df], ignore_index=True)
                updated_df.to_csv(csv_file, index=False)
                self.update_similar_places()
            else:
                print("No new similar places to update.")
        except Exception as e:
            print(f"Error saving similar places to CSV: {str(e)}")


    def save_new_places(self, itinerary):
        """Persist new entities from the itinerary into their respective tables:
        places_to_visit -> places, hotels -> hotels, restaurants -> restaurants.
        Only inserts entities not already present (by name, case-insensitive).
        Runs in a background thread, so it uses its own dedicated DB connection."""
        try:
            conn = new_connection()  # autocommit, dedicated to this thread
            cursor = conn.cursor()
        except Exception as e:
            print(f"[save_new_places] DB connection failed: {e}")
            return

        try:
            self._save_new_places_to_db(cursor, itinerary)
            self._save_new_hotels_to_db(cursor, itinerary)
            self._save_new_restaurants_to_db(cursor, itinerary)
            conn.commit()
        except Exception as e:
            print(f"[save_new_places] error: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def _parse_city_state(location):
        """Parse an LLM "City, State" location string -> (city_lower, state)."""
        city = state = None
        if location:
            parts = [p.strip() for p in str(location).split(',')]
            if parts and parts[0]:
                city = parts[0].lower()
            if len(parts) > 1 and parts[1]:
                state = parts[1].strip()
        return city, state

    @staticmethod
    def _to_decimal(value):
        """Extract the first number from an LLM value like '4.5' or '₹400–₹600'."""
        if value is None:
            return None
        m = re.search(r'\d+(?:\.\d+)?', str(value).replace(',', ''))
        return float(m.group()) if m else None

    def _save_new_places_to_db(self, cursor, itinerary):
        """timeline place items -> places table (resolving/creating the city)."""
        candidates = []
        for day in itinerary.get('itinerary', []):
            for item in day.get('timeline', []):
                if item.get('type') != 'place':
                    continue
                name = str(item.get('name', '')).strip()
                if name:
                    candidates.append((name, item.get('location', ''), item.get('activities'),
                                       item.get('lat'), item.get('lon'), item.get('full_address')))
        if not candidates:
            return

        cursor.execute("SELECT LOWER(name) FROM places")
        existing = {row[0] for row in cursor.fetchall()}
        inserted, seen = 0, set()
        for name, location, activities, lat, lon, full_address in candidates:
            key = name.lower()
            if key in existing or key in seen:
                continue
            seen.add(key)
            try:
                city, state = self._parse_city_state(location)
                city_id = self._resolve_city_id(cursor, city, state)
                famous = ", ".join(activities) if isinstance(activities, list) and activities else None
                cursor.execute(
                    "INSERT INTO places (name, display_name, city_id, famous_activities, lat, lon, full_address) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key, name, city_id, famous, lat, lon, full_address or None),
                )
                inserted += 1
            except Exception as e:
                print(f"[save_new_places] failed to insert place '{name}': {e}")
        print(f"[save_new_places] inserted {inserted} new place(s).")

    def _save_new_hotels_to_db(self, cursor, itinerary):
        """hotels (grouped format) -> hotels table (matched on property_name)."""
        candidates = []
        for group in itinerary.get('hotels', []):
            if not isinstance(group, dict):
                continue
            # New grouped format: {city, selected, alternatives}
            for key in ('selected',):
                h = group.get(key)
                if isinstance(h, dict) and str(h.get('name', '')).strip():
                    candidates.append(h)
            for h in (group.get('alternatives') or []):
                if isinstance(h, dict) and str(h.get('name', '')).strip():
                    candidates.append(h)
        if not candidates:
            return

        cursor.execute("SELECT LOWER(property_name) FROM hotels WHERE property_name IS NOT NULL")
        existing = {row[0] for row in cursor.fetchall()}
        inserted, seen = 0, set()
        for hotel in candidates:
            name = str(hotel.get('name', '')).strip()
            key = name.lower()
            if key in existing or key in seen:
                continue
            seen.add(key)
            try:
                city, state = self._parse_city_state(hotel.get('location', ''))
                cursor.execute(
                    """INSERT INTO hotels
                       (property_name, property_type, city, state, site_review_rating, pageurl, lat, lon, full_address)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, str(hotel.get('type', '')).strip() or None,
                     city, state, self._to_decimal(hotel.get('rating')),
                     str(hotel.get('link', '')).strip() or None,
                     hotel.get('lat'), hotel.get('lon'),
                     hotel.get('full_address') or None),
                )
                inserted += 1
            except Exception as e:
                print(f"[save_new_places] failed to insert hotel '{name}': {e}")
        print(f"[save_new_places] inserted {inserted} new hotel(s).")

    def _save_new_restaurants_to_db(self, cursor, itinerary):
        """timeline meal items -> restaurants table (matched on name)."""
        candidates = []
        for day in itinerary.get('itinerary', []):
            for item in day.get('timeline', []):
                if item.get('type') != 'meal':
                    continue
                name = str(item.get('name', '')).strip()
                if name:
                    candidates.append(item)
        if not candidates:
            return

        cursor.execute("SELECT LOWER(name) FROM restaurants WHERE name IS NOT NULL")
        existing = {row[0] for row in cursor.fetchall()}
        inserted, seen = 0, set()
        for r in candidates:
            name = str(r.get('name', '')).strip()
            key = name.lower()
            if key in existing or key in seen:
                continue
            seen.add(key)
            try:
                city, _ = self._parse_city_state(r.get('location', ''))
                cost = self._to_decimal(r.get('approx_cost'))
                cursor.execute(
                    """INSERT INTO restaurants
                       (name, locality, city, cuisine, rating, cost, lat, lon, full_address)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (name, str(r.get('location', '')).strip() or None, city,
                     str(r.get('cuisine', '')).strip() or None,
                     self._to_decimal(r.get('rating')),
                     int(cost) if cost is not None else None,
                     r.get('lat'), r.get('lon'), r.get('full_address') or None),
                )
                inserted += 1
            except Exception as e:
                print(f"[save_new_places] failed to insert restaurant '{name}': {e}")
        print(f"[save_new_places] inserted {inserted} new restaurant(s).")

    def _resolve_city_id(self, cursor, city, state):
        """Return cities.id for `city` (lowercased name), creating the row if it
        doesn't exist. Returns None when no city name is available."""
        if not city:
            return None
        cursor.execute("SELECT id FROM cities WHERE name = ?", (city,))
        row = cursor.fetchone()
        if row:
            return row[0]

        # Create the city; link a state if we can match one.
        state_id = None
        if state:
            cursor.execute(
                "SELECT id FROM states WHERE LOWER(name) = ?", (state.lower(),)
            )
            srow = cursor.fetchone()
            if srow:
                state_id = srow[0]
        cursor.execute(
            "INSERT INTO cities (name, state_id) OUTPUT INSERTED.id VALUES (?, ?)", (city, state_id)
        )
        return int(cursor.fetchone()[0])


    def _calculate_similarity_scores(self, text1, text2):
        """Calculate similarity between two texts using embeddings"""
        embedding1 = self._encode([text1])[0]
        embedding1 = embedding1 / norm(embedding1)
        embedding2 = self._encode([text2])[0]
        embedding2 = embedding2 / norm(embedding2)
        return dot(embedding1, embedding2)

    @staticmethod
    def _validate_user_preferences(preferences):
        """Validate user preferences format and required fields"""
        required_fields = [
            'preferred_activities',
            'places_of_interest',
            'number_of_people',
            'travel_group_type',
            'food_preferences',
            'user_location',
            'current_month',
            'trip_type',
            'trip_duration'
        ]

        for field in required_fields:
            if field not in preferences:
                raise ValueError(f"Missing required field: {field}")

        if not isinstance(preferences['preferred_activities'], list):
            raise ValueError("preferred_activities must be a list")

        if not isinstance(preferences['number_of_people'], int):
            raise ValueError("number_of_people must be an integer")