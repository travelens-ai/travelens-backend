import numpy as np
import threading
from concurrent.futures import ThreadPoolExecutor

from integrations.api_integrations import GooglePlacesClient, ImageSearchClient, NominatimClient

from prompts.constants import (
    BUDGET_TIER_MAP, HOTEL_TIER_STARS, MEAL_COST_CAPS,
    PLACE_COLS_PROMPT, HOTEL_COLS_PROMPT, REST_COLS_PROMPT,
)

from models.recommendation import data_loading as _dl
from models.recommendation import image_helpers as _img
from models.recommendation import scoring as _sc
from models.recommendation import popular_places as _pop
from models.recommendation import recommendations as _rec
from models.recommendation import finalization as _fin
from models.recommendation import llm_calls as _llm
from models.recommendation import db_persistence as _dbp

try:
    from langfuse import observe as _lf_observe, get_client as _lf_get_client, propagate_attributes as _lf_propagate
    _LF_AVAILABLE = True
except ImportError:
    _LF_AVAILABLE = False
    def _lf_observe(*a, **kw):
        if a and callable(a[0]):
            return a[0]
        return lambda fn: fn
    def _lf_get_client():
        return None
    class _NoopPropagateAttrs:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    def _lf_propagate(**kw):
        return _NoopPropagateAttrs()


class ItenaryRecommendationSystem:
    # Re-bind constants so self._X still resolves (existing callers use self._X).
    _BUDGET_TIER_MAP   = BUDGET_TIER_MAP
    _HOTEL_TIER_STARS  = HOTEL_TIER_STARS
    _MEAL_COST_CAPS    = MEAL_COST_CAPS
    _PLACE_COLS_PROMPT = PLACE_COLS_PROMPT
    _HOTEL_COLS_PROMPT = HOTEL_COLS_PROMPT
    _REST_COLS_PROMPT  = REST_COLS_PROMPT

    def __init__(self, client, chat_deployment, embedding_deployment):
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

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def initialize(self):
        _dl.setup_models(self)
        print("Models initialized successfully.")
        _dl.load_data(self)
        self.schedule_popular_destination()
        self.schedule_similar_places()
        return True

    def _setup_models(self):
        _dl.setup_models(self)

    def _load_data(self):
        _dl.load_data(self)

    def _load_places_df(self):
        return _dl.load_places_df(self)

    def _load_hotels_df(self):
        return _dl.load_hotels_df(self)

    def _load_restaurants_df(self):
        return _dl.load_restaurants_df(self)

    def preprocess_places_data(self, df):
        return _dl.preprocess_places_data(self, df)

    @staticmethod
    def _coerce_numeric(df, columns):
        return _dl.coerce_numeric(df, columns)

    # ------------------------------------------------------------------
    # Encoding
    # ------------------------------------------------------------------

    def _encode(self, texts):
        result = self.client.embeddings.create(
            model=self.embedding_deployment,
            input=texts,
        )
        return [np.array(e.embedding) for e in result.data]

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    def merge_list(self, activities):
        return _sc.merge_list(self, activities)

    def normalize(self, text):
        return _sc.normalize(self, text)

    def _generate_user_embedding(self, user_preferences):
        return _sc.generate_user_embedding(self, user_preferences)

    def compute_activity_score(self, activity_dict, user_activity_embedding):
        return _sc.compute_activity_score(self, activity_dict, user_activity_embedding)

    def compute_trip_type_score(self, place_type, user_trip_type_embedding):
        return _sc.compute_trip_type_score(self, place_type, user_trip_type_embedding)

    def weighted_place_rating(self, row, C):
        return _sc.weighted_place_rating(self, row, C)

    def weighted_restaurants_rating(self, row, C):
        return _sc.weighted_restaurants_rating(self, row, C)

    def _calculate_similarity_scores(self, text1, text2):
        return _sc.calculate_similarity_scores(self, text1, text2)

    # ------------------------------------------------------------------
    # Image helpers
    # ------------------------------------------------------------------

    def _get_images_for_places(self, names):
        return _img.get_images_for_places(self, names)

    def _search_images_by_keywords(self, keywords, limit=5):
        return _img.search_images_by_keywords(self, keywords, limit=limit)

    def _search_image_by_keywords(self, keywords):
        return _img.search_image_by_keywords(self, keywords)

    # ------------------------------------------------------------------
    # Popular places
    # ------------------------------------------------------------------

    def schedule_similar_places(self):
        _pop.schedule_similar_places(self)

    def update_similar_places(self):
        _pop.update_similar_places(self)

    def schedule_popular_destination(self):
        _pop.schedule_popular_destination(self)

    def set_popular_destination(self):
        _pop.set_popular_destination(self)

    def get_popular_destination(self):
        return _pop.get_popular_destination(self)

    def get_similar_places(self):
        return _pop.get_similar_places(self)

    def save_similar_places(self, similar_places):
        _pop.save_similar_places(self, similar_places)

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    def _get_place_recommendations(self, user_preferences):
        return _rec.get_place_recommendations(self, user_preferences)

    def _enrich_place_images(self, places_df):
        return _rec.enrich_place_images(self, places_df)

    def _get_hotel_recommendations(self, user_preferences):
        return _rec.get_hotel_recommendations(self, user_preferences)

    def _get_restaurant_recommendations(self, user_preferences):
        return _rec.get_restaurant_recommendations(self, user_preferences)

    def getEditPlaces(self, city, state):
        return _rec.get_edit_places(self, city, state)

    @staticmethod
    def _validate_user_preferences(preferences):
        _rec.validate_user_preferences(preferences)

    def _trim_for_prompt(self, df, cols, n):
        return _rec.trim_for_prompt(self, df, cols, n)

    def _get_available_places(self, itinerary, user_preferences, count, scored_df=None):
        return _fin.get_available_places(self, itinerary, user_preferences, count, scored_df=scored_df)

    # ------------------------------------------------------------------
    # Finalization helpers
    # ------------------------------------------------------------------

    def _finalize_trip_level(self, itinerary, places):
        return _fin.finalize_trip_level(self, itinerary, places)

    def _finalize_days(self, itinerary, days, places, start_date=None, start_day_index=0):
        return _fin.finalize_days(self, itinerary, days, places, start_date=start_date, start_day_index=start_day_index)

    def _finalize_itinerary(self, itinerary, places, start_date=None, user_preferences=None):
        return _fin.finalize_itinerary(self, itinerary, places, start_date=start_date, user_preferences=user_preferences)

    def _attach_lat_long(self, itinerary):
        _fin.attach_lat_long(self, itinerary)

    def _attach_db_fields(self, itinerary):
        _fin.attach_db_fields(self, itinerary)

    def _compute_travel_times(self, itinerary):
        _fin.compute_travel_times(self, itinerary)

    def _attach_weather(self, itinerary, start_date_str):
        _fin.attach_weather(self, itinerary, start_date_str)

    def _db_lat_long(self, table, name):
        return _fin.db_lat_long(self, table, name)

    def _db_lat_long_batch(self, table, names):
        return _fin.db_lat_long_batch(self, table, names)

    def _save_lat_long_to_db(self, table, name, lat, lon, full_address):
        _fin.save_lat_long_to_db(self, table, name, lat, lon, full_address)

    def _resolve_lat_long(self, table, name, location_hint=""):
        return _fin.resolve_lat_long(self, table, name, location_hint=location_hint)

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    def save_new_places(self, itinerary):
        _dbp.save_new_places(self, itinerary)

    @staticmethod
    def _parse_city_state(location):
        return _dbp._parse_city_state(location)

    @staticmethod
    def _to_decimal(value):
        return _dbp._to_decimal(value)

    def _save_new_places_to_db(self, cursor, itinerary):
        _dbp._save_new_places_to_db(self, cursor, itinerary)

    def _save_new_hotels_to_db(self, cursor, itinerary):
        _dbp._save_new_hotels_to_db(self, cursor, itinerary)

    def _save_new_restaurants_to_db(self, cursor, itinerary):
        _dbp._save_new_restaurants_to_db(self, cursor, itinerary)

    def _resolve_city_id(self, cursor, city, state):
        return _dbp._resolve_city_id(cursor, city, state)

    # ------------------------------------------------------------------
    # LLM utilities (static-ish helpers kept for backward compat)
    # ------------------------------------------------------------------

    @staticmethod
    def _accumulate_usage(itinerary, response):
        _llm.accumulate_usage(itinerary, response)

    @staticmethod
    def _log_json_decode_error(response, response_text, err):
        _llm.log_json_decode_error(response, response_text, err)

    @staticmethod
    def _extract_completed_json(response):
        return _llm.extract_completed_json(response)

    @staticmethod
    def _find_json_objects(text):
        return _llm.find_json_objects(text)

    @staticmethod
    def _parse_itinerary_json(response_text):
        return _llm.parse_itinerary_json(response_text)

    @staticmethod
    def _sanitize_llm_json(text):
        return _llm.sanitize_llm_json(text)

    @staticmethod
    def _collect_used_place_names(days):
        return _llm.collect_used_place_names(days)

    @staticmethod
    def _days_from_obj(obj):
        return _llm.days_from_obj(obj)

    @staticmethod
    def _parse_days_json(response_text):
        return _llm.parse_days_json(response_text)

    # ------------------------------------------------------------------
    # Prompt builders (delegated to prompts/ package)
    # ------------------------------------------------------------------

    def generate_trip_skeleton_prompt(self, user_preferences, top_places, top_restaurants, top_hotels):
        from prompts import generate_trip_skeleton_prompt
        return generate_trip_skeleton_prompt(user_preferences, top_places, top_restaurants, top_hotels)

    def generate_travel_itinerary_prompt(self, user_preferences, top_places, top_restaurants, top_hotels, rest_slot_counts=None):
        from prompts import generate_travel_itinerary_prompt
        return generate_travel_itinerary_prompt(user_preferences, top_places, top_restaurants, top_hotels, rest_slot_counts=rest_slot_counts)

    def generate_extra_days_prompt(self, user_preferences, top_places, top_restaurants, top_hotels,
                                   start_day, num_days, used_places, rest_slot_counts=None):
        from prompts import generate_extra_days_prompt
        return generate_extra_days_prompt(
            user_preferences, top_places, top_restaurants, top_hotels,
            start_day=start_day, num_days=num_days, used_places=used_places,
            rest_slot_counts=rest_slot_counts,
        )

    def generate_edit_itinerary_prompt(self, user_preferences, top_places, top_restaurants, top_hotels,
                                       must_include_places, rest_slot_counts=None):
        from prompts import generate_edit_itinerary_prompt
        return generate_edit_itinerary_prompt(
            user_preferences, top_places, top_restaurants, top_hotels,
            must_include_places, rest_slot_counts=rest_slot_counts,
        )

    # ------------------------------------------------------------------
    # LLM call methods
    # ------------------------------------------------------------------

    def _generate_trip_skeleton(self, user_preferences, top_places, top_restaurants, top_hotels):
        return _llm.generate_trip_skeleton(self, user_preferences, top_places, top_restaurants, top_hotels)

    def _generate_detailed_itinerary(self, user_preferences, top_places, top_hotels, top_restaurants, rest_slot_counts=None):
        return _llm.generate_detailed_itinerary(self, user_preferences, top_places, top_hotels, top_restaurants, rest_slot_counts=rest_slot_counts)

    def _ensure_full_days(self, itinerary, user_preferences, places_trimmed, rests_trimmed, hotels_trimmed, rest_slot_counts=None):
        return _llm.ensure_full_days(self, itinerary, user_preferences, places_trimmed, rests_trimmed, hotels_trimmed, rest_slot_counts=rest_slot_counts)

    def _generate_extra_days(self, user_preferences, top_places, top_restaurants, top_hotels,
                             start_day, num_days, used_places, itinerary=None, rest_slot_counts=None):
        return _llm.generate_extra_days(
            self, user_preferences, top_places, top_restaurants, top_hotels,
            start_day=start_day, num_days=num_days, used_places=used_places,
            itinerary=itinerary, rest_slot_counts=rest_slot_counts,
        )

    def _generate_destination_description(self, destination):
        return _llm.generate_destination_description(self, destination)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @_lf_observe(name="generate_itinerary")
    def generate_itinerary(self, user_preferences):
        try:
            user_preferences['suggested_places'] = user_preferences.get('suggested_places', [])
            user_preferences['budget'] = user_preferences.get('budget', "")

            with _lf_propagate(
                user_id=str(user_preferences.get('_user_id') or ''),
                session_id=str(user_preferences.get('_session_id') or ''),
                metadata={
                    'destination': user_preferences.get('places_of_interest', ''),
                    'trip_type': user_preferences.get('trip_type', ''),
                    'trip_duration': user_preferences.get('trip_duration', ''),
                },
            ):
                with ThreadPoolExecutor(max_workers=3) as ex:
                    fut_places = ex.submit(self._get_place_recommendations, user_preferences)
                    fut_hotels = ex.submit(self._get_hotel_recommendations, user_preferences)
                    fut_rests  = ex.submit(self._get_restaurant_recommendations, user_preferences)
                    places                    = fut_places.result()
                    hotels                    = fut_hotels.result()
                    restaurants, rest_slots   = fut_rests.result()

                threading.Thread(target=self._enrich_place_images, args=(places,), daemon=True).start()

                itinerary = self._generate_detailed_itinerary(
                    user_preferences, places, hotels, restaurants,
                    rest_slot_counts=rest_slots,
                )

                return self._finalize_itinerary(
                    itinerary, places,
                    start_date=user_preferences.get('start_date'),
                    user_preferences=user_preferences,
                )

        except (KeyError, IndexError, TypeError) as e:
            print("Error while processing:", e)
            return {'status': 'error', 'message': str(e)}

    def generate_itinerary_stream(self, user_preferences):
        from flask import json
        import pandas as pd

        try:
            user_preferences['suggested_places'] = user_preferences.get('suggested_places', [])
            user_preferences['budget'] = user_preferences.get('budget', "")

            yield {'event': 'progress', 'step': 'started', 'message': 'Starting your itinerary...'}

            with ThreadPoolExecutor(max_workers=3) as ex:
                fut_places = ex.submit(self._get_place_recommendations, user_preferences)
                fut_hotels = ex.submit(self._get_hotel_recommendations, user_preferences)
                fut_rests  = ex.submit(self._get_restaurant_recommendations, user_preferences)
                places                    = fut_places.result()
                hotels                    = fut_hotels.result()
                restaurants, rest_slots   = fut_rests.result()

            threading.Thread(target=self._enrich_place_images, args=(places,), daemon=True).start()
            yield {'event': 'progress', 'step': 'places', 'message': 'Found places to visit'}

            n_places = _llm.places_count_for_trip(user_preferences.get('trip_duration', 3))
            places_trimmed = _llm.truncate_text_cols(self._trim_for_prompt(places, self._PLACE_COLS_PROMPT, n_places), max_chars=50)
            hotels_trimmed = self._trim_for_prompt(hotels, self._HOTEL_COLS_PROMPT, 10)
            rests_trimmed  = _llm.truncate_text_cols(self._trim_for_prompt(restaurants, self._REST_COLS_PROMPT, 15), max_chars=50)

            yield {'event': 'progress', 'step': 'info', 'message': 'Preparing your trip overview...'}

            # Fire skeleton and day-1 LLM calls concurrently — skeleton has no dependency on day-1.
            with ThreadPoolExecutor(max_workers=2) as ex:
                fut_skeleton = ex.submit(
                    self._generate_trip_skeleton,
                    user_preferences, places_trimmed, rests_trimmed, hotels_trimmed,
                )
                fut_day1 = ex.submit(
                    self._generate_extra_days,
                    user_preferences, places_trimmed, rests_trimmed, hotels_trimmed,
                    start_day=1, num_days=1, used_places=[],
                    itinerary=None, rest_slot_counts=rest_slots,
                )
            itinerary = fut_skeleton.result()
            day1_prefetched = fut_day1.result()

            itinerary.setdefault('itinerary', [])

            self._finalize_trip_level(itinerary, places)

            try:
                total_days = int(user_preferences.get('trip_duration'))
            except (TypeError, ValueError):
                total_days = len(itinerary.get('itinerary', [])) or 1

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

            yield {'event': 'images', 'images': itinerary.get('images', [])}

            trip_hotels = itinerary.get('hotels', []) or []
            if trip_hotels:
                hotel_ctx = {'city': itinerary.get('city'), 'state': itinerary.get('state'),
                             'itinerary': [], 'hotels': trip_hotels}
                self._attach_lat_long(hotel_ctx)
                trip_hotels = json.loads(json.dumps(trip_hotels, default=lambda x: '' if pd.isna(x) else x))
                itinerary['hotels'] = trip_hotels
                yield {'event': 'hotels', 'hotels': trip_hotels}

            for similar in itinerary.get('similar_places', []) or []:
                yield {'event': 'similar_place', 'item': similar}

            start_date = user_preferences.get('start_date')
            built_days = []
            used_places = []
            for day_no in range(1, total_days + 1):
                yield {'event': 'progress', 'step': f'day_{day_no}',
                       'message': f'Planning day {day_no} of {total_days}...'}
                extra = None
                # Use the pre-fetched day-1 result if available.
                if day_no == 1 and day1_prefetched:
                    extra = day1_prefetched
                else:
                    for attempt in range(3):
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
                day_no = len(built_days) + 1
                day['day'] = day_no
                finalized = self._finalize_days(
                    itinerary, [day], places, start_date=start_date,
                    start_day_index=day_no - 1,
                )
                day = finalized[0] if finalized else day
                built_days.append(day)
                used_places.extend(self._collect_used_place_names([day]))

                yield {'event': 'day_info', 'day': day_no,
                       'date': day.get('date', ''), 'weekday': day.get('weekday', ''),
                       'theme': day.get('theme', ''), 'day_summary': day.get('day_summary', '')}
                timeline = day.get('timeline', []) or []
                has_places = any(i.get('type') == 'place' for i in timeline)
                has_meals  = any(i.get('type') == 'meal'  for i in timeline)
                for item in timeline:
                    yield {'event': 'timeline_item', 'day': day_no, 'item': item}
                if has_places and has_meals:
                    yield {'event': 'ad_slot', 'day': day_no}
                meal_options = day.get('meal_options', {})
                if meal_options:
                    yield {'event': 'meal_options', 'day': day_no, 'options': meal_options}

            itinerary['itinerary'] = built_days
            itinerary['total_days'] = len(built_days)

            try:
                available = self._get_available_places(itinerary, user_preferences, 30, scored_df=places)
            except Exception as e:
                print(f"Warning: _get_available_places failed: {e}")
                available = []
            itinerary['available_places'] = available
            yield {'event': 'available_places', 'available_places': available}

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

    @_lf_observe(name="edit_itinerary", as_type="generation")
    def edit_itinerary(self, user_preferences):
        from prompts import generate_edit_itinerary_prompt

        try:
            user_preferences['suggested_places'] = user_preferences.get('suggested_places', [])
            user_preferences['budget'] = user_preferences.get('budget', "")

            with _lf_propagate(
                user_id=str(user_preferences.get('_user_id') or ''),
                session_id=str(user_preferences.get('_session_id') or ''),
                metadata={
                    'destination': user_preferences.get('places_of_interest', ''),
                    'trip_type': user_preferences.get('trip_type', ''),
                    'trip_duration': user_preferences.get('trip_duration', ''),
                },
            ):
                edit_places = user_preferences.get('places', []) or []
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

                n_places = _llm.places_count_for_trip(user_preferences.get('trip_duration', 3))
                places_trimmed = _llm.truncate_text_cols(self._trim_for_prompt(places, self._PLACE_COLS_PROMPT, n_places), max_chars=50)
                hotels_trimmed = self._trim_for_prompt(hotels, self._HOTEL_COLS_PROMPT, 10)
                rests_trimmed  = _llm.truncate_text_cols(self._trim_for_prompt(restaurants, self._REST_COLS_PROMPT, 15), max_chars=50)
                messages = generate_edit_itinerary_prompt(
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
                _lf_get_client().update_current_generation(
                    model=self.chat_deployment,
                    usage_details={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
                    output=response.output_text,
                )
                response_text = self._extract_completed_json(response)
                try:
                    itinerary = self._parse_itinerary_json(response_text)
                except ValueError as e:
                    self._log_json_decode_error(response, response_text, e)
                    raise

                self._accumulate_usage(itinerary, response)
                days_received = len(itinerary.get('itinerary') or [])
                target = user_preferences.get('trip_duration')
                print(f"[edit_itinerary] received {days_received}/{target} days (tokens used: in={response.usage.input_tokens} out={response.usage.output_tokens})")

                itinerary = self._ensure_full_days(
                    itinerary, user_preferences, places_trimmed, rests_trimmed, hotels_trimmed,
                    rest_slot_counts=rest_slots,
                )

                return self._finalize_itinerary(
                    itinerary, places,
                    start_date=user_preferences.get('start_date'),
                    user_preferences=user_preferences,
                )

        except (KeyError, IndexError, TypeError) as e:
            print("Error while processing edit:", e)
            return {'status': 'error', 'message': str(e)}
