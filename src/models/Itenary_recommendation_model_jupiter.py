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
            self.places_df['city'].str.lower() == city.lower()
        ]

        if edit_places.empty:
            # If no city matches, fall back to matching by state
            edit_places = self.places_df[
                self.places_df['state'].str.lower() == state.lower()
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
                places      = fut_places.result()
                hotels      = fut_hotels.result()
                restaurants = fut_rests.result()

            # Enrich images in background — benefits the next request; not needed for current response
            threading.Thread(target=self._enrich_place_images, args=(places,), daemon=True).start()

            # Generate detailed itinerary
            itinerary = self._generate_detailed_itinerary(
                user_preferences,
                places,
                hotels,
                restaurants,
            )

            return self._finalize_itinerary(itinerary, places, start_date=user_preferences.get('start_date'), user_preferences=user_preferences)

        except (KeyError, IndexError, TypeError) as e:
            print("Error while processing:", e)
            return {
                'status': 'error',
                'message': str(e)
            }

    def generate_itinerary_stream(self, user_preferences):
        """Same as generate_itinerary, but a generator that yields progress
        events as each stage completes. Each yielded value is a dict:
            {'event': 'progress', 'step': <key>, 'message': <text>}   (per stage)
            {'event': 'complete', 'data': <full result dict>}         (once, at end)
            {'event': 'error', 'message': <text>}                     (on failure)
        The caller (route) is responsible for serializing these to the wire."""
        try:
            user_preferences['suggested_places'] = user_preferences.get('suggested_places', [])
            user_preferences['budget'] = user_preferences.get('budget', "")

            yield {'event': 'progress', 'step': 'started', 'message': 'Starting your itinerary...'}

            # Fetch places, hotels, restaurants in parallel — they are independent
            with ThreadPoolExecutor(max_workers=3) as ex:
                fut_places = ex.submit(self._get_place_recommendations, user_preferences)
                fut_hotels = ex.submit(self._get_hotel_recommendations, user_preferences)
                fut_rests  = ex.submit(self._get_restaurant_recommendations, user_preferences)
                places      = fut_places.result()
                hotels      = fut_hotels.result()
                restaurants = fut_rests.result()

            # Enrich images in background — benefits the next request; not needed for current response
            threading.Thread(target=self._enrich_place_images, args=(places,), daemon=True).start()
            yield {'event': 'progress', 'step': 'places', 'message': 'Found places to visit'}
            yield {'event': 'progress', 'step': 'hotels', 'message': 'Picked hotels'}
            yield {'event': 'progress', 'step': 'restaurants', 'message': 'Picked restaurants'}

            # Emit early info event so the client can show destination name immediately.
            # Description is intentionally empty here — the complete event carries the
            # authoritative description from the generated itinerary JSON.
            destination = str(user_preferences.get('places_of_interest', '')).strip()
            dest_city, dest_state = self._parse_city_state(destination)
            yield {'event': 'progress', 'step': 'info', 'message': 'Preparing your trip details...'}
            yield {
                'event': 'info',
                'name': destination,
                'description': '',
                'city': dest_city or destination,
                'state': dest_state or '',
                'price_estimated_range': '',
                'total_days': user_preferences.get('trip_duration'),
                'notes': '',
            }

            yield {'event': 'progress', 'step': 'finalizing', 'message': 'Adding images and locations...'}
            dest_images = self._search_images_by_keywords(
                [destination, dest_city, dest_state], limit=5,
            )
            yield {'event': 'images', 'images': dest_images}

            yield {'event': 'progress', 'step': 'generating', 'message': 'Building your day-by-day plan...'}
            itinerary = self._generate_detailed_itinerary(
                user_preferences, places, hotels, restaurants,
            )

            result = self._finalize_itinerary(itinerary, places, start_date=user_preferences.get('start_date'), user_preferences=user_preferences)

            yield {'event': 'complete', 'data': result}

        except Exception as e:
            print("Error while streaming itinerary:", e)
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
                places      = fut_places.result()
                hotels      = fut_hotels.result()
                restaurants = fut_rests.result()
            threading.Thread(target=self._enrich_place_images, args=(places,), daemon=True).start()

            places_trimmed = self._trim_for_prompt(places, self._PLACE_COLS_PROMPT, 60)
            hotels_trimmed = self._trim_for_prompt(hotels, self._HOTEL_COLS_PROMPT, 60)
            rests_trimmed  = self._trim_for_prompt(restaurants, self._REST_COLS_PROMPT, 60)
            messages = self.generate_edit_itinerary_prompt(
                user_preferences, places_trimmed, rests_trimmed, hotels_trimmed, place_names
            )
            print(f"Edit prompt length: {sum(len(m['content']) for m in messages)} chars")

            response = self.client.responses.create(
                model=self.chat_deployment,
                input=messages,
                max_output_tokens=self.max_tokens,
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
            for p in day.get('places_to_visit', []):
                n = p.get('name', '').strip()
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
            'itinerary': [{'places_to_visit': places_list}],
            'hotels': [],
        }
        self._attach_lat_long(_wrapper)
        self._attach_db_fields(_wrapper)

        return places_list

    def _finalize_itinerary(self, itinerary, places, start_date=None, user_preferences=None):
        """Shared post-processing for generated/edited itineraries: resolves
        images for each place and the destination gallery, persists new places,
        attaches similar-place images, cleans NaNs, and builds the response."""
        try:
            if places.empty:
                places = self.getEditPlaces(itinerary['city'] , itinerary['state'])
            # Merge places, popular destinations, and similar places into a single array
            merged_places = []
            merged_places.extend(places.to_dict(orient='records'))  # Convert DataFrame to list of dicts
            popular_destinations = self.get_popular_destination()
            if popular_destinations:
                merged_places.extend(popular_destinations)
            place_image_map = {}

            for place in merged_places:
                image = place.get('image')
                if image and pd.notna(image):
                    placename = place.get('placename') or place.get('city') or place.get('name', '')
                    if placename:
                        place_image_map[placename] = image
            
            placename = itinerary['name']

            print("placenameplacename",placename,place_image_map)
           
            with open(os.path.join(_PROJECT_ROOT, 'similar_places.pkl'), 'rb') as f:
                similar_places_data = pickle.load(f)
            if placename not in place_image_map and placename in similar_places_data:
                place_image_map[placename] = similar_places_data.get(placename).get('image')

            if placename not in place_image_map:
                place_image_map[placename] = 'default' + str(random.randint(1, 7)) + '.webp'

            # Resolve the main destination's single image — used only as a
            # fallback for the parent-level images gallery below (it is no longer
            # returned as itinerary['image']).
            main_single_image = None
            if placename in place_image_map and pd.notna(place_image_map[placename]):
                main_single_image = place_image_map[placename]

            # Attach 4-5 images (_0 hero first) to every place in each day's
            # places_to_visit. Batch-fetch all place names via _get_images_for_places
            # (DB join, already ordered _0 first), then keyword-search as fallback.
            itin_city = itinerary.get('city')
            itin_state = itinerary.get('state')

            all_place_names = [
                str(place.get('name', '')).strip()
                for day in itinerary.get('itinerary', [])
                for place in day.get('places_to_visit', [])
                if str(place.get('name', '')).strip()
            ]
            place_images_db = self._get_images_for_places(all_place_names) if all_place_names else {}

            normalized_image_map = {
                str(name).strip().lower(): img
                for name, img in place_image_map.items()
            }

            for day in itinerary.get('itinerary', []):
                for place in day.get('places_to_visit', []):
                    place_name = str(place.get('name', '')).strip()
                    key = place_name.lower()

                    images = place_images_db.get(key, [])[:5]

                    if not images:
                        images = self._search_images_by_keywords([place_name], limit=5)

                    if not images:
                        # Fallback to the single image already in memory from places_df
                        single = normalized_image_map.get(key)
                        if single and not (not isinstance(single, list) and pd.isna(single)):
                            images = [single]

                    if not images:
                        images = ['default' + str(random.randint(1, 7)) + '.webp']

                    place['images'] = images

            # Parent level: all images for the main destination. The destination
            # is usually a city (not a row in `places`), so fall back to the
            # single main image resolved above when it has none of its own.
            main_images = self._get_images_for_places([placename]).get(
                str(placename).strip().lower(), []
            )
            if not main_images:
                # Search the images table by the destination name / city / state,
                # collecting up to 5 images for the gallery.
                main_images = self._search_images_by_keywords(
                    [placename, itinerary.get('city'), itinerary.get('state')],
                    limit=5,
                )
            if not main_images and main_single_image:
                main_images = [main_single_image]
            itinerary['images'] = main_images

            threading.Thread(target=self.save_similar_places, args=(itinerary['similar_places'],), daemon=True).start()

            # Update similar_places with images from similar_places.pkl
            try:
                for place in itinerary['similar_places']:
                    placename = place['placename']
                    matching_place = similar_places_data.get(placename)
                    if matching_place and pd.notna(matching_place['image']):
                            place['image'] = matching_place['image']
                    else:
                        place['image'] = 'default' + str(random.randint(1, 7)) + '.webp'
            except FileNotFoundError:
                print("similar_places.pkl not found. Skipping image update for similar places.")

            # Attach lat/lon/full_address to every place, hotel and restaurant —
            # from the DB when we have it, otherwise geocode via Nominatim and
            # store the result back on the row so the response and DB match.
            # Done BEFORE save_new_places so newly-inserted rows carry the same
            # coordinates that go out in the response.
            self._attach_lat_long(itinerary)
            self._compute_travel_times(itinerary)
            self._attach_db_fields(itinerary)

            if start_date:
                self._attach_weather(itinerary, start_date)

            # Persist any place the LLM returned that isn't yet in our places
            # table (off the request thread so the response isn't delayed). The
            # itinerary now has lat/lon/full_address attached, so new rows are
            # inserted with the same values returned in the response.
            threading.Thread(target=self.save_new_places, args=(itinerary,), daemon=True).start()

            # Convert NaN values to empty strings using deep search
            itinerary = json.loads(json.dumps(itinerary, default=lambda x: '' if pd.isna(x) else x))
            places = places.fillna('')

            # Ensure total_days is always an integer — LLM sometimes returns it
            # as a string or omits it entirely; fall back to actual day count.
            try:
                itinerary['total_days'] = int(itinerary['total_days'])
            except (TypeError, ValueError, KeyError):
                itinerary['total_days'] = len(itinerary.get('itinerary', []))

            # Lift the accumulated token usage out of the itinerary dict to the
            # result top level so store_itinerary can persist it, and drop the
            # internal key from the itinerary so it doesn't leak into the
            # client response / stored response_json.
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
            for place in day.get("places_to_visit", []):
                key = str(place.get("name", "")).strip().lower()
                db = db_lookup.get(key)
                if not db:
                    continue
                # Override LLM-generated fields with authoritative Google data
                if db.get("opening_hours"):
                    place["opening_hours"] = db["opening_hours"]
                if db.get("website_uri"):
                    place["website_uri"] = db["website_uri"]
                if db.get("phone_number"):
                    place["phone_number"] = db["phone_number"]
                # UI-only fields — never in the prompt, attached here for the frontend
                if db.get("google_maps_uri"):
                    place["google_maps_uri"] = db["google_maps_uri"]

                if db.get("full_address"):
                    place["full_address"] = db["full_address"]
                if db.get("good_for_children") is not None:
                    place["good_for_children"] = db["good_for_children"]
                if db.get("accessibility"):
                    place["accessibility"] = db["accessibility"]
                # Extra detail fields for place detail screen
                place["editorial_summary"] = db.get("editorial_summary") or ""
                place["review_summary"] = db.get("review_summary") or ""
                place["short_formatted_address"] = db.get("short_formatted_address") or ""
                place["google_rating"] = db.get("google_rating")
                place["google_rating_count"] = db.get("google_rating_count")

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
            for place in day.get('places_to_visit', []):
                apply(place, 'places', default_hint)
            for meal in day.get('meals', {}).values():
                if isinstance(meal, dict):
                    apply(meal, 'restaurants', default_hint)
        for hotel in itinerary.get('hotels', []):
            apply(hotel, 'hotels', default_hint)

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
            places = day.get('places_to_visit', [])
            for i, place in enumerate(places):
                if i == 0:
                    place['travel_from_prev'] = None
                    continue
                prev = places[i - 1]
                try:
                    dist_km = haversine(
                        float(prev['lat']), float(prev['lon']),
                        float(place['lat']), float(place['lon'])
                    )
                    mins = max(5, int(dist_km / 30 * 60))
                    mode = 'walking' if dist_km < 1.5 else 'auto' if dist_km < 4 else 'cab'
                    place['travel_from_prev'] = {
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

        # Collect all tasks: (place_obj, day_index, day_date_str)
        tasks = []
        for i, day in enumerate(itinerary.get('itinerary', [])):
            day_date = (trip_start + timedelta(days=i)).strftime("%Y-%m-%d")
            for place in day.get('places_to_visit', []):
                tasks.append((place, i, day_date))

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

        top_places = self.places_df

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
            top_places['city'].str.lower().apply(
                lambda city: any(part == str(city).lower() for part in location_parts)
            )
        ]

        if not city_matches.empty:
            # If there are city matches, use them only
            top_places = city_matches.copy()
        else:
            # Otherwise fall back to matching by state (exact match)
            top_places = top_places[
                top_places['state'].str.lower().apply(
                    lambda state: any(part == str(state).lower() for part in location_parts) )
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

    # Star-rating bands that define each hotel tier.
    _HOTEL_TIER_STARS = {
        'budget':  (0, 2),
        'mid':     (3, 3),
        'luxury':  (4, 5),
    }

    def _get_hotel_recommendations(self, user_preferences):
        """Get hotel recommendations based on selected places.

        Always returns all tiers so the LLM has real data for each tier bucket.
        When `hotel_preference` is set, preferred-tier rows are sorted to the
        top so the LLM encounters them first (prompt rule handles emphasis).
        """
        poi = user_preferences["places_of_interest"]
        city = ", ".join(poi) if isinstance(poi, list) else str(poi)

        live_hotels = self.places_client.search_hotels(city)
        if live_hotels:
            return pd.DataFrame(live_hotels).head(100)

        # City-only match (state bleed excluded — see comment in git history).
        city_part = city.split(',')[0].strip().lower()
        top_hotels = self.hotels_df[
            self.hotels_df['city'].str.lower().str.contains(city_part, na=False) |
            self.hotels_df['address'].str.lower().str.contains(city_part, na=False)
        ].sort_values('site_review_rating', ascending=False)

        pref = str(user_preferences.get('hotel_preference') or '').strip().lower()
        if pref in self._HOTEL_TIER_STARS:
            lo, hi = self._HOTEL_TIER_STARS[pref]
            is_pref = top_hotels['hotel_star_rating'].between(lo, hi, inclusive='both')
            # Float preferred tier to the top; all other rows follow sorted by rating.
            top_hotels = pd.concat([
                top_hotels[is_pref],
                top_hotels[~is_pref],
            ]).reset_index(drop=True)

        return top_hotels.head(100)


    def _get_restaurant_recommendations(self, user_preferences):
        """Get restaurant recommendations based on location and preferences"""
        poi = user_preferences["places_of_interest"]
        city = ", ".join(poi) if isinstance(poi, list) else str(poi)
        cuisine_raw = user_preferences["food_preferences"]
        cuisine = ", ".join(cuisine_raw) if isinstance(cuisine_raw, list) else str(cuisine_raw)

        live_restaurants = self.places_client.search_restaurants(city, cuisine)
        if live_restaurants:
            return pd.DataFrame(live_restaurants).head(100)

        # Fallback to CSV data — city-only match, same rationale as hotels.
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
        top_restaurants['rating_score'] = top_restaurants.apply(
            lambda x: self.weighted_restaurants_rating(x, C), axis=1
        )

        return top_restaurants.sort_values('rating_score', ascending=False).head(100)



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
    _REST_COLS_PROMPT  = ['Name', 'City', 'Cuisine', 'Rating', 'Cost', 'Locality']

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

    def _generate_detailed_itinerary(self, user_preferences, top_places, top_hotels, top_restaurants):
        places_trimmed = self._trim_for_prompt(top_places, self._PLACE_COLS_PROMPT, 30)
        hotels_trimmed = self._trim_for_prompt(top_hotels, self._HOTEL_COLS_PROMPT, 10)
        rests_trimmed  = self._trim_for_prompt(top_restaurants, self._REST_COLS_PROMPT, 20)
        messages = self.generate_travel_itinerary_prompt(user_preferences, places_trimmed, rests_trimmed, hotels_trimmed)
        print(f"Prompt length: {sum(len(m['content']) for m in messages)} chars")

        response = self.client.responses.create(
            model=self.chat_deployment,
            input=messages,
            max_output_tokens=self.max_tokens,
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
            itinerary, user_preferences, places_trimmed, rests_trimmed, hotels_trimmed
        )

    def _ensure_full_days(self, itinerary, user_preferences, places_trimmed, rests_trimmed, hotels_trimmed):
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
                itinerary=itinerary,
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
        """Names of all places already placed across the given day objects, so
        follow-up generations can avoid repeating them."""
        names = []
        for day in days:
            if not isinstance(day, dict):
                continue
            for place in day.get('places_to_visit', []) or []:
                name = str((place or {}).get('name', '')).strip()
                if name and name not in names:
                    names.append(name)
        return names

    def _generate_extra_days(self, user_preferences, top_places, top_restaurants, top_hotels,
                             start_day, num_days, used_places, itinerary=None):
        """Ask the model for exactly `num_days` additional day objects, numbered
        from `start_day`, excluding already-used places. Returns a list of day
        dicts (possibly fewer than requested), or [] on failure. Token usage is
        added to the running total on `itinerary` when provided."""
        messages = self.generate_extra_days_prompt(
            user_preferences, top_places, top_restaurants, top_hotels,
            start_day=start_day, num_days=num_days, used_places=used_places,
        )
        print(f"[days] requesting {num_days} extra day(s) starting at day {start_day}")
        response = self.client.responses.create(
            model=self.chat_deployment,
            input=messages,
            max_output_tokens=self.max_tokens,
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
                                   start_day, num_days, used_places):
        used_block = ", ".join(used_places) if used_places else "(none yet)"

        system_content = """You are a senior human trip planner extending an existing itinerary with additional days.

Your entire response must be a single raw JSON object with one key `itinerary` containing an array of the new day objects. Start with { and end with }. No markdown, no code fences, no explanation, no preamble.

No trailing commas. No NaN — use "" for missing strings, null for missing numbers. No comments inside JSON.

Meals are part of the experience. Set realistic suggested_time for each meal:
- Breakfast: 7:30–8:30 AM (30–45 min). Set suggested_time e.g. "8:00 AM".
- Lunch: derive from morning schedule, typically 12:30–1:30 PM (60–75 min). Set suggested_time accordingly.
- Dinner: after last place winds down, typically 7:30–9:00 PM (90 min). Set suggested_time accordingly.
suggested_time is required on every meal slot."""

        user_content = f"""Extend the itinerary with exactly {num_days} new day object(s), numbered {start_day} through {start_day + num_days - 1}.

## Trip context
- Destination / places of interest: {user_preferences['places_of_interest']}
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}
- Trip type: {user_preferences['trip_type']}
- Budget: {user_preferences['budget']}

## Places already used — DO NOT reuse any of these
{used_block}

## Recommended Places (prefer unused ones; supplement with your knowledge)
{top_places}

## Restaurants Dataset
{top_restaurants}

## Hotels Dataset
{top_hotels}

## Rules
1. Generate EXACTLY {num_days} day object(s) numbered {start_day} to {start_day + num_days - 1}.
2. Never reuse a place from the already-used list or repeat within these new days.
3. Each day: 2–3 geographically close places, 3 meal slots (breakfast/lunch/dinner), no hotels inside days.
4. For each place: `name`, `location`, `reason`, `activities`, `rating`, `opening_hours`, `duration`, `suggested_start_time`, `travel_from_prev`.
5. For each meal: `name`, `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`, `suggested_time`.
6. `suggested_time` on every meal is mandatory — derive from the day's actual time flow.

## Output Format

{{"itinerary": [
  {{
    "day": {start_day},
    "theme": "Short day theme",
    "day_summary": "One-line summary of the day's flow",
    "places_to_visit": [
      {{
        "name": "Place Name",
        "location": "City, State",
        "reason": "why it fits",
        "activities": ["Activity 1"],
        "rating": "4.3",
        "opening_hours": "9:00 AM – 6:00 PM",
        "duration": "1.5–2 hours",
        "suggested_start_time": "9:30 AM",
        "travel_from_prev": null
      }}
    ],
    "meals": {{
      "breakfast": {{
        "name": "Restaurant Name",
        "cuisine": "Cuisine Type",
        "approx_cost": "₹200–₹400",
        "rating": "4.2",
        "location": "Area Name",
        "near_place": "Closest place that day",
        "reason": "Light breakfast before heading out",
        "suggested_time": "8:00 AM"
      }},
      "lunch": {{
        "name": "Restaurant Name",
        "cuisine": "Cuisine Type",
        "approx_cost": "₹500–₹800",
        "rating": "4.3",
        "location": "Area Name",
        "near_place": "Closest place at midday",
        "reason": "Good spot near your midday stop",
        "suggested_time": "1:00 PM"
      }},
      "dinner": {{
        "name": "Restaurant Name",
        "cuisine": "Cuisine Type",
        "approx_cost": "₹800–₹1,200",
        "rating": "4.4",
        "location": "Area Name",
        "near_place": "Closest place from last activity",
        "reason": "Relaxed dinner to end the day",
        "suggested_time": "8:00 PM"
      }}
    }}
  }}
]}}
"""
        return [
            {"role": "system", "content": system_content},
            {"role": "user",   "content": user_content},
        ]

    def generate_travel_itinerary_prompt(self, user_preferences, top_places, top_restaurants, top_hotels):
        trip_duration = user_preferences['trip_duration']

        system_content = f"""You are a senior human trip planner with 20 years of experience crafting real, enjoyable travel itineraries for Indian travellers. You think about trips the way a well-travelled friend would — not like a robot filling a schedule.

Your entire response must be a single raw JSON object. Start with {{ and end with }}. No markdown, no code fences, no explanation, no preamble. Nothing outside the JSON.

🚨 Use EXACT key names as shown in the output format. `approx_cost` is not `cost`. `placename` is not `name`. `price_range` is not `budget`. Even a small key change will break the app.
No trailing commas. No NaN — use "" for missing strings, null for missing numbers. No comments inside JSON.

## Planning philosophy

A good trip has rhythm. Not every day should be equally packed.

Day pacing guide — schedule by available time, not by a fixed place count:
- A day runs roughly 9:00 AM to 8:00 PM (11 hours). Deducting meals (~3 hrs total) leaves ~8 hrs for places + travel.
- ALL days — including Day 1 and the final day — are FULL days. Travelers may arrive the night before or depart late at night, so assume every day is fully available for sightseeing.
- Full day: use the full ~8 hrs. Pack as many places as genuinely fit without rushing — could be 3, 4, or more for short/nearby attractions.
- Relaxed day (only applicable for 5+ day trips, one middle day): ~5–6 hrs for sightseeing. Choose fewer or shorter-duration places, but still fill the available time — do NOT limit to 1 place.
- 6+ day trips: alternate full and relaxed days. Never 3 packed days in a row.
- Never leave large idle gaps. If time remains after the last planned place, add one more nearby attraction.

Travel is real. A 20-min cab ride + finding parking + walking in = 35 min gone.

## Meal timing — 3 meals every day, in the `meals` dict

Each day must have exactly 3 entries in the `meals` dict: `breakfast`, `lunch`, and `dinner`.
- breakfast: near the hotel or on the way. Typically 7:30–8:30 AM.
- lunch: after 2–3 hours of morning sightseeing. Typically 12:30–1:30 PM. Derive honestly from the morning flow.
- dinner: after the last place winds down. Typically 7:30–9:00 PM. Set a realistic evening time.

`suggested_time` and `duration` are mandatory on every meal. Derive `suggested_time` from the actual day timeline — do not use a fixed template.

## Day count (initial generation — non-negotiable)

Output exactly {trip_duration} day objects in the `itinerary` array (day 1 through day {trip_duration}).
`suggested_places` are hints — fit them within the fixed days. If one won't fit, omit it gracefully. Do NOT extend the day count.
"""

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

### 📍 Recommended Places (Use only these when available!)
{top_places}

### 🍽️ Restaurants Dataset (Use only real data when available)
{top_restaurants}

### 🏨 Hotels Dataset (Use only real data when available)
{top_hotels}

### 🧠 Rules

1. The final output must be 100% valid JSON. Strictly no broken or partial JSON.
2. 🚨 DAY COUNT IS MANDATORY: The `itinerary` array must contain exactly {trip_duration} day objects (day 1 through day {trip_duration}). Never stop early. This is non-negotiable.
3. You must include all {user_preferences['suggested_places']} in the itinerary. If including them requires more than {trip_duration} days, still produce exactly {trip_duration} days (initial generation has fixed days).
4. To fill all {trip_duration} days, use every relevant place from the Recommended Places dataset first, then your own travel knowledge to add more real, distinct nearby attractions so every day's available time is filled. Add an extra place whenever the day's schedule has remaining time before dinner. Do not repeat places across days.
4b. If the destination genuinely cannot fill {trip_duration} days even after adding day-trips: still output all {trip_duration} days, but set `notes` to a short advisory suggesting the recommended number of days. If {trip_duration} days fits well, set `notes` to "".
5. Each day must include: as many geographically close places as fit within the day's available time (see pacing guide) — minimum 2, no fixed upper limit. Always include exactly 3 meal slots (breakfast/lunch/dinner in the `meals` dict). Hotels go at the TOP LEVEL, not inside days.
6. For each place include: `name`, `location`, `reason`, `activities`, `rating`, `opening_hours`, `duration`, `suggested_start_time`, `travel_from_prev`.
   - `opening_hours`: typical operating hours (e.g. "9:00 AM – 6:00 PM"). Use your knowledge; if unknown, "Check locally".
   - `rating`: from dataset when available; otherwise a realistic value.
   - `suggested_start_time`: derived from previous place start + duration + travel time. Be honest about travel time.
   - `travel_from_prev`: null for first place of the day. For others: {{"duration_mins": int, "mode": "walking|auto|cab", "note": "human string"}}. Walking < 1.5 km, auto 1.5–4 km, cab > 4 km.
   - Do not suggest a place on a day it is regularly closed (use start_date to calculate day-of-week).
7. For each meal include: `name`, `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`, `suggested_time`, `duration`.
   - `suggested_time` is mandatory — derive from the day's actual time flow (see system instructions).
   - `duration`: typical time spent (e.g. "30–45 mins" for breakfast, "45–60 mins" for lunch/dinner).
   - `near_place`: the closest place being visited that day.
   - Choose restaurants matching food preferences and budget.
8. Top-level `hotels`: exactly 3 options (budget / mid / luxury) for the whole trip. Include: `name`, `type`, `price_range`, `rating`, `location`, `reason`, `from_day`, `to_day`, `link`.
   - Use the `hotel_star_rating` column to assign tiers: 0–2 star = budget, 3 star = mid, 4–5 star = luxury.
   - Pick one hotel per tier strictly from the Hotels Dataset above. Only use your own knowledge for a tier if the dataset has zero candidates for it.
   - User hotel preference: "{user_preferences.get('hotel_preference') or 'all'}". If not "all", emphasise that tier — make it the strongest recommendation and pick a great match from the dataset for it.
   - `link`: use `pageurl` from the dataset when available; only generate a URL if the field is empty.
   - Estimate `price_range` from the star tier: budget ≈ ₹1,000–₹3,000/night, mid ≈ ₹3,000–₹8,000/night, luxury ≈ ₹8,000+/night.
9. On Day 1 and the final day, choose places/hotels closer to airport/train station.
10. Avoid repeating places, hotels, or restaurants on different days.
11. Keep travel flow linear — no A→B→A routing.
12. The trip starts on "{user_preferences.get('start_date', '')}". Use this to determine day-of-week for each day.
13. Order `places_to_visit` within each day by opening time — earliest-closing places first; sunset/night spots last.
14. Do NOT include comments, markdown, or explanation — only JSON output.
15. Do NOT add trailing commas.
16. If required fields are missing in datasets, generate realistic replacements using your travel knowledge.
17. Always generate a full response — no placeholder text like "TBD" or "N/A".
18. At the end of the JSON, include a `similar_places` list — 2–3 alternative Indian destinations matching user's vibe, activities, and trip type.
19. Don't add NaN if any field is not available. Leave it blank ("").

### 📦 Output Format (JSON)

The example below shows a single day. Repeat for every day from 1 to {trip_duration}.
Hotels go at the TOP LEVEL (not inside days) — 3 options covering budget/mid/luxury for the whole trip.

{{
  "itinerary": [
    {{
      "day": 1,
      "theme": "Short day theme",
      "day_summary": "One-line summary e.g. Morning temples → afternoon bazaar → riverside dinner",
      "places_to_visit": [
        {{
          "name": "Place Name",
          "location": "City, State",
          "reason": "Matches your interest in [e.g. temples, nature]",
          "activities": ["Activity 1", "Activity 2"],
          "rating": "X.X",
          "opening_hours": "9:00 AM – 6:00 PM",
          "duration": "1.5–2 hours",
          "suggested_start_time": "9:30 AM",
          "travel_from_prev": null
        }}
      ],
      "meals": {{
        "breakfast": {{
          "name": "Restaurant Name",
          "cuisine": "Cuisine Type",
          "approx_cost": "₹200–₹400",
          "rating": "X.X",
          "location": "Area Name",
          "near_place": "Closest place to visit that day",
          "reason": "Matches your food preference: {user_preferences['food_preferences']}",
          "suggested_time": "8:00 AM",
          "duration": "30–45 mins"
        }},
        "lunch": {{
          "name": "Restaurant Name",
          "cuisine": "Cuisine Type",
          "approx_cost": "₹500–₹800",
          "rating": "X.X",
          "location": "Area Name",
          "near_place": "Closest place at midday",
          "reason": "Good spot for lunch near your midday stop",
          "suggested_time": "1:00 PM",
          "duration": "45–60 mins"
        }},
        "dinner": {{
          "name": "Restaurant Name",
          "cuisine": "Cuisine Type",
          "approx_cost": "₹800–₹1,200",
          "rating": "X.X",
          "location": "Area Name",
          "near_place": "Closest place from last activity",
          "reason": "Relaxed dinner after the day's sightseeing",
          "suggested_time": "8:00 PM",
          "duration": "60–90 mins"
        }}
      }}
    }}
  ],
  "hotels": [
    {{
      "name": "Hotel Name",
      "type": "budget",
      "price_range": "₹1,000–₹3,000/night",
      "rating": "X.X",
      "location": "City, State",
      "reason": "Near visited places or transport hub. Good for {user_preferences['travel_group_type']}",
      "from_day": 1,
      "to_day": {trip_duration},
      "link": "Add valid Page URL from hotel dataset or generated link"
    }},
    {{
      "name": "Hotel Name",
      "type": "mid",
      "price_range": "₹3,000–₹8,000/night",
      "rating": "X.X",
      "location": "City, State",
      "reason": "Good value, near key attractions",
      "from_day": 1,
      "to_day": {trip_duration},
      "link": "Add valid Page URL from hotel dataset or generated link"
    }},
    {{
      "name": "Hotel Name",
      "type": "luxury",
      "price_range": "₹8,000+/night",
      "rating": "X.X",
      "location": "City, State",
      "reason": "Premium experience for {user_preferences['travel_group_type']}",
      "from_day": 1,
      "to_day": {trip_duration},
      "link": "Add valid Page URL from hotel dataset or generated link"
    }}
  ],
  "name": "Place Name",
  "description": "Short description of the place",
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
      "price_estimated_range": "same logic as above"
    }},
    {{
      "placename": "Alternative Destination 2",
      "description": "Why this is a good fit based on user's preferences",
      "state": "State Name",
      "price_estimated_range": "same logic as above"
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

    def generate_edit_itinerary_prompt(self, user_preferences, top_places, top_restaurants, top_hotels, must_include_places):
        must_include_block = "\n".join(f"  - {name}" for name in must_include_places) or "  (none specified)"
        trip_duration = user_preferences['trip_duration']

        system_content = f"""You are a senior human trip planner with 20 years of experience crafting real, enjoyable travel itineraries for Indian travellers.

This is an EDIT request. The user has explicitly chosen specific places they want in their itinerary. Your primary job is to honour every place on the must-include list — even if it means adding extra days. Think like a human planner who listens to what the traveller actually wants.

Your entire response must be a single raw JSON object. Start with {{ and end with }}. No markdown, no code fences, no explanation, no preamble. Nothing outside the JSON.

Use EXACT key names as shown in the output format. No trailing commas. No NaN — use "" for missing strings, null for missing numbers. No comments inside JSON.

## Planning philosophy

A good trip has rhythm. Not every day should be equally packed. Think like a human planner who knows when to pause.

Day pacing guide:
- 1–2 day trip: reasonably full days, not exhausting.
- 3 day trip: Day 1 = lighter arrival/orientation, Day 2 = full exploration, Day 3 = relaxed morning + departure-friendly afternoon.
- 4 day trip: Day 1 = light arrival, Day 2 = full, Day 3 = relaxed/leisure (1–2 places, long lunch, slow afternoon), Day 4 = easy departure morning.
- 5 day trip: Day 1 = light, Day 2 = full, Day 3 = full, Day 4 = relaxed leisure, Day 5 = easy departure day.
- 6+ day trips: alternate full and relaxed days. Never 3 packed days in a row.
- A "relaxed day" means 1–2 places max, one being a slow/nature/market/beach experience, a long unhurried lunch, maybe an evening stroll.

Travel is real. A 20-min cab ride + finding parking + walking in = 35 min gone. Be honest about time.

## Meal timing rules

Meals are part of the experience, not logistics.

Breakfast (30–45 min):
- Near the hotel or on the way to the first place. Typically 7:30–8:30 AM.
- Set `suggested_time` to a realistic time e.g. "8:00 AM".

Lunch (60–75 min):
- After 2–3 hours of morning sightseeing. Typically 12:30 PM – 1:30 PM.
- Derive the time honestly from the morning's actual schedule.
- Set `suggested_time` accordingly.

Dinner (90 min):
- After the last place of the day winds down. Typically 7:30 PM – 9:00 PM.
- Set `suggested_time` to a realistic evening time.
- Nothing planned after dinner.

`suggested_time` is required on every meal. Derive from the actual day timeline.

## Day count (edit — flexible)

First, try to fit all must-include places within {trip_duration} days.
If they do not all fit: extend the trip by the minimum number of extra days needed to include every must-include place. Update `total_days` to the new count and add a friendly `notes` message explaining the extension.
If they fit within {trip_duration} days: set `total_days` to {trip_duration} and `notes` to "".
"""

        user_content = f"""Rebuild this travel itinerary. The places listed under "Must Include" are a hard requirement — every single one must appear in the final plan.

## User Preferences
- Places of interest: {user_preferences['places_of_interest']}
- Preferred activities: {', '.join(user_preferences['preferred_activities'])}
- Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
- Food preferences: {user_preferences['food_preferences']}
- Starting location: {user_preferences['user_location']}
- Travel month: {user_preferences['current_month']}
- Trip type: {user_preferences['trip_type']}
- Trip duration: {trip_duration} days (may be extended if must-include places require it)
- Start date: {user_preferences.get('start_date', 'not specified')}
- Budget: {user_preferences['budget']}

## Places that MUST be included (hard requirement — do not drop, rename, or merge any)
{must_include_block}

## Recommended Places (supplement with your knowledge if needed)
{top_places}

## Restaurants Dataset (use real data when available)
{top_restaurants}

## Hotels Dataset (use real data when available)
{top_hotels}

## Rules

1. Every must-include place must appear in `places_to_visit` across the days. This is non-negotiable.
2. Distribute must-include places sensibly across days, grouping geographically close ones together, linear travel flow.
3. If they do not fit in {trip_duration} days, extend the trip (minimum extra days needed). Update `total_days` and add a friendly `notes` message about the extension.
4. After placing must-include places, fill remaining slots with other relevant places (dataset or your knowledge) so each full day has 2–3 geographically close places.
5. For each place include: `name`, `location`, `reason`, `activities`, `rating`, `opening_hours`, `duration`, `suggested_start_time`, `travel_from_prev`.
   - `suggested_start_time`: derived from previous place start + duration + travel. Be honest about travel time.
   - `travel_from_prev`: null for first place of the day. For others: {{"duration_mins": int, "mode": "walking|auto|cab", "note": "human string"}}. Walking < 1.5 km, auto 1.5–4 km, cab > 4 km.
   - Do not suggest a place on a day it is regularly closed (use start_date to calculate day-of-week).
6. For each meal include: `name`, `cuisine`, `approx_cost`, `rating`, `location`, `near_place`, `reason`, `suggested_time`.
   - `suggested_time` is mandatory. Derive from the actual day timeline.
   - Breakfast near hotel/on way to first place. Lunch after mid-morning sightseeing. Dinner after last place.
7. Hotels: exactly 3 options (budget / mid / luxury) at trip level, NOT inside any day. Include: `name`, `type`, `price_range`, `rating`, `location`, `reason`, `from_day`, `to_day`, `link`.
   - Use the `hotel_star_rating` column to assign tiers: 0–2 star = budget, 3 star = mid, 4–5 star = luxury.
   - Pick one hotel per tier strictly from the Hotels Dataset. Only use your own knowledge for a tier if the dataset has zero candidates for it.
   - User hotel preference: "{user_preferences.get('hotel_preference') or 'all'}". If not "all", emphasise that tier — make it the strongest recommendation and pick a great match from the dataset for it.
   - `link`: use `pageurl` from the dataset when available; only generate a URL if the field is empty.
   - Estimate `price_range` from the star tier: budget ≈ ₹1,000–₹3,000/night, mid ≈ ₹3,000–₹8,000/night, luxury ≈ ₹8,000+/night.
8. Day 1 and last day: choose places and hotels close to airport/train station.
9. No place, hotel, or restaurant repeated across days.
10. Keep travel flow linear — no A→B→A routing.
11. Order places within each day by opening time: earliest-closing first, sunset/night spots last.
12. `similar_places`: 2–3 alternative destinations matching the user's vibe and trip type.
13. `price_estimated_range`: total estimated per-person cost in ₹.
14. No placeholder text like "TBD" or "N/A".

## Output Format

Return only this JSON. No text before or after.

{{
  "itinerary": [
    {{
      "day": 1,
      "theme": "Short day theme",
      "day_summary": "One-line summary e.g. Must-see fort in the morning → old bazaar stroll → riverside dinner",
      "places_to_visit": [
        {{
          "name": "Place Name",
          "location": "City, State",
          "reason": "Why this fits",
          "activities": ["Activity 1"],
          "rating": "4.5",
          "opening_hours": "9:00 AM – 6:00 PM",
          "duration": "1.5–2 hours",
          "suggested_start_time": "9:30 AM",
          "travel_from_prev": null
        }}
      ],
      "meals": {{
        "breakfast": {{
          "name": "Restaurant Name",
          "cuisine": "Cuisine Type",
          "approx_cost": "₹200–₹400",
          "rating": "4.2",
          "location": "Area Name",
          "near_place": "Closest place to visit that day",
          "reason": "Light breakfast on the way to your first stop",
          "suggested_time": "8:00 AM"
        }},
        "lunch": {{
          "name": "Restaurant Name",
          "cuisine": "Cuisine Type",
          "approx_cost": "₹500–₹800",
          "rating": "4.4",
          "location": "Area Name",
          "near_place": "Closest place at midday",
          "reason": "Well-reviewed spot near your midday stop",
          "suggested_time": "1:15 PM"
        }},
        "dinner": {{
          "name": "Restaurant Name",
          "cuisine": "Cuisine Type",
          "approx_cost": "₹800–₹1,200",
          "rating": "4.5",
          "location": "Area Name",
          "near_place": "Closest place from last activity",
          "reason": "Relaxed dinner to end the day",
          "suggested_time": "8:00 PM"
        }}
      }}
    }}
  ],
  "hotels": [
    {{
      "name": "Budget Hotel Name",
      "type": "budget",
      "price_range": "₹1,000–₹3,000/night",
      "rating": "4.0",
      "location": "City, State",
      "reason": "Affordable, central to all days. Good for {user_preferences['travel_group_type']}",
      "from_day": 1,
      "to_day": {trip_duration},
      "link": "https://..."
    }},
    {{
      "name": "Mid-range Hotel Name",
      "type": "mid",
      "price_range": "₹3,000–₹8,000/night",
      "rating": "4.3",
      "location": "City, State",
      "reason": "Good value, near key attractions",
      "from_day": 1,
      "to_day": {trip_duration},
      "link": "https://..."
    }},
    {{
      "name": "Luxury Hotel Name",
      "type": "luxury",
      "price_range": "₹8,000+/night",
      "rating": "4.7",
      "location": "City, State",
      "reason": "Premium experience for {user_preferences['travel_group_type']}",
      "from_day": 1,
      "to_day": {trip_duration},
      "link": "https://..."
    }}
  ],
  "name": "Destination Name",
  "description": "2–3 line description of the destination",
  "total_days": {trip_duration},
  "notes": "",
  "price_estimated_range": "₹X,XXX–₹X,XXX per person",
  "state": "State Name",
  "city": "City Name",
  "similar_places": [
    {{
      "placename": "Alternative Destination",
      "description": "Why this fits the user's vibe and preferences",
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
                try:
                    # Generate and save image
                    image_path = self.image_generator.generate_and_save_image(placename)
                    
                    # Upload image
                    with open(image_path, 'rb') as image_file:
                        response = requests.post(
                            "https://travelens.in/app/upload.php",
                            files={'file': image_file}
                        )
                        if response.status_code == 200:
                            res = response.json()
                            place['image'] = res['path']
                        else:
                            place['image'] = ''
                except Exception as e:
                    print(f"Error generating or uploading image for {placename}: {str(e)}")
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
        """places_to_visit -> places (resolving/creating the city)."""
        candidates = []
        for day in itinerary.get('itinerary', []):
            for place in day.get('places_to_visit', []):
                name = str(place.get('name', '')).strip()
                if name:
                    candidates.append((name, place.get('location', ''), place.get('activities'),
                                       place.get('lat'), place.get('lon'), place.get('full_address')))
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
        """hotels -> hotels table (matched on property_name)."""
        candidates = []
        for hotel in itinerary.get('hotels', []):
            name = str(hotel.get('name', '')).strip()
            if name:
                candidates.append(hotel)
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
        """restaurants -> restaurants table (matched on name)."""
        candidates = []
        for day in itinerary.get('itinerary', []):
            for r in day.get('meals', {}).values():
                if not isinstance(r, dict):
                    continue
                name = str(r.get('name', '')).strip()
                if name:
                    candidates.append(r)
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