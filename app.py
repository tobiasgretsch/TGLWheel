import os
import json  # Import JSON
from flask import Flask, render_template, jsonify

app = Flask(__name__)

IMAGE_FOLDER = os.path.join('static', 'wheel_images')
DATA_FILE = 'wheel_data.json'  # Path to your JSON file
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif'}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/get_wheel_data')
def get_wheel_data():
    # 1. Get List of Images
    if not os.path.exists(IMAGE_FOLDER):
        os.makedirs(IMAGE_FOLDER)

    files = sorted([f for f in os.listdir(IMAGE_FOLDER) if os.path.splitext(f)[1].lower() in ALLOWED_EXTENSIONS])

    # 2. Load Custom Text Mapping
    custom_texts = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            try:
                custom_texts = json.load(f)
            except:
                print("Error loading JSON file")

    # 3. Build the Response Object
    response_data = []
    for filename in files:
        # Check if text exists in JSON, otherwise use filename without extension
        display_text = custom_texts.get(filename, os.path.splitext(filename)[0])

        response_data.append({
            "filename": filename,
            "path": f"wheel_images/{filename}",
            "text": display_text
        })

    return jsonify(response_data)


if __name__ == '__main__':
    app.run(debug=True, port=5000)