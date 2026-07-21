import re
import numpy as np
from numpy import dot
from numpy.linalg import norm

_user_embedding_cache = {}  # keyed by (trip_type, sorted activities)


def merge_list(system, activities):
    if len(activities) == 0:
        return ""
    if len(activities) == 1:
        return activities[0]
    return ", ".join(activities[:-1]) + " and " + activities[-1]


def normalize(system, text):
    return re.sub(r'[^\w\s]', '', str(text).lower().strip())


def generate_user_embedding(system, user_preferences):
    cache_key = (
        user_preferences.get('trip_type', ''),
        tuple(sorted(str(a) for a in user_preferences.get('preferred_activities', [])))
    )
    if cache_key in _user_embedding_cache:
        return _user_embedding_cache[cache_key]
    query = (
        'The user prefers trips focused on ' + user_preferences['trip_type'] +
        '. They are also interested in activities such as ' +
        merge_list(system, user_preferences['preferred_activities']) + '.'
    )
    user_activity_embedding = system._encode([query])[0]
    _user_embedding_cache[cache_key] = user_activity_embedding
    return user_activity_embedding


def compute_activity_score(system, activity_dict, user_activity_embedding):
    if not activity_dict:
        return 0
    score = 0.0
    for activity, rating in activity_dict.items():
        if not activity:
            continue
        activity_embedding = system.activity_embeddings.get(activity)
        if activity_embedding is None:
            activity_embedding = system._encode([activity])[0]
            activity_embedding = activity_embedding / norm(activity_embedding)
            system.activity_embeddings[activity] = activity_embedding
        similarity = dot(activity_embedding, user_activity_embedding)
        score += rating * similarity
    return score


def compute_trip_type_score(system, place_type, user_trip_type_embedding):
    import pandas as pd
    if place_type is None or (isinstance(place_type, float) and pd.isna(place_type)) \
            or not str(place_type).strip():
        return 0.0
    place_type_embedding = system.place_type_embeddings.get(place_type)
    if place_type_embedding is None:
        place_type_embedding = system._encode([place_type])[0]
        place_type_embedding = place_type_embedding / norm(place_type_embedding)
        system.place_type_embeddings[place_type] = place_type_embedding
    return dot(place_type_embedding, user_trip_type_embedding)


def weighted_place_rating(system, row, C):
    m = 50
    R = float(row['rating'])
    v = float(row['no of rating'])
    return (v / (v + m)) * R + (m / (v + m)) * C


def weighted_restaurants_rating(system, row, C):
    m = 50
    R = float(row['Rating'])
    v = float(row['Votes'])
    return (v / (v + m)) * R + (m / (v + m)) * C


def calculate_similarity_scores(system, text1, text2):
    embedding1 = system._encode([text1])[0]
    embedding1 = embedding1 / norm(embedding1)
    embedding2 = system._encode([text2])[0]
    embedding2 = embedding2 / norm(embedding2)
    return dot(embedding1, embedding2)
