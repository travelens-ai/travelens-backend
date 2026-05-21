from flask import json
import pandas as pd
import requests
import numpy as np
import ast
import re
import pickle
import textwrap
from google import genai
from google.genai import types
from numpy import dot
from numpy.linalg import norm
import schedule
import time
import threading
from generate_images import ImageGenerator
import os
import random


class ItenaryRecommendationSystem:
    def __init__(self, api_key):
        """
        Initialize the Itinerary Recommendation System

        Args:
            api_key (str): Google Generative AI API key
        """
        self.api_key = api_key
        self.genai_client = genai.Client(api_key=api_key)
        self.genai_model = None
        self.embedding_model = None
        self.places_df = None
        self.hotels_df = None
        self.restaurants_df = None

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
        result = self.genai_client.models.embed_content(
            model='text-embedding-005',
            contents=texts,
        )
        return [np.array(e.values) for e in result.embeddings]

    def _setup_models(self):
        """Setup Google AI embedding and Gemini models"""
        try:
            print("Configuring Google AI client...")
            self.gemini_model_name = "gemini-2.0-flash-001"
            self.generation_config = types.GenerateContentConfig(
                max_output_tokens=8192,
                temperature=0.5,
                top_p=0.5,
            )
            print("Google AI client configured successfully.")
        except Exception as e:
            print(f"Error configuring Google AI: {str(e)}")
            raise

    def _load_data(self):
        """Load required datasets"""
        try:
            print("Loading data...")
            self.places_df = pd.read_csv('indian_travel_places.csv')
            self.hotels_df = pd.read_csv('indian_hotels.csv')
            self.restaurants_df = pd.read_csv('indian_restaurants.csv')

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
                for i in range(0, len(activity_list), 200):
                    batch = activity_list[i:i+200]
                    embeddings = self._encode(batch)
                    for text, emb in zip(batch, embeddings):
                        self.activity_embeddings[text] = emb / norm(emb)
                    print(f"  Encoded activities {i+1}-{min(i+200, len(activity_list))} of {len(activity_list)}")

                # Batch encode place types
                place_type_list = list(all_place_types)
                for i in range(0, len(place_type_list), 200):
                    batch = place_type_list[i:i+200]
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

            # Check if the placename exists in the mp dictionary
            if placename in place_image_map and pd.notna(place_image_map[placename]):
                itinerary['image'] = place_image_map[placename]

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

            # Convert NaN values to empty strings using deep search
            itinerary = json.loads(json.dumps(itinerary, default=lambda x: '' if pd.isna(x) else x))
            places = places.fillna('')

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
                    lambda state: any(part in state.lower() for part in location_parts) )
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


    def _get_hotel_recommendations(self, user_preferences):
        """Get hotel recommendations based on selected places"""
        preferred_location = user_preferences["places_of_interest"].lower()

        #Get Hotel Recommendations
        top_hotels = self.hotels_df[
            self.hotels_df['city'].str.lower().str.contains(preferred_location, na=False) |
            self.hotels_df['state'].str.lower().str.contains(preferred_location, na=False)|
            self.hotels_df['address'].str.lower().str.contains(preferred_location, na=False)
        ].sort_values('site_review_rating', ascending=False)

        return top_hotels.head(100)


    def _get_restaurant_recommendations(self, user_preferences):
        """Get restaurant recommendations based on location and preferences"""
        preferred_cuisines = [self.normalize(cuisine) for cuisine in user_preferences["food_preferences"].split(',')]   # Normalize cuisines
        preferred_location = user_preferences["places_of_interest"].lower()


        # Get top 100 restaurants
        top_restaurants = self.restaurants_df[
            (self.restaurants_df['City'].apply(self.normalize).str.contains(preferred_location, na=False) |
            self.restaurants_df['Locality'].apply(self.normalize).str.contains(preferred_location, na=False)) &
            (self.restaurants_df['Cuisine'].apply(self.normalize).str.contains('|'.join(preferred_cuisines), na=False))  # Cuisine filter
        ].sort_values('Rating', ascending=False)
        top_restaurants = top_restaurants.drop_duplicates(subset=['Name'], keep='first')

        C = top_restaurants['Rating'].mean()
        # Calculate weighted rating score for each place for user preferences
        top_restaurants['rating_score'] = top_restaurants.apply(
            lambda x: self.weighted_restaurants_rating(x, C), axis=1
        )

        top_restaurants = top_restaurants.sort_values('rating_score', ascending=False)

        return top_restaurants.head(100)



    def _generate_detailed_itinerary(self, user_preferences, top_places, top_hotels, top_restaurants):
        prompt = self.generate_travel_itinerary_prompt(user_preferences, top_places, top_restaurants, top_hotels)
        
                # Prompt the model
        response = self.genai_client.models.generate_content(
            model=self.gemini_model_name,
            contents=prompt,
            config=self.generation_config,
        )
        
        if response.candidates:
            content = response.candidates[0].content
            if content.parts:
                text = content.parts[0].text
            else:
                print("⚠️ No text parts in content.")
        else:
            print("⚠️ No candidates returned.")

        
        # response = self.genai_model.generate_content(prompt)
        response_text = text
        response_text = response_text[7:]
        response_text = response_text[:-3]



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
          - `name`, `location`, `reason`, `activities`, and estimated visit time (e.g., "1.5–2 hours").
        7. For each restaurant, include:
          - `name`, `cuisine`, `approx_cost`, `rating`, `location`, and a short reason related to food preference.
          - If user budget is available, choose restaurants that fall within that budget. If not, choose automatically based on location and meal type.
        8. For each hotel, include:
          - `name`, `type`, `price_range`, `rating`, `location`, `reason`, and a `link` (either from dataset or generated if missing).
          - If user budget is available, ensure the hotel fits within the budget range: low (₹1000–₹3000), mid (₹3000–₹8000), high (₹8000+). If no budget is specified, select a range of hotel types.
        9. On Day 1 and the final day, choose places/hotels closer to the airport or train station.
        10. Avoid repeating places, hotels, or restaurants on different days.
        11. Keep the travel flow linear — do not plan A → B → A routes.
        12. Do NOT include comments, markdown, or any explanation in the response — only JSON output.
        13. Do NOT add trailing commas — not after the last item in an array or the last key in an object.
        14. If required fields are missing in the datasets, **generate realistic replacements using GenAI knowledge**, ensuring the format and tone match the examples.
        15. Always generate a full response — no placeholder text like “TBD” or “N/A”.
        16. If budget is provided in the payload, ensure hotels and restaurants fall within it. Otherwise, choose budget automatically based on destination and travel group type.
        17. At the end of the JSON, include a `similar_places` list — destinations similar to the main place, based on:
          - places should be from indian_travel_places dataset
          - user’s `places_of_interest`
          - preferred activities
          - travel group type
          - food preferences
          - user location (for budget-friendly or closer alternatives)
          - trip type (Beach, mountain, hill station, Religious site, Nature etc.)
        18. Don't add NaN if any image or name is not available. Just leave it blank.

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
                    image_path = imageGenerator.generate_and_save_image(placename)
                    
                    # Upload image
                    with open(image_path, 'rb') as image_file:
                        response = requests.post(
                            "https://www.ultrazynix.com/travelens/upload.php",
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