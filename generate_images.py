from vertexai.preview.vision_models import ImageGenerationModel
import vertexai
from PIL import Image
import os
from io import BytesIO
import pandas as pd

# Create output folder if it doesn't exist
output_dir = "generated_images"
os.makedirs(output_dir, exist_ok=True)

class ImageGenerator:
    generation_model = None
    places_df = None
    
    def __init__(self):
        vertexai.init(project="glanceai-prod-5aea", location="us-central1")
        self.generation_model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-002")

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
        print("✅ Updated CSV saved as 'indian_travel_places.csv'")

    def generate_and_save_image(self, place: str) -> str:
        try:
            images = self.generation_model.generate_images(
                prompt=f"Generate an image of a place {place} to display on a travel itinerary",
                number_of_images=1,
                aspect_ratio="16:9",
                negative_prompt="",
                person_generation="",
                safety_filter_level="",
                add_watermark=True,
            )

            if not images:
                print("No image generated.")
                return ""

            pil_image = images[0]._pil_image
            pil_image = pil_image.resize((640, 360))

            buffer = BytesIO()
            pil_image.save(buffer, format="WEBP", quality=100, optimize=True)
            size_kb = buffer.tell() / 1024

            filename = f"{place.replace(' ', '_')}.webp"
            filepath = os.path.join(output_dir, filename)

            with open(filepath, "wb") as f:
                f.write(buffer.getvalue())

            print(f"🖼️ Image saved to {filepath} ({size_kb:.2f} KB)")
            return filepath
        except Exception as e:
            print(f"⚠️ Error processing image for {place}: {e}")
            return ""
