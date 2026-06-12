from google import genai
from google.genai import types
from PIL import Image
import os
from io import BytesIO
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Create output folder if it doesn't exist
output_dir = "generated_images"
os.makedirs(output_dir, exist_ok=True)

class ImageGenerator:
    generation_model = None
    places_df = None

    def __init__(self):
        api_key = os.getenv('GOOGLE_API_KEY')
        if api_key:
            self.genai_client = genai.Client(api_key=api_key)
        else:
            self.genai_client = None

    def getPlaces(self, user_preferences: dict):
        places_df = pd.read_csv("indian_travel_places.csv")
        self.places_df = places_df

        for index, row in self.places_df.iterrows():
            if user_preferences['start'] <= index <= user_preferences['end']:
                print("Generating image for:", row['name'], row['city'], row['state'])

                place_parts = [str(row.get(key, "")) for key in ['name', 'city', 'state'] if pd.notna(row.get(key, ""))]
                place_str = " ".join(place_parts)

                image_path = self.generate_and_save_image(place_str)
                image_filename = os.path.basename(image_path) if image_path else ""

                # Save filename to the 'image' column
                self.places_df.at[index, 'image'] = image_filename

        # Save the updated DataFrame once after all iterations
        self.places_df.to_csv('indian_travel_places.csv', index=False)
        print("Updated CSV saved as 'indian_travel_places.csv'")

    def generate_and_save_image(self, place: str) -> str:
        if not self.genai_client:
            return ""
        try:
            response = self.genai_client.models.generate_images(
                model='imagen-3.0-generate-002',
                prompt=f"Generate an image of a place {place} to display on a travel itinerary",
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="16:9",
                    add_watermark=True,
                ),
            )

            if not response.generated_images:
                print("No image generated.")
                return ""

            img_bytes = response.generated_images[0].image.image_bytes
            pil_image = Image.open(BytesIO(img_bytes))
            pil_image = pil_image.resize((640, 360))

            buffer = BytesIO()
            pil_image.save(buffer, format="WEBP", quality=100, optimize=True)
            size_kb = buffer.tell() / 1024

            filename = f"{place.replace(' ', '_')}.webp"
            filepath = os.path.join(output_dir, filename)

            with open(filepath, "wb") as f:
                f.write(buffer.getvalue())

            print(f"Image saved to {filepath} ({size_kb:.2f} KB)")
            return filepath
        except Exception as e:
            print(f"Error processing image for {place}: {e}")
            return ""
