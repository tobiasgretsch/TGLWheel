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
game_state = {
    "command_id": 0,
    "command": None,  # 'spin', 'reset', 'update_config'

    # Persistent Data
    "scores": {"left": 0, "right": 0},
    "config": {
        "result_duration": 60,  # Default 60s
        "global_time_set": 600,  # Default 10 mins (in seconds)
        "global_timer_running": False
    }
}


def get_images():
    # ... (Same as before) ...
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


@app.route('/api/check_status')
def check_status():
    # Return the full state including scores
    return jsonify(game_state)


# --- UNIFIED CONTROL API ---
@app.route('/api/send_command', methods=['POST'])
def send_command():
    data = request.json
    action = data.get('action')
    payload = data.get('payload', {})

    game_state['command_id'] = time.time()
    game_state['command'] = action

    # Logic to update server-side state
    if action == 'update_score':
        side = payload.get('side')
        change = payload.get('change', 0)
        if side in ['left', 'right']:
            game_state['scores'][side] += change
            if game_state['scores'][side] < 0: game_state['scores'][side] = 0

    elif action == 'reset_scores':
        game_state['scores']['left'] = 0
        game_state['scores']['right'] = 0

    elif action == 'set_timers':
        # Update default configs
        if 'result_duration' in payload:
            game_state['config']['result_duration'] = int(payload['result_duration'])
        if 'global_time' in payload:
            game_state['config']['global_time_set'] = int(payload['global_time'])
            # Stop timer if we reset the time
            game_state['config']['global_timer_running'] = False

    elif action == 'control_global_timer':
        state = payload.get('state')  # 'start' or 'stop'
        game_state['config']['global_timer_running'] = (state == 'start')

    return jsonify({"status": "success", "state": game_state})


if __name__ == '__main__':
    app.run(debug=True, port=5000, threaded=True)