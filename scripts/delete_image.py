"""Delete a specific image from the images table, place_image_map, and filesystem.

Usage:
    PYTHONPATH=src venv/bin/python scripts/delete_image.py <image_name>

Example:
    PYTHONPATH=src venv/bin/python scripts/delete_image.py Kolukkumalai_Tea_Estate_Munnar_Kerala_0.webp
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.config import *  # loads .env
from core.db import get_connection

IMAGE_DIR = os.path.join(os.path.dirname(__file__), '..', 'generated_images')


def delete_image(image_name: str):
    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute("SELECT id FROM images WHERE image_name = ?", (image_name,))
        row = cur.fetchone()
        if not row:
            print(f"Not found in images table: {image_name!r}")
            return

        image_id = row[0]

        cur.execute("DELETE FROM place_image_map WHERE image_id = ?", (image_id,))
        pim_deleted = cur.rowcount
        print(f"Deleted {pim_deleted} row(s) from place_image_map")

        cur.execute("DELETE FROM images WHERE id = ?", (image_id,))
        print(f"Deleted from images table: {image_name!r} (id={image_id})")

        conn.commit()

        file_path = os.path.join(IMAGE_DIR, image_name)
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Deleted file: {file_path}")
        else:
            print(f"File not found on disk (already gone or served remotely): {file_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/delete_image.py <image_name>")
        sys.exit(1)
    delete_image(sys.argv[1])
