import os
import json
import time
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# --- CONFIGURATION ---
IMAGE_FOLDER = os.path.join('static', 'wheel_images')
DATA_FILE = 'wheel_data.json'
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif'}

# --- GLOBAL STATE STORE ---
# This holds the last command sent by the control page
game_state = {
    "command_id": 0,  # Unique ID to prevent repeating the same command
    "command": None  # 'spin' or 'reset'
}


def get_images():
    if not os.path.exists(IMAGE_FOLDER):
        os.makedirs(IMAGE_FOLDER)
    files = sorted([f for f in os.listdir(IMAGE_FOLDER) if os.path.splitext(f)[1].lower() in ALLOWED_EXTENSIONS])

    custom_texts = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            try:
                custom_texts = json.load(f)
            except:
                pass

    response_data = []
    for filename in files:
        display_text = custom_texts.get(filename, os.path.splitext(filename)[0])
        response_data.append({
            "filename": filename,
            "path": f"wheel_images/{filename}",
            "text": display_text
        })
    return response_data


# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/control')
def control():
    return render_template('control.html')


@app.route('/api/get_wheel_data')
def get_wheel_data():
    return jsonify(get_images())


# --- COMMAND API (For the Control Page) ---
@app.route('/api/send_command', methods=['POST'])
def send_command():
    data = request.json
    action = data.get('action')

    if action in ['spin', 'reset']:
        game_state['command'] = action
        game_state['command_id'] = time.time()  # Use timestamp as unique ID
        return jsonify({"status": "success", "action": action})

    return jsonify({"status": "error"}), 400


# --- STATUS API (For the Index Page to Poll) ---
@app.route('/api/check_status')
def check_status():
    return jsonify(game_state)


if __name__ == '__main__':
    # threaded=True is helpful for handling the polling requests smoothly
    app.run(debug=True, port=5000, threaded=True)