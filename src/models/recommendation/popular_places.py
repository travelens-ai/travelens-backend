import os
import pickle
import pandas as pd

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))


def schedule_similar_places(system):
    update_similar_places(system)


def update_similar_places(system):
    try:
        similar_places_df = pd.read_csv(os.path.join(_PROJECT_ROOT, 'similar_places.csv'))
        final_places = {}
        for row in similar_places_df.itertuples(index=False):
            final_places[row.placename] = row._asdict()
        with open(os.path.join(_PROJECT_ROOT, 'similar_places.pkl'), 'wb') as f:
            pickle.dump(final_places, f)
        print("similar_places.pkl generated or updated successfully.")
    except Exception as e:
        print(f"Error generating or updating similar_places.pkl: {str(e)}")


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
    try:
        path = os.path.join(_PROJECT_ROOT, 'similar_places.pkl')
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, 'rb') as f:
                return pickle.load(f)
        else:
            print("similar_places.pkl is missing or empty.")
            return {}
    except FileNotFoundError:
        print("No popular destination found. Please run set_popular_destination() first.")
        return None


def save_similar_places(system, similar_places):
    csv_file = 'similar_places.csv'
    try:
        existing_df = pd.read_csv(csv_file)
    except FileNotFoundError:
        existing_df = pd.DataFrame(
            columns=['placename', 'description', 'state', 'image', 'price_estimated_range']
        )

    for place in similar_places:
        if place['placename'] not in existing_df['placename'].values or not place.get('image'):
            place['image'] = ''

    try:
        similar_places_df = pd.DataFrame(similar_places)
        similar_places_df['image'] = similar_places_df['image'].fillna('').replace({None: ''})
        new_places_df = similar_places_df[~similar_places_df['placename'].isin(existing_df['placename'])]
        if not new_places_df.empty:
            updated_df = pd.concat([existing_df, new_places_df], ignore_index=True)
            updated_df.to_csv(csv_file, index=False)
            update_similar_places(system)
        else:
            print("No new similar places to update.")
    except Exception as e:
        print(f"Error saving similar places to CSV: {str(e)}")
