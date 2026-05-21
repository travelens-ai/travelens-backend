from flask import Flask, request, jsonify
from Itenary_recommendation_model_jupiter import ItenaryRecommendationSystem
from generate_images import ImageGenerator
import os
import multiprocessing as mp
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

api_key = os.getenv('GOOGLE_API_KEY')
recommender = ItenaryRecommendationSystem(api_key=api_key)
recommender.initialize()
imageGenerator = ImageGenerator()

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

@app.route('/generate-itinerary', methods=['POST'])
def generate_itinerary():
    try:
        user_preferences = request.json
        result = recommender.generate_itinerary(user_preferences)
        return jsonify(result), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
    
@app.route('/popular-destination', methods=['GET'])
def get_popular_destination():
    try:
        result = recommender.get_popular_destination()
        return jsonify(result), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/generate-images', methods=['POST'])
def generate_images():
    try:
        user_preferences = request.json
        result = imageGenerator.getPlaces(user_preferences)
        return jsonify(result), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == '__main__':
    mp.set_start_method("spawn", force=True)
    port = int(os.environ.get('PORT', 4000))
    app.run(host='0.0.0.0', port=port, threaded=True)
