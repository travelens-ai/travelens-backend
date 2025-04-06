from flask import Flask, request, jsonify
from Itenary_recommendation_model import ItenaryRecommendationSystem
import os

app = Flask(__name__)

# Initialize the recommendation system
api_key = os.getenv('GOOGLE_API_KEY', "AIzaSyAuJmq-huDk2yKbkF1-Kb1QacTA4Cs59wA")
recommender = ItenaryRecommendationSystem(api_key=api_key)
recommender.initialize()

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

@app.route('/generate-itinerary', methods=['POST'])
def generate_itinerary():
    try:
        user_preferences = request.json
        result = recommender.generate_itinerary(user_preferences)
        print("-------->",result)
        return  jsonify(result), 200
    except Exception as e:
        print(f"Error generating itinerary: {e}")
        # return jsonify({
        #     "status": "error",
        #     "message": str(e)
        # }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 4000))
    app.run(host='0.0.0.0', port=port)