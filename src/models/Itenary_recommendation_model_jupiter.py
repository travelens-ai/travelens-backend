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
from integrations.generate_images import ImageGenerator
from integrations.api_integrations import GooglePlacesClient, ImageSearchClient, NominatimClient
from core.db import fetch_dicts, new_connection
import os
import random


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
        try:
            # Add the appropriate code here or remove the try block if           
            self._setup_models()
            print("Models initialized successfully.")
            self._load_data()
            self.schedule_popular_destination()
            self.schedule_similar_places()
            # threading.Thread(target=self.schedule_popular_destination, daemon=True).start()
            # threading.Thread(target=self.schedule_similar_places, daemon=True).start()
            return True
        except Exception as e:
            print(f"Initialization failed: {str(e)}")
            return False
    
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
            similar_places_df = pd.read_csv('similar_places.csv')

            final_places = {}
            for row in similar_places_df.itertuples(index=False):
                final_places[row.placename] = row._asdict()      
            # Save the DataFrame to a pickle file
            with open('similar_places.pkl', 'wb') as f:
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
            with open('popular_destination.csv', 'w') as f:
                f.truncate(0)

            # Save the top destinations to a CSV file
            top_destinations.to_csv('popular_destination.csv', index=False)

            # Save the preprocessed DataFrame to a pickle file
            with open('popular_destination.pkl', 'wb') as f:
                pickle.dump(top_destinations, f)

            print("Popular destinations saved successfully.")
        except Exception as e:
            print(f"Error generating popular destinations: {str(e)}")
        
    def get_popular_destination(self):
        """Load the most popular top 10 destinations"""
        try:
            with open('popular_destination.pkl', 'rb') as f:
                popular_destination = pickle.load(f)
            return popular_destination.to_dict(orient='records')
        except FileNotFoundError:
            print("No popular destination found. Please run set_popular_destination() first.")
            return None
    
    def get_similar_places(self):
        try:
            if os.path.exists('similar_places.pkl') and os.path.getsize('similar_places.pkl') > 0:
                with open('similar_places.pkl', 'rb') as f:
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
                       p.rating AS rating, p.num_ratings AS `no of rating`,
                       p.best_month AS `best month to visit`,
                       p.famous_activities AS `famous activities`,
                       p.famous_activities_rating AS `famous activities with rating`,
                       (SELECT i.image_name FROM place_image_map pim
                          JOIN images i ON pim.image_id = i.id
                         WHERE pim.place_id = p.id LIMIT 1) AS image,
                       p.dist_airport AS `distance from airport`,
                       p.dist_bus_stand AS `distance from bus stand`,
                       p.dist_railway AS `distance from railway station`,
                       p.prefer_friends AS `prefer for friends`,
                       p.prefer_couple AS `prefer for couple`,
                       p.prefer_family_children AS `prefer for family with children`,
                       p.prefer_family_no_children AS `prefer for family without children`,
                       p.opening_hours AS opening_hours,
                       p.website_uri AS website_uri,
                       p.phone_number AS phone_number,
                       p.place_types AS place_types,
                       p.lat AS lat, p.lon AS lon
                FROM places p
                LEFT JOIN cities c ON p.city_id = c.id
                LEFT JOIN states st ON c.state_id = st.id
                """
            )
            if rows:
                df = pd.DataFrame(rows)
                df = self._coerce_numeric(df, ['rating', 'no of rating'])
                print(f"  Loaded {len(df)} places from DB.")
                return df
            print("  places table empty — falling back to CSV.")
        except Exception as e:
            print(f"  DB read for places failed ({e}) — falling back to CSV.")
        return pd.read_csv('indian_travel_places.csv')

    def _get_images_for_places(self, names):
        """Return {lowercased place name -> [image_name, ...]} for the given place
        names, pulling ALL images per place from place_image_map. Used to build the
        multi-image galleries in the itinerary response. Returns {} on any DB error."""
        names = [str(n).strip().lower() for n in names if str(n).strip()]
        if not names:
            return {}
        try:
            placeholders = ",".join(["%s"] * len(names))
            rows = fetch_dicts(
                f"""SELECT LOWER(p.name) AS name, i.image_name AS image
                    FROM places p
                    JOIN place_image_map pim ON pim.place_id = p.id
                    JOIN images i ON pim.image_id = i.id
                    WHERE LOWER(p.name) IN ({placeholders})""",
                tuple(names),
            )
        except Exception as e:
            print(f"  _get_images_for_places failed ({e})")
            return {}

        result = {}
        for row in rows:
            result.setdefault(row["name"], [])
            if row["image"] and row["image"] not in result[row["name"]]:
                result[row["name"]].append(row["image"])
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
                    "SELECT image_name FROM images WHERE LOWER(image_name) LIKE %s "
                    "ORDER BY id LIMIT %s",
                    ("%" + escaped + "%", limit),
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
        return pd.read_csv('indian_hotels.csv')

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
        return pd.read_csv('indian_restaurants.csv')

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
                with open('activity_embeddings.pkl', 'rb') as f:
                    self.activity_embeddings = pickle.load(f)
                with open('place_type_embeddings.pkl', 'rb') as f:
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
                with open('activity_embeddings.pkl', 'wb') as f:
                    pickle.dump(self.activity_embeddings, f)
                with open('place_type_embeddings.pkl', 'wb') as f:
                    pickle.dump(self.place_type_embeddings, f)
                print("Generated and saved new embeddings")


        except FileNotFoundError as e:
            raise Exception(f"Required data files not found: {str(e)}")

    def preprocess_places_data(self, df):
        """Preprocess the dataset for better recommendations."""
        # Clean column names
        df = df.rename(columns={'name': 'placename'})
        df = df.drop_duplicates(subset=['placename'], keep='first')

        # Replace '/' with ',' in comma-separated fields
        df['famous activities'] = df['famous activities'].str.replace('/', ',')
        df['best month to visit'] = df['best month to visit'].str.replace('/', ',')

        # Convert string representations of dictionaries to actual dictionaries
        df['famous activities with rating'] = df['famous activities with rating'].apply(
            lambda x: ast.literal_eval(x) if isinstance(x, str) and x.startswith('{') else {}
        )
        return df

    def merge_list(self, activities):
        """Merge activities with commas and 'and' before the last one."""
        if len(activities) == 0:
            return ""  # Handle empty list
        if len(activities) == 1:
            return activities[0]  # Handle single activity
        return ", ".join(activities[:-1]) + " and " + activities[-1]

    def _generate_user_embedding(self, user_preferences):
        """Generate user embedding based on user preferences"""
        query = 'The user prefers trips focused on ' + user_preferences['trip_type'] + '. They are also interested in activities such as ' + self.merge_list(user_preferences['preferred_activities']) + '.'
        user_activity_embedding = self._encode([query])[0]
        # Implement user embedding generation logic here
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

            # Get place recommendations
            places = self._get_place_recommendations(user_preferences)
            places = self._enrich_place_images(places)

            # Get hotel recommendations
            hotels = self._get_hotel_recommendations(user_preferences)

            # Get restaurant recommendations
            restaurants = self._get_restaurant_recommendations(user_preferences)

            # Generate detailed itinerary
            itinerary = self._generate_detailed_itinerary(
                user_preferences,
                places,
                hotels,
                restaurants,
            )

            return self._finalize_itinerary(itinerary, places, start_date=user_preferences.get('start_date'))

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

            places = self._get_place_recommendations(user_preferences)
            places = self._enrich_place_images(places)
            yield {'event': 'progress', 'step': 'places', 'message': 'Found places to visit'}

            hotels = self._get_hotel_recommendations(user_preferences)
            yield {'event': 'progress', 'step': 'hotels', 'message': 'Picked hotels'}

            restaurants = self._get_restaurant_recommendations(user_preferences)
            yield {'event': 'progress', 'step': 'restaurants', 'message': 'Picked restaurants'}

            # Resolve the destination's title and image gallery up front (from
            # the requested place) so they can be shown before the LLM plan is
            # built. The final `complete` event still carries the authoritative
            # values from the generated itinerary.
            destination = str(user_preferences.get('places_of_interest', '')).strip()
            dest_city, dest_state = self._parse_city_state(destination)
            yield {'event': 'progress', 'step': 'info', 'message': 'Preparing your trip details...'}
            dest_description = self._generate_destination_description(destination)
            yield {
                'event': 'info',
                'name': destination,
                'description': dest_description,
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

            result = self._finalize_itinerary(itinerary, places, start_date=user_preferences.get('start_date'))

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

            places = self._get_place_recommendations(user_preferences)
            places = self._enrich_place_images(places)
            hotels = self._get_hotel_recommendations(user_preferences)
            restaurants = self._get_restaurant_recommendations(user_preferences)

            prompt = self.generate_edit_itinerary_prompt(
                user_preferences, places, restaurants, hotels, place_names
            )
            print(f"Edit prompt length: {len(prompt)} chars")

            response = self.client.responses.create(
                model=self.chat_deployment,
                input=[{"role": "user", "content": prompt}],
            )
            response_text = response.output_text
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            itinerary = json.loads(response_text.strip())

            return self._finalize_itinerary(itinerary, places, start_date=user_preferences.get('start_date'))

        except (KeyError, IndexError, TypeError) as e:
            print("Error while processing edit:", e)
            return {
                'status': 'error',
                'message': str(e)
            }

    def _finalize_itinerary(self, itinerary, places, start_date=None):
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
                if(pd.notna(place['image'])):
                    placename = place['placename']
                    image = place['image']
                    place_image_map[placename] = image
            
            placename = itinerary['name']

            print("placenameplacename",placename,place_image_map)
           
            with open('similar_places.pkl', 'rb') as f:
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

            # Attach a single image to every place in each day's places_to_visit.
            # Match the LLM-generated place name (title case) against our image
            # map (DB placenames are lowercase) case-insensitively; fall back to
            # a default image so the field is always populated.
            normalized_image_map = {
                str(name).strip().lower(): img
                for name, img in place_image_map.items()
            }
            itin_city = itinerary.get('city')
            itin_state = itinerary.get('state')
            for day in itinerary.get('itinerary', []):
                for place in day.get('places_to_visit', []):
                    place_name = str(place.get('name', '')).strip()
                    key = place_name.lower()
                    image = normalized_image_map.get(key)
                    if not image or (not isinstance(image, list) and pd.isna(image)):
                        # No mapped image — search the images table by keyword,
                        # most specific first: this place's name, then the
                        # location ("City, State"), then the itinerary city/state.
                        location = place.get('location', '') or ''
                        loc_parts = [p.strip() for p in str(location).split(',') if p.strip()]
                        keywords = [place_name] + loc_parts + [itin_city, itin_state]
                        image = self._search_image_by_keywords(keywords)
                    if not image or (not isinstance(image, list) and pd.isna(image)):
                        image = 'default' + str(random.randint(1, 7)) + '.webp'
                    place['image'] = image

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

            return {
                'status': 'success',
                'data': {
                    'detailed_itinerary': itinerary,
                    'places': places.to_dict(orient='records')
                }
            }

        except (KeyError, IndexError, TypeError) as e:
            print("Error while processing:", e)
            return {
                'status': 'error',
                'message': str(e)
            }

    def _db_lat_long(self, table, name):
        """Look up lat/lon/full_address for a row by name in
        places/hotels/restaurants. Returns a dict only when lat & lon are both
        present, else None."""
        if not name:
            return None
        name_col = "property_name" if table == "hotels" else "name"
        try:
            rows = fetch_dicts(
                f"SELECT lat, lon, full_address FROM {table} "
                f"WHERE LOWER({name_col}) = %s AND lat IS NOT NULL AND lon IS NOT NULL "
                f"LIMIT 1",
                (str(name).strip().lower(),),
            )
        except Exception as e:
            print(f"  _db_lat_long failed for {name!r} in {table} ({e})")
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
                f"UPDATE {table} SET lat = %s, lon = %s, full_address = %s "
                f"WHERE LOWER({name_col}) = %s AND (lat IS NULL OR lon IS NULL)",
                (lat, lon, full_address, str(name).strip().lower()),
            )
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"  _save_lat_long_to_db failed for {name!r} in {table} ({e})")

    def _resolve_lat_long(self, table, name, location_hint=""):
        """Resolve lat/lon/full_address for an entity: prefer the DB value, fall
        back to OpenStreetMap Nominatim. On a successful geocode, persist the
        result back to the DB. Returns a dict {'lat','lon','full_address'} or
        None. Tries multiple query variants to improve Nominatim hit rate."""
        coords = self._db_lat_long(table, name)
        if coords is not None:
            return coords

        # Build a list of query variants from most specific to least specific.
        # Nominatim sometimes fails on very long or unusual names but succeeds
        # with a shorter name + city hint.
        name_str = str(name or "").strip()
        hint = location_hint.strip().strip(",") if location_hint else ""
        queries = []
        if hint:
            queries.append(f"{name_str}, {hint}")   # full name + city/state
        queries.append(name_str)                     # name only as last resort

        result = None
        for q in queries:
            result = self.geocoder.geocode(q)
            if result is not None:
                break

        if result is None:
            return None

        # Persist back to the DB so future itineraries skip the API.
        self._save_lat_long_to_db(
            table, name, result["lat"], result["lon"], result.get("full_address", "")
        )
        return result

    def _attach_db_fields(self, itinerary):
        """Override LLM-generated opening_hours/website_uri/phone_number with
        verified Google data from the DB whenever available. Silently skips
        places not found in the DB lookup."""
        if self.places_df is None or self.places_df.empty:
            return
        db_lookup = {
            str(r.get("name", "")).strip().lower(): r
            for r in self.places_df.to_dict(orient="records")
        }
        for day in itinerary.get("itinerary", []):
            for place in day.get("places_to_visit", []):
                key = str(place.get("name", "")).strip().lower()
                db = db_lookup.get(key)
                if not db:
                    continue
                if db.get("opening_hours"):
                    place["opening_hours"] = db["opening_hours"]
                if db.get("website_uri"):
                    place["website_uri"] = db["website_uri"]
                if db.get("phone_number"):
                    place["phone_number"] = db["phone_number"]

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
            for rest in day.get('restaurants', []):
                apply(rest, 'restaurants', default_hint)
            for hotel in day.get('hotels', []):
                apply(hotel, 'hotels', default_hint)

    def _attach_weather(self, itinerary, start_date_str):
        """Attach per-place weather to every place_to_visit using its lat/lon.
        Also sets a representative day-level weather summary on each day object.
        Requires _attach_lat_long to have already run. Silently skips places
        without coordinates. Uses the 2-hour in-memory cache in weather service."""
        from datetime import datetime, timedelta
        from features.weather.service import get_weather_by_coords

        try:
            trip_start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            return

        for i, day in enumerate(itinerary.get('itinerary', [])):
            day_date = (trip_start + timedelta(days=i)).strftime("%Y-%m-%d")
            day_weather = None

            for place in day.get('places_to_visit', []):
                lat = place.get('lat')
                lon = place.get('lon')
                if lat is None or lon is None:
                    place['weather'] = None
                    continue
                result, err = get_weather_by_coords(float(lat), float(lon), day_date, days=1)
                if result and result.get('weather'):
                    weather_entry = result['weather'][0]
                    place['weather'] = weather_entry
                    if day_weather is None:
                        day_weather = weather_entry
                else:
                    place['weather'] = None

            day['weather'] = day_weather

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

        preferred_location = user_preferences["places_of_interest"].lower()

        # Split the preferred_location on comma and strip spaces
        location_parts = [part.strip() for part in preferred_location.split(",")]

        # Step 1: Try to match on city first
        city_matches = top_places[
            top_places['city'].str.lower().apply(
                lambda city: any(part in str(city).lower() for part in location_parts)
            )
        ]

        if not city_matches.empty:
            # If there are city matches, use them only
            top_places = city_matches
        else:
            # Otherwise fall back to matching by state
            top_places = top_places[
                top_places['state'].str.lower().apply(
                    lambda state: any(part in str(state).lower() for part in location_parts) )
            ]

        # Calculate average rating for all restaurants (or set a constant)
        C = top_places['rating'].mean()

        # Calculate weighted rating score for each place for user preferences
        top_places['rating_score'] = top_places.apply(
            lambda x: self.weighted_place_rating(x, C), axis=1
        )

        # Normalize scores to a range of 0-1
        for column in ['activity_score', 'trip_type_score', 'rating_score']:
            min_val = top_places[column].min()
            max_val = top_places[column].max()
            top_places[column] = (top_places[column] - min_val) / (max_val - min_val)

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

    def _get_hotel_recommendations(self, user_preferences):
        """Get hotel recommendations based on selected places"""
        city = user_preferences["places_of_interest"]

        live_hotels = self.places_client.search_hotels(city)
        if live_hotels:
            return pd.DataFrame(live_hotels).head(100)

        # Fallback to CSV data
        preferred_location = city.lower()
        top_hotels = self.hotels_df[
            self.hotels_df['city'].str.lower().str.contains(preferred_location, na=False) |
            self.hotels_df['state'].str.lower().str.contains(preferred_location, na=False) |
            self.hotels_df['address'].str.lower().str.contains(preferred_location, na=False)
        ].sort_values('site_review_rating', ascending=False)

        return top_hotels.head(100)


    def _get_restaurant_recommendations(self, user_preferences):
        """Get restaurant recommendations based on location and preferences"""
        city = user_preferences["places_of_interest"]
        cuisine = user_preferences["food_preferences"]

        live_restaurants = self.places_client.search_restaurants(city, cuisine)
        if live_restaurants:
            return pd.DataFrame(live_restaurants).head(100)

        # Fallback to CSV data
        preferred_cuisines = [self.normalize(c) for c in cuisine.split(',')]
        preferred_location = city.lower()

        top_restaurants = self.restaurants_df[
            (self.restaurants_df['City'].apply(self.normalize).str.contains(preferred_location, na=False) |
            self.restaurants_df['Locality'].apply(self.normalize).str.contains(preferred_location, na=False)) &
            (self.restaurants_df['Cuisine'].apply(self.normalize).str.contains('|'.join(preferred_cuisines), na=False))
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

    def _generate_detailed_itinerary(self, user_preferences, top_places, top_hotels, top_restaurants):
        prompt = self.generate_travel_itinerary_prompt(user_preferences, top_places, top_restaurants, top_hotels)
        print(f"Prompt length: {len(prompt)} chars")

        response = self.client.responses.create(
            model=self.chat_deployment,
            input=[{"role": "user", "content": prompt}],
        )
        print(f"response:", response)

        response_text = response.output_text
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()

        return json.loads(response_text)

    def generate_travel_itinerary_prompt(self,user_preferences, top_places, top_restaurants, top_hotels):
        prompt = f"""
        🚨 VERY IMPORTANT: You must follow the output format strictly. DO NOT modify, rename, add, or remove any key in the JSON structure below. 
        For example: `approx_cost` ≠ `cost`, `placename` ≠ `name`, `price_range` ≠ `budget`. 
        Use the **exact keys** from the format shown — even a small change will break the output. This is the top priority rule.

        --- 

        You are a smart AI that helps create a personalized multi-day travel itinerary.

        Use the information provided below: user preferences, recommended places, real restaurant and hotel datasets. 
        If no data is available, use your general travel knowledge to add relevant places, hotels, or restaurants — but always follow the format strictly.

        ---

        ### 👤 User Preferences
        - Preferred activities: {', '.join(user_preferences['preferred_activities'])}
        - Places of interest: {user_preferences['places_of_interest']}
        - Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
        - Food preferences: {user_preferences['food_preferences']}
        - Starting location: {user_preferences['user_location']}
        - Travel month: {user_preferences['current_month']}
        - Trip type: {user_preferences['trip_type']}
        - Trip duration: {user_preferences['trip_duration']} days
        - Suggested places: {user_preferences['suggested_places']}
        - Budget: {user_preferences['budget']}

        ---

        ### 📍 Recommended Places (Use only these when available!)
        {top_places}

        ---

        ### 🍽️ Restaurants Dataset (Use only real data when available)
        {top_restaurants}

        ---

        ### 🏨 Hotels Dataset (Use only real data when available)
        {top_hotels}

        ---

        ### 🧠 Rules

        1. The final output **must be 100% valid JSON**. Strictly no broken or partial JSON.
        2. If `suggested_places` is empty or not provided, you **must generate exactly {user_preferences['trip_duration']} full days** in the itinerary — no more, no fewer. This is not negotiable.
        3. You **must include all {user_preferences['suggested_places']} in the itinerary**. If including them increases the number of days, **extend the trip duration accordingly and generate the itinerary for the new total number of days**.
        4. If datasets do not have enough entries to cover all days, you **must use GenAI knowledge** to fill missing places, restaurants, or hotels.
        5. Each day must include:
          - 2–3 geographically close places to visit.
          - 2–3 restaurants (match cuisine and location).
          - 2–3 hotels (low, mid, and high budget options).
        6. For each place, include:
          - `name`, `location`, `reason`, `activities`, `rating`, estimated visit time (e.g., “1.5–2 hours”), and `opening_hours`.
          - `opening_hours`: typical operating hours as a short string (e.g., “6:00 AM – 8:00 PM”, “Open 24 hours”, “Tue–Sun: 9:00 AM – 5:00 PM”). Use your knowledge for well-known places; if genuinely unknown, use “Check locally”.
          - `rating` must be the place’s rating from the Recommended Places dataset when available; otherwise use a realistic rating based on your knowledge (e.g. “4.5”).
        7. For each restaurant, include:
          - `name`, `cuisine`, `approx_cost`, `rating`, `location`, and a short reason related to food preference.
          - If user budget is available, choose restaurants that fall within that budget. If not, choose automatically based on location and meal type.
        8. For each hotel, include:
          - `name`, `type`, `price_range`, `rating`, `location`, `reason`, and a `link` (either from dataset or generated if missing).
          - If user budget is available, ensure the hotel fits within the budget range: low (₹1000–₹3000), mid (₹3000–₹8000), high (₹8000+). If no budget is specified, select a range of hotel types.
        9. On Day 1 and the final day, choose places/hotels closer to the airport or train station.
        10. Avoid repeating places, hotels, or restaurants on different days.
        11. Keep the travel flow linear — do not plan A → B → A routes.
        12. The trip starts on "{user_preferences.get('start_date', '')}". Use this to determine the actual day-of-week for each itinerary day (Day 1 = start_date, Day 2 = start_date + 1 day, etc.).
            - **DO NOT suggest a place on a day it is regularly closed.** For example, if a museum is closed on Tuesdays and Day 2 falls on a Tuesday, swap it with another open place or move it to a different day.
            - If start_date is not provided, skip this constraint.
        13. Order `places_to_visit` within each day by opening time — earliest-closing places first:
            - Places that close before 2:00 PM (e.g., morning markets, some temples) **must appear first**.
            - Places open until evening or 24 hours can go later in the day.
            - Religious sites with morning and evening darshan windows — prefer the morning slot.
            - Sunset viewpoints, night markets, and forts with light-and-sound shows — slot them last.
        14. Do NOT include comments, markdown, or any explanation in the response — only JSON output.
        15. Do NOT add trailing commas — not after the last item in an array or the last key in an object.
        16. If required fields are missing in the datasets, **generate realistic replacements using GenAI knowledge**, ensuring the format and tone match the examples.
        17. Always generate a full response — no placeholder text like “TBD” or “N/A”.
        18. If budget is provided in the payload, ensure hotels and restaurants fall within it. Otherwise, choose budget automatically based on destination and travel group type.
        19. At the end of the JSON, include a `similar_places` list — destinations similar to the main place, based on:
          - places should be from indian_travel_places dataset
          - user’s `places_of_interest`
          - preferred activities
          - travel group type
          - food preferences
          - user location (for budget-friendly or closer alternatives)
          - trip type (Beach, mountain, hill station, Religious site, Nature etc.)
        20. Don’t add NaN if any image or name is not available. Just leave it blank.

        ---

        ### 📦 Output Format (JSON)

        {{
          "itinerary": [
            {{
              "day": 1,
              "places_to_visit": [
                {{
                  "name": "Place Name",
                  "location": "City, State",
                  "reason": "Matches your interest in [e.g. temples, nature]",
                  "activities": ["Activity 1", "Activity 2"],
                  "rating": "X.X",
                  "opening_hours": "9:00 AM – 6:00 PM",
                  "duration": "1.5–2 hours",
                }}
              ],
              "restaurants": [
                {{
                  "name": "Restaurant Name",
                  "cuisine": "Cuisine Type",
                  "approx_cost": "₹XXX", // if budget is given, match cost range accordingly; else choose normally
                  "rating": "X.X",
                  "location": "Area Name",
                  "reason": "Matches your food preference: {user_preferences['food_preferences']}"
                }}
              ],
              "hotels": [
                {{
                  "name": "Hotel Name",
                  "type": "Hotel Type",
                  "price_range": "₹XXX", // match this to budget: low (1000–3000) | mid (3000–8000) | high (8000+) — auto select if no budget
                  "rating": "X.X",
                  "location": "City",
                  "reason": "Near visited places or transport hub. Good for {user_preferences['travel_group_type']}",
                  "link": "Add valid Page URL from hotel dataset or generated link"
                }}
              ]
            }}
            // Repeat same structure for next days — NO comma after the last day.
          ],
          "name": "Place Name",
          "description": "Short description of the place",
          "price_estimated_range": "give the total price range estimated per head. It should be in the range of ${user_preferences['budget']} if the price range actually comes in the user's budget, otherwise show the actual price range.",
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

        return textwrap.dedent(prompt)

    def generate_edit_itinerary_prompt(self, user_preferences, top_places, top_restaurants, top_hotels, must_include_places):
        must_include_block = "\n".join(f"          - {name}" for name in must_include_places) or "          (none specified)"
        prompt = f"""
        🚨 VERY IMPORTANT: You must follow the output format strictly. DO NOT modify, rename, add, or remove any key in the JSON structure below.
        For example: `approx_cost` ≠ `cost`, `placename` ≠ `name`, `price_range` ≠ `budget`.
        Use the **exact keys** from the format shown — even a small change will break the output. This is the top priority rule.

        ---

        You are a smart AI that EDITS / REGENERATES a personalized multi-day travel itinerary.

        This is an EDIT request. The user already has an itinerary and now wants it rebuilt so that a
        specific set of places they picked are **guaranteed to be part of the plan**. Rebuild the full
        itinerary from the user preferences below, but treat the "Places that MUST be included" list as
        a hard requirement.

        Use the information provided below: user preferences, recommended places, real restaurant and hotel datasets.
        If no data is available, use your general travel knowledge to add relevant places, hotels, or restaurants — but always follow the format strictly.

        ---

        ### 👤 User Preferences
        - Preferred activities: {', '.join(user_preferences['preferred_activities'])}
        - Places of interest: {user_preferences['places_of_interest']}
        - Travel group: {user_preferences['travel_group_type']} ({user_preferences['number_of_people']} people)
        - Food preferences: {user_preferences['food_preferences']}
        - Starting location: {user_preferences['user_location']}
        - Travel month: {user_preferences['current_month']}
        - Trip type: {user_preferences['trip_type']}
        - Trip duration: {user_preferences['trip_duration']} days
        - Suggested places: {user_preferences['suggested_places']}
        - Budget: {user_preferences['budget']}

        ---

        ### ✅ Places that MUST be included (hard requirement for this edit)
{must_include_block}

        ---

        ### 📍 Recommended Places (Use only these when available!)
        {top_places}

        ---

        ### 🍽️ Restaurants Dataset (Use only real data when available)
        {top_restaurants}

        ---

        ### 🏨 Hotels Dataset (Use only real data when available)
        {top_hotels}

        ---

        ### 🧠 Rules

        1. The final output **must be 100% valid JSON**. Strictly no broken or partial JSON.
        2. **Every place in the "Places that MUST be included" list MUST appear** in the itinerary's `places_to_visit` across the days. This is non-negotiable — do not drop, rename, or merge any of them.
        3. Distribute the must-include places sensibly across days, grouping geographically close places together and keeping a linear travel flow.
        4. If the must-include places do not all fit within {user_preferences['trip_duration']} days, **extend the trip duration** and generate the itinerary for the new total number of days so that all of them are included. In that case you MUST also:
          - Set the top-level `total_days` to the new (increased) number of days.
          - Add a clear, friendly `notes` message explaining that the trip was extended from {user_preferences['trip_duration']} days to the new number of days to fit all the selected places.
          If everything fits within {user_preferences['trip_duration']} days, set `total_days` to {user_preferences['trip_duration']} and set `notes` to an empty string "".
        5. After placing all must-include places, you may add other relevant places (from the dataset or your knowledge) so each day has 2–3 geographically close places to visit.
        6. If `suggested_places` are provided, include them as well.
        7. If datasets do not have enough entries, **use GenAI knowledge** to fill missing places, restaurants, or hotels.
        8. Each day must include:
          - 2–3 geographically close places to visit (including any must-include places assigned to that day).
          - 2–3 restaurants (match cuisine and location).
          - 2–3 hotels (low, mid, and high budget options).
        9. For each place, include:
          - `name`, `location`, `reason`, `activities`, `rating`, estimated visit time (e.g., “1.5–2 hours”), and `opening_hours`.
          - `opening_hours`: typical operating hours as a short string (e.g., “6:00 AM – 8:00 PM”, “Open 24 hours”, “Tue–Sun: 9:00 AM – 5:00 PM”). Use your knowledge for well-known places; if genuinely unknown, use “Check locally”.
          - `rating` must be the place's rating from the Recommended Places dataset when available; otherwise use a realistic rating based on your knowledge (e.g. “4.5”).
        10. For each restaurant, include:
          - `name`, `cuisine`, `approx_cost`, `rating`, `location`, and a short reason related to food preference.
          - If user budget is available, choose restaurants that fall within that budget. If not, choose automatically based on location and meal type.
        11. For each hotel, include:
          - `name`, `type`, `price_range`, `rating`, `location`, `reason`, and a `link` (either from dataset or generated if missing).
          - If user budget is available, ensure the hotel fits within the budget range: low (₹1000–₹3000), mid (₹3000–₹8000), high (₹8000+). If no budget is specified, select a range of hotel types.
        12. On Day 1 and the final day, choose places/hotels closer to the airport or train station.
        13. Avoid repeating places, hotels, or restaurants on different days.
        14. Keep the travel flow linear — do not plan A → B → A routes.
        15. The trip starts on "{user_preferences.get('start_date', '')}". Use this to determine the actual day-of-week for each itinerary day (Day 1 = start_date, Day 2 = start_date + 1 day, etc.).
            - **DO NOT suggest a place on a day it is regularly closed.** For example, if a museum is closed on Tuesdays and Day 2 falls on a Tuesday, swap it with another open place or move it to a different day.
            - If start_date is not provided, skip this constraint.
        16. Order `places_to_visit` within each day by opening time — earliest-closing places first:
            - Places that close before 2:00 PM (e.g., morning markets, some temples) **must appear first**.
            - Places open until evening or 24 hours can go later in the day.
            - Religious sites with morning and evening darshan windows — prefer the morning slot.
            - Sunset viewpoints, night markets, and forts with light-and-sound shows — slot them last.
        17. Do NOT include comments, markdown, or any explanation in the response — only JSON output.
        18. Do NOT add trailing commas — not after the last item in an array or the last key in an object.
        19. If required fields are missing in the datasets, **generate realistic replacements using GenAI knowledge**, ensuring the format and tone match the examples.
        20. Always generate a full response — no placeholder text like “TBD” or “N/A”.
        21. At the end of the JSON, include a `similar_places` list — destinations similar to the main place, based on the user's `places_of_interest`, preferred activities, travel group type, food preferences, user location, and trip type. Places should be from the indian_travel_places dataset.
        22. Don't add NaN if any image or name is not available. Just leave it blank.

        ---

        ### 📦 Output Format (JSON)

        {{
          "itinerary": [
            {{
              "day": 1,
              "places_to_visit": [
                {{
                  "name": "Place Name",
                  "location": "City, State",
                  "reason": "Matches your interest in [e.g. temples, nature]",
                  "activities": ["Activity 1", "Activity 2"],
                  "rating": "X.X",
                  "opening_hours": "9:00 AM – 6:00 PM",
                  "duration": "1.5–2 hours",
                }}
              ],
              "restaurants": [
                {{
                  "name": "Restaurant Name",
                  "cuisine": "Cuisine Type",
                  "approx_cost": "₹XXX", // if budget is given, match cost range accordingly; else choose normally
                  "rating": "X.X",
                  "location": "Area Name",
                  "reason": "Matches your food preference: {user_preferences['food_preferences']}"
                }}
              ],
              "hotels": [
                {{
                  "name": "Hotel Name",
                  "type": "Hotel Type",
                  "price_range": "₹XXX", // match this to budget: low (1000–3000) | mid (3000–8000) | high (8000+) — auto select if no budget
                  "rating": "X.X",
                  "location": "City",
                  "reason": "Near visited places or transport hub. Good for {user_preferences['travel_group_type']}",
                  "link": "Add valid Page URL from hotel dataset or generated link"
                }}
              ]
            }}
            // Repeat same structure for next days — NO comma after the last day.
          ],
          "name": "Place Name",
          "description": "Short description of the place",
          "total_days": {user_preferences['trip_duration']}, // the actual number of days in this itinerary (increase if you extended the trip to fit all must-include places)
          "notes": "", // empty string if no change; otherwise explain that the trip was extended to fit all selected places
          "price_estimated_range": "give the total price range estimated per head. It should be in the range of ${user_preferences['budget']} if the price range actually comes in the user's budget, otherwise show the actual price range.",
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

        return textwrap.dedent(prompt)


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
        except Exception as e:
            print(f"[save_new_places] error: {e}")
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
                    "INSERT INTO places (name, city_id, famous_activities, lat, lon, full_address) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (key, city_id, famous, lat, lon, full_address or None),
                )
                inserted += 1
            except Exception as e:
                print(f"[save_new_places] failed to insert place '{name}': {e}")
        print(f"[save_new_places] inserted {inserted} new place(s).")

    def _save_new_hotels_to_db(self, cursor, itinerary):
        """hotels -> hotels table (matched on property_name)."""
        candidates = []
        for day in itinerary.get('itinerary', []):
            for hotel in day.get('hotels', []):
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
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
            for r in day.get('restaurants', []):
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
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
        cursor.execute("SELECT id FROM cities WHERE name = %s", (city,))
        row = cursor.fetchone()
        if row:
            return row[0]

        # Create the city; link a state if we can match one.
        state_id = None
        if state:
            cursor.execute(
                "SELECT id FROM states WHERE LOWER(name) = %s", (state.lower(),)
            )
            srow = cursor.fetchone()
            if srow:
                state_id = srow[0]
        cursor.execute(
            "INSERT INTO cities (name, state_id) VALUES (%s, %s)", (city, state_id)
        )
        return cursor.lastrowid


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