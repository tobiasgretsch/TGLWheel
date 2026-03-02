import os
import json
import time
import threading
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# --- CONFIGURATION ---
IMAGE_FOLDER = os.path.join('static', 'wheel_images')
DATA_FILE = 'wheel_data.json'
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif'}

# --- GLOBAL STATE ---
_state_lock = threading.Lock()
_command_counter = 0

game_state = {
    "command_id": 0,
    "command": None,
    "scores": {"left": 0, "right": 0},
    "config": {
        "result_duration": 60,
        "global_time_remaining": 600,
        "global_timer_running": False,
        "global_timer_start": None,  # time.time() when the timer was last started
    },
}


def _next_command_id():
    global _command_counter
    _command_counter += 1
    return _command_counter


def _effective_remaining():
    """Return remaining seconds, accounting for elapsed time if the timer is running."""
    cfg = game_state["config"]
    if cfg["global_timer_running"] and cfg["global_timer_start"] is not None:
        elapsed = time.time() - cfg["global_timer_start"]
        return max(0.0, cfg["global_time_remaining"] - elapsed)
    return float(cfg["global_time_remaining"])


def _state_snapshot():
    """Return a serialisable copy of game_state with the effective remaining time."""
    return {
        "command_id": game_state["command_id"],
        "command": game_state["command"],
        "scores": dict(game_state["scores"]),
        "config": {
            **game_state["config"],
            "global_time_remaining": _effective_remaining(),
        },
    }


def get_images():
    if not os.path.exists(IMAGE_FOLDER):
        os.makedirs(IMAGE_FOLDER)
    files = sorted(
        f for f in os.listdir(IMAGE_FOLDER)
        if os.path.splitext(f)[1].lower() in ALLOWED_EXTENSIONS
    )
    custom_texts = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                custom_texts = json.load(f)
            except json.JSONDecodeError:
                pass
    return [
        {
            "filename": filename,
            "path": f"wheel_images/{filename}",
            "text": custom_texts.get(filename, os.path.splitext(filename)[0]),
        }
        for filename in files
    ]


# --- ROUTES ---
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/control")
def control():
    return render_template("control.html")


@app.route("/api/get_wheel_data")
def get_wheel_data():
    return jsonify(get_images())


@app.route("/api/check_status")
def check_status():
    with _state_lock:
        return jsonify(_state_snapshot())


@app.route("/api/send_command", methods=["POST"])
def send_command():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    action = data.get("action")
    if not action:
        return jsonify({"status": "error", "message": "Missing action"}), 400
    payload = data.get("payload", {})

    with _state_lock:
        game_state["command_id"] = _next_command_id()
        game_state["command"] = action

        if action == "update_score":
            side = payload.get("side")
            change = payload.get("change", 0)
            if side in ("left", "right"):
                game_state["scores"][side] = max(0, game_state["scores"][side] + change)

        elif action == "reset_scores":
            game_state["scores"]["left"] = 0
            game_state["scores"]["right"] = 0

        elif action == "set_timers":
            if "result_duration" in payload:
                game_state["config"]["result_duration"] = int(payload["result_duration"])
            if "global_time" in payload:
                game_state["config"]["global_time_remaining"] = int(payload["global_time"])
                game_state["config"]["global_timer_running"] = False
                game_state["config"]["global_timer_start"] = None

        elif action == "control_global_timer":
            state = payload.get("state")
            cfg = game_state["config"]
            if state == "start" and not cfg["global_timer_running"]:
                cfg["global_timer_running"] = True
                cfg["global_timer_start"] = time.time()
            elif state == "stop" and cfg["global_timer_running"]:
                # Freeze current remaining before clearing the running state
                cfg["global_time_remaining"] = _effective_remaining()
                cfg["global_timer_running"] = False
                cfg["global_timer_start"] = None

        snapshot = _state_snapshot()

    return jsonify({"status": "success", "state": snapshot})


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
