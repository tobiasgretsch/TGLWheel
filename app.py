import os
import json
import queue
import random
import time
import threading
from flask import Flask, render_template, jsonify, request, Response, stream_with_context

app = Flask(__name__)

# --- CONFIGURATION ---
IMAGE_FOLDER = os.path.join('static', 'wheel_images')
DATA_FILE = 'wheel_data.json'
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif'}

# --- GLOBAL STATE ---
_state_lock = threading.Lock()
_command_counter = 0

# --- SSE SUBSCRIBERS ---
_subscribers: list[queue.Queue] = []
_subscribers_lock = threading.Lock()


def _notify_subscribers(snapshot: dict) -> None:
    """Push a state snapshot to every connected SSE client."""
    message = f"data: {json.dumps(snapshot)}\n\n"
    with _subscribers_lock:
        dead = [q for q in _subscribers if not _try_put(q, message)]
        for q in dead:
            _subscribers.remove(q)


def _try_put(q: queue.Queue, message: str) -> bool:
    try:
        q.put_nowait(message)
        return True
    except queue.Full:
        return False

game_state = {
    "command_id": 0,
    "command": None,
    "winner_index": None,         # sector index chosen by server on spin
    "scores": {"left": 0, "right": 0},
    "show_events": False,
    "disabled_events": [],        # filenames removed from the wheel
    "config": {
        "result_duration": 60,
        "global_time_remaining": 600,
        "global_timer_running": False,
        "global_timer_start": None,  # time.time() when the timer was last started
        "global_timer_size": 3.0,   # rem units for the on-screen timer font size
        "score_size": 5.0,          # rem units for the score digits
    },
}


def _next_command_id():
    global _command_counter
    _command_counter += 1
    return _command_counter


def _safe_int(value, default):
    """Convert value to int, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default):
    """Convert value to float, returning default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
        "winner_index": game_state["winner_index"],
        "scores": dict(game_state["scores"]),
        "show_events": game_state["show_events"],
        "disabled_events": list(game_state["disabled_events"]),
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


def _handle_spin(payload):
    active = [img for img in get_images()
              if img["filename"] not in game_state["disabled_events"]]
    game_state["winner_index"] = random.randrange(len(active)) if active else None


def _handle_update_score(payload):
    side = payload.get("side")
    change = payload.get("change", 0)
    if side in ("left", "right"):
        game_state["scores"][side] = max(0, game_state["scores"][side] + change)


def _handle_reset_scores(payload):
    game_state["scores"]["left"] = 0
    game_state["scores"]["right"] = 0


def _handle_set_timers(payload):
    if "result_duration" in payload:
        game_state["config"]["result_duration"] = _safe_int(
            payload["result_duration"], game_state["config"]["result_duration"])
    if "global_time" in payload:
        game_state["config"]["global_time_remaining"] = _safe_int(
            payload["global_time"], game_state["config"]["global_time_remaining"])
        game_state["config"]["global_timer_running"] = False
        game_state["config"]["global_timer_start"] = None


def _handle_set_score_size(payload):
    size = _safe_float(payload.get("size"), 5.0)
    game_state["config"]["score_size"] = max(1.0, min(20.0, size))


def _handle_reset(payload):
    game_state["disabled_events"] = []


def _handle_disable_event(payload):
    filename = payload.get("filename")
    if filename and filename not in game_state["disabled_events"]:
        game_state["disabled_events"].append(filename)


def _handle_set_disabled_events(payload):
    game_state["disabled_events"] = list(payload.get("events", []))


def _handle_toggle_events(payload):
    game_state["show_events"] = not game_state["show_events"]


def _handle_set_timer_size(payload):
    size = _safe_float(payload.get("size"), 3.0)
    game_state["config"]["global_timer_size"] = max(1.0, min(12.0, size))


def _handle_control_global_timer(payload):
    state = payload.get("state")
    cfg = game_state["config"]
    if state == "start" and not cfg["global_timer_running"]:
        cfg["global_timer_running"] = True
        cfg["global_timer_start"] = time.time()
    elif state == "stop" and cfg["global_timer_running"]:
        cfg["global_time_remaining"] = _effective_remaining()
        cfg["global_timer_running"] = False
        cfg["global_timer_start"] = None


_ACTION_HANDLERS = {
    "spin":                 _handle_spin,
    "update_score":         _handle_update_score,
    "reset_scores":         _handle_reset_scores,
    "set_timers":           _handle_set_timers,
    "set_score_size":       _handle_set_score_size,
    "reset":                _handle_reset,
    "disable_event":        _handle_disable_event,
    "set_disabled_events":  _handle_set_disabled_events,
    "toggle_events":        _handle_toggle_events,
    "set_timer_size":       _handle_set_timer_size,
    "control_global_timer": _handle_control_global_timer,
}


@app.route("/api/send_command", methods=["POST"])
def send_command():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    action = data.get("action")
    if not action:
        return jsonify({"status": "error", "message": "Missing action"}), 400

    handler = _ACTION_HANDLERS.get(action)
    if handler is None:
        return jsonify({"status": "error", "message": f"Unknown action: {action}"}), 400

    payload = data.get("payload", {})

    with _state_lock:
        game_state["command_id"] = _next_command_id()
        game_state["command"] = action
        handler(payload)
        snapshot = _state_snapshot()

    _notify_subscribers(snapshot)
    return jsonify({"status": "success", "state": snapshot})


@app.route("/api/stream")
def stream():
    def event_stream():
        q: queue.Queue = queue.Queue(maxsize=10)
        with _subscribers_lock:
            _subscribers.append(q)
        try:
            # Send current state immediately so the client is up to date on connect.
            with _state_lock:
                initial = _state_snapshot()
            yield f"data: {json.dumps(initial)}\n\n"
            while True:
                try:
                    yield q.get(timeout=30)
                except queue.Empty:
                    # Heartbeat keeps the connection alive through proxies and load balancers.
                    yield ": heartbeat\n\n"
        finally:
            with _subscribers_lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
