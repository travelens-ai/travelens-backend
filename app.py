from flask import Flask, request, jsonify
from Itenary_recommendation_model_jupiter import ItenaryRecommendationSystem
import os
import multiprocessing as mp
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

recommender = None  # global placeholder

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

if __name__ == '__main__':
    # 🛡️ Safe multiprocessing for ONNX, transformers, etc.
    mp.set_start_method("spawn", force=True)

    # ✅ Initialize recommender inside __main__ (safe zone)
    api_key = os.getenv('GOOGLE_API_KEY', "AIzaSyDrsp2VLdY5q_ZztVQBfFS8AboxnYl9Aas")
    recommender = ItenaryRecommendationSystem(api_key=api_key)
    recommender.initialize()

    # 🚀 Dev mode with threading
    port = int(os.environ.get('PORT', 4000))
    app.run(host='0.0.0.0', port=port, threaded=True)
