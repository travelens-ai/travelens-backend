import pandas as pd
import numpy as np
from sentence_transformers import SentenceTransformer
import google.generativeai as genai
from sklearn.metrics.pairwise import cosine_similarity
import ast
import re
import pickle
import textwrap

class ItenaryRecommendationSystem:
    def __init__(self, api_key):
        """
        Initialize the Itinerary Recommendation System

        Args:
            api_key (str): Google Generative AI API key
        """
        self.api_key = api_key
        self.genai_model = None
        self.bert_model = None
        self.places_df = None
        self.hotels_df = None
        self.restaurants_df = None

    def initialize(self):
        """Initialize models and load data"""
        try:
            print("Initializing models and loading data...")
            self._setup_models()
            print("Models initialized successfully.")
            self._load_data()
            return True
        except Exception as e:
            print(f"Initialization failed: {str(e)}")
            return False

    def _setup_models(self):
        """Setup BERT and Gemini models"""
        # Setup BERT
        try:
            # Setup BERT
            print("Initializing SentenceTransformer...")
            self.bert_model = SentenceTransformer('all-MiniLM-L6-v2')
            print("SentenceTransformer initialized successfully.")
        except Exception as e:
            print(f"Error initializing SentenceTransformer: {str(e)}")
            raise


        # Setup Gemini
        try:
            print("Configuring Google Generative AI...")
            genai.configure(api_key=self.api_key)
            generation_config = {
                "temperature": 0.7,
                "top_p": 0.95,
                "top_k": 64,
                "max_output_tokens": 2048,
            }
            self.genai_model = genai.GenerativeModel(
                model_name="gemini-1.5-pro",
                generation_config=generation_config
            )
            print("Google Generative AI configured successfully.")
        except Exception as e:
            print(f"Error configuring Google Generative AI: {str(e)}")
            raise

    def _load_data(self):
        """Load required datasets"""
        try:
            print("Loading data...")
            self.places_df = pd.read_csv('indian_travel_places.csv')
            self.hotels_df = pd.read_csv('indian_hotels.csv')
            self.restaurants_df = pd.read_csv('indian_restaurants.csv')

            print("Data loaded successfully.")
            print("places Data Columns", self.places_df.columns)
            print("hotels Data Columns", self.hotels_df.columns)
            print("restaurants Data Columns", self.restaurants_df.columns)

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
                # Generate embeddings for activities
                for _, row in self.places_df.iterrows():
                    activities = row['famous activities with rating'].keys()
                    for activity in activities:
                        if activity not in self.activity_embeddings:
                            self.activity_embeddings[activity] = self.bert_model.encode([activity])[0]
                    
                    place_type = row['type']
                    if place_type not in self.place_type_embeddings:
                        self.place_type_embeddings[place_type] = self.bert_model.encode([place_type])[0]
                
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

    def merge_activities(self, activities):
        """Merge activities with commas and 'and' before the last one."""
        if len(activities) == 0:
            return ""  # Handle empty list
        if len(activities) == 1:
            return activities[0]  # Handle single activity
        return ", ".join(activities[:-1]) + " and " + activities[-1]

    def _generate_user_embedding(self, user_preferences):
        """Generate user embedding based on user preferences"""
        query = 'The user is interested in ' + self.merge_activities(user_preferences['preferred_activities'])
        user_activity_embedding = self.bert_model.encode([query])[0]
        # Implement user embedding generation logic here
        return user_activity_embedding

    def compute_activity_score(self, activity_dict, user_activity_embedding):
        """Compute activity score based on user activity embedding"""
        score = 0

           # Check if activity_dict is None or empty
        if not activity_dict:
            return 0

        for activity, rating in activity_dict.items():
            

            if not activity:
                continue
            # Use cached embedding if available
            if activity in self.activity_embeddings:
                activity_embedding = self.activity_embeddings[activity]
            else:
                activity_embedding = self.bert_model.encode([activity])[0]
                self.activity_embeddings[activity] = activity_embedding
        
            activity_embedding = self.bert_model.encode([activity])[0]
            similarity = cosine_similarity([activity_embedding], [user_activity_embedding])[0][0]

            score += rating * similarity

        return score

    def compute_trip_type_score(self, place_type, user_trip_type_embedding):
        """Compute trip type score using BERT embeddings"""
            # Use cached embedding if available
        if place_type in self.place_type_embeddings:
            place_type_embedding = self.place_type_embeddings[place_type]
        else:
            place_type_embedding = self.bert_model.encode([place_type])[0]
            self.place_type_embeddings[place_type] = place_type_embedding
            
        similarity = cosine_similarity([place_type_embedding], [user_trip_type_embedding])[0][0]
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
                    'trip_duration': str
                }

        Returns:
            dict: Recommended itinerary with places, hotels, and restaurants
        """
        try:

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

            return {
                'status': 'success',
                'data': {
                    # 'places': places,
                    # 'hotels': hotels,
                    # 'restaurants': restaurants,
                    'detailed_itinerary': itinerary
                }
            }

        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }

    def _get_place_recommendations(self, user_preferences):
        """Get recommended places based on user preferences"""
        # generate user embedding
        user_embedding = self._generate_user_embedding(user_preferences)

        top_places = self.places_df

        # Calculate activity score for each place for user preferences
        top_places['activity_score'] = top_places['famous activities with rating'].apply(
            lambda x: self.compute_activity_score(x, user_embedding)
        )

        # Calculate trip type score for each place for user preferences
        top_places['trip_type_score'] = top_places['type'].apply(
            lambda x: self.compute_trip_type_score(x, user_embedding)
        )

        preferred_location = user_preferences["places_of_interest"].lower()

        # Primary Recommendations on State and City (Exact Match)
        top_places = top_places[
            top_places['city'].str.lower().str.contains(preferred_location.lower(), na=False) |
            top_places['state'].str.lower().str.contains(preferred_location.lower(), na=False)
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
        top_places = primary_place_recommendation.head(50)

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


        # Implementation for restaurant recommendations
        pass

    def _generate_detailed_itinerary(self, user_preferences, top_places, top_hotels, top_restaurants):
        prompt = self.generate_travel_itinerary_prompt(user_preferences, top_places, top_restaurants, top_hotels)
        response = self.genai_model.generate_content(prompt)
        response_text = response.text
        response_text = response_text[7:]
        response_text = response_text[:-3]
        # print(response_text)

        return response_text

    def generate_travel_itinerary_prompt(self,user_preferences, top_places, top_restaurants, top_hotels):
        prompt = f"""
        You are a smart AI that helps create a personalized multi-day travel itinerary.

        Use only the information provided below: user preferences, recommended places, real restaurant and hotel datasets.

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

        ---

        ### 📍 Recommended Places (Use only these!)
        {top_places}

        ---

        ### 🍽️ Restaurants Dataset (Real data only)
        {top_restaurants}

        ---

        ### 🏨 Hotels Dataset (Real data only)
        {top_hotels}

        ---

        ### 🧠 Rules

        1. Use ONLY the recommended places. No made-up locations.
        2. Plan for up to {user_preferences['trip_duration']} days.
        3. If data is not available for all days, generate for fewer days (do not force full trip duration).
        4. Each day: include 2–3 geographically close places.
        5. For each place: give name, location, reason (based on user interest), activities, and estimated visit time (e.g. "1.5–2 hours").
        6. Suggest 2–3 restaurants per day (match cuisine & location). Use only from dataset.
        7. Suggest 2–3 hotels per day (low, mid, high range). Use real data.
        8. For each restaurant/hotel: include name, type, cost, rating, location, reason for recommendation, and a link or image if available.
        9. On Day 1 and final day, choose places/hotels closer to airport or station.
        10. Don’t repeat places on different days.
        11. Keep travel path linear (avoid A → B → A style).
        12. If no data is available, suggest real known options using general knowledge (never invent fake names).
        13. Always return response in correct JSON format only (even if data is incomplete or days are fewer).
        14. ⚠️ Do not add trailing commas or comments inside the JSON output.

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
                "duration": "1.5–2 hours"
                }}
            ],
            "restaurants": [
                {{
                "name": "Restaurant Name",
                "cuisine": "Cuisine Type",
                "approx_cost": "₹XXX",
                "rating": "X.X",
                "location": "Area Name",
                "reason": "Matches your food preference: {user_preferences['food_preferences']}"
                }}
            ],
            "hotels": [
                {{
                "name": "Hotel Name",
                "type": "Hotel Type",
                "price_range": "Give price range as low, mid, high. Use your knowledge",
                "rating": "X.X",
                "location": "City",
                "reason": "Near visited places or transport hub. Good for {user_preferences['travel_group_type']}",
                "link": "Add Page URL(pageurl) from hotel dataset. "
                }}
            ]
            }}
            // Add more days only if data is available (no comma after last item)
        ],
        "name": "Place Name",
        "description": "Add short description about the place",
        "image": "Add real image URL that opens directly (no placeholders or fake links)"
        }}
        """
        return textwrap.dedent(prompt)



    def _calculate_similarity_scores(self, text1, text2):
        """Calculate similarity between two texts using BERT embeddings"""
        embedding1 = self.bert_model.encode([text1])[0]
        embedding2 = self.bert_model.encode([text2])[0]
        return cosine_similarity([embedding1], [embedding2])[0][0]

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
