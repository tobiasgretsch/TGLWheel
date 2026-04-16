import io
import os
import json
import queue
import random
import time
import threading
import uuid

import segno
from flask import (Flask, render_template, jsonify, request,
                   Response, stream_with_context)
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# --- CONFIGURATION ---
IMAGE_FOLDER = os.path.join('static', 'wheel_images')
DATA_FILE = 'wheel_data.json'
TEAM_DATA_FILE = 'team_data.json'
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif'}

TEAM_COLORS = [
    "#B03030", "#1C3455", "#27ae60", "#d35400",
    "#8e44ad", "#2980b9", "#f39c12", "#1abc9c",
]


# ---------------------------------------------------------------------------
# SSE Broadcaster (reusable for both game and team streams)
# ---------------------------------------------------------------------------
class SSEBroadcaster:
    """Manages SSE subscribers for a named channel."""

    def __init__(self):
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=10)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def broadcast(self, data: dict):
        message = f"data: {json.dumps(data)}\n\n"
        with self._lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(message)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)


_game_sse = SSEBroadcaster()
_team_sse = SSEBroadcaster()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
_command_counter = 0
_team_lock = threading.Lock()


def _next_command_id():
    global _command_counter
    _command_counter += 1
    return _command_counter


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Game state (in-memory, not persisted)
# ---------------------------------------------------------------------------
game_state = {
    "command_id": 0,
    "command": None,
    "winner_index": None,
    "scores": {"left": 0, "right": 0},
    "show_events": False,
    "disabled_events": [],
    "active_match": None,  # {"game_index": 0, "home": "Team 1", "away": "Team 2"}
    "config": {
        "result_duration": 60,
        "global_time_remaining": 600,
        "global_timer_running": False,
        "global_timer_start": None,
        "global_timer_size": 3.0,
        "score_size": 5.0,
    },
}


def _effective_remaining():
    cfg = game_state["config"]
    if cfg["global_timer_running"] and cfg["global_timer_start"] is not None:
        elapsed = time.time() - cfg["global_timer_start"]
        return max(0.0, cfg["global_time_remaining"] - elapsed)
    return float(cfg["global_time_remaining"])


def _state_snapshot():
    return {
        "command_id": game_state["command_id"],
        "command": game_state["command"],
        "winner_index": game_state["winner_index"],
        "scores": dict(game_state["scores"]),
        "show_events": game_state["show_events"],
        "disabled_events": list(game_state["disabled_events"]),
        "active_match": game_state["active_match"],
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


# ---------------------------------------------------------------------------
# Team state (persisted to team_data.json)
# ---------------------------------------------------------------------------
def _default_team_state():
    return {
        "players": [],
        "teams": [],
        "schedule": [],
        "phase": "registration",
        "settings": {
            "num_teams": 4,
            "num_games": 1,
        },
    }


team_state = _default_team_state()


def _load_team_state():
    if os.path.exists(TEAM_DATA_FILE):
        try:
            with open(TEAM_DATA_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # Merge with defaults so new keys are always present
            default = _default_team_state()
            default.update(saved)
            if "settings" in saved:
                default["settings"] = {**_default_team_state()["settings"], **saved["settings"]}
            return default
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def _save_team_state():
    tmp = TEAM_DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(team_state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TEAM_DATA_FILE)


# Load persisted state on startup
_saved = _load_team_state()
if _saved:
    team_state.update(_saved)


def _team_state_snapshot():
    return {
        "players": list(team_state["players"]),
        "teams": list(team_state["teams"]),
        "schedule": list(team_state["schedule"]),
        "phase": team_state["phase"],
        "settings": dict(team_state["settings"]),
    }


# ---------------------------------------------------------------------------
# Round-robin schedule generation (circle method)
# ---------------------------------------------------------------------------
def _generate_round_robin(teams, num_games=1):
    n = len(teams)
    if n < 2:
        return []

    team_list = list(teams)
    if n % 2 == 1:
        team_list.append(None)
        n += 1

    schedule = []
    game_num = 0

    for pass_num in range(num_games):
        rotation = list(range(1, n))

        for _ in range(n - 1):
            pairs = [(0, rotation[-1])]
            for i in range((n - 2) // 2):
                pairs.append((rotation[i], rotation[n - 2 - 1 - i]))

            for home_idx, away_idx in pairs:
                home = team_list[home_idx]
                away = team_list[away_idx]
                if home is None or away is None:
                    continue
                if pass_num % 2 == 1:
                    home, away = away, home
                game_num += 1
                schedule.append({
                    "game": game_num,
                    "home": home["name"],
                    "away": away["name"],
                    "score_home": None,
                    "score_away": None,
                })

            rotation = [rotation[-1]] + rotation[:-1]

    return schedule


# ---------------------------------------------------------------------------
# SSE helper (shared by game and team streams)
# ---------------------------------------------------------------------------
def _make_sse_response(broadcaster: SSEBroadcaster, initial_snapshot: dict):
    def event_stream():
        q = broadcaster.subscribe()
        try:
            yield f"data: {json.dumps(initial_snapshot)}\n\n"
            while True:
                try:
                    yield q.get(timeout=30)
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            broadcaster.unsubscribe(q)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Game action handlers
# ---------------------------------------------------------------------------
def _handle_spin(payload):
    active = [img for img in get_images()
              if img["filename"] not in game_state["disabled_events"]]
    game_state["winner_index"] = random.randrange(len(active)) if active else None
    cfg = game_state["config"]
    if cfg["global_timer_running"]:
        cfg["global_time_remaining"] = _effective_remaining()
        cfg["global_timer_running"] = False
        cfg["global_timer_start"] = None


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


def _handle_set_active_match(payload):
    idx = _safe_int(payload.get("game_index"), -1)
    with _team_lock:
        schedule = team_state.get("schedule", [])
        if 0 <= idx < len(schedule):
            match = schedule[idx]
            game_state["active_match"] = {
                "game_index": idx,
                "home": match["home"],
                "away": match["away"],
            }
            game_state["scores"]["left"] = 0
            game_state["scores"]["right"] = 0
        elif idx == -1:
            game_state["active_match"] = None


def _handle_confirm_match_score(payload):
    active = game_state["active_match"]
    if not active:
        return
    idx = active["game_index"]
    score_home = game_state["scores"]["left"]
    score_away = game_state["scores"]["right"]
    with _team_lock:
        if 0 <= idx < len(team_state["schedule"]):
            team_state["schedule"][idx]["score_home"] = score_home
            team_state["schedule"][idx]["score_away"] = score_away
            _save_team_state()
            _team_sse.broadcast(_team_state_snapshot())
    game_state["active_match"] = None
    game_state["scores"]["left"] = 0
    game_state["scores"]["right"] = 0


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
    "set_active_match":     _handle_set_active_match,
    "confirm_match_score":  _handle_confirm_match_score,
}


# ---------------------------------------------------------------------------
# Team action handlers
# ---------------------------------------------------------------------------
def _handle_create_teams(payload):
    players = team_state["players"]
    n = team_state["settings"]["num_teams"]
    if len(players) < n:
        return

    goalkeepers = [p for p in players if p.get("position") == "goalkeeper"]
    field_players = [p for p in players if p.get("position") != "goalkeeper"]
    random.shuffle(goalkeepers)
    random.shuffle(field_players)

    teams = []
    for i in range(n):
        teams.append({
            "name": f"Team {i + 1}",
            "color": TEAM_COLORS[i % len(TEAM_COLORS)],
            "players": [],
        })

    # Assign one goalkeeper per team first, then remaining goalkeepers round-robin
    for idx, gk in enumerate(goalkeepers):
        teams[idx % n]["players"].append(gk["id"])

    # Distribute field players round-robin across teams
    for idx, fp in enumerate(field_players):
        teams[idx % n]["players"].append(fp["id"])

    team_state["teams"] = teams
    team_state["schedule"] = _generate_round_robin(teams, team_state["settings"]["num_games"])
    team_state["phase"] = "teams_created"


def _handle_reset_teams(payload):
    team_state["teams"] = []
    team_state["schedule"] = []
    team_state["phase"] = "registration"


def _handle_reset_all(payload):
    team_state["players"] = []
    team_state["teams"] = []
    team_state["schedule"] = []
    team_state["phase"] = "registration"


def _handle_remove_player(payload):
    pid = payload.get("id")
    if not pid or team_state["phase"] != "registration":
        return
    team_state["players"] = [p for p in team_state["players"] if p["id"] != pid]


def _handle_update_settings(payload):
    if "num_teams" in payload:
        team_state["settings"]["num_teams"] = max(2, min(20, _safe_int(payload["num_teams"], 4)))
    if "num_games" in payload:
        team_state["settings"]["num_games"] = max(1, min(3, _safe_int(payload["num_games"], 1)))


def _handle_update_match_score(payload):
    idx = _safe_int(payload.get("game_index"), -1)
    if 0 <= idx < len(team_state["schedule"]):
        match = team_state["schedule"][idx]
        if "score_home" in payload:
            match["score_home"] = _safe_int(payload["score_home"], match["score_home"])
        if "score_away" in payload:
            match["score_away"] = _safe_int(payload["score_away"], match["score_away"])


_TEAM_ACTION_HANDLERS = {
    "create_teams":       _handle_create_teams,
    "reset_teams":        _handle_reset_teams,
    "reset_all":          _handle_reset_all,
    "remove_player":      _handle_remove_player,
    "update_settings":    _handle_update_settings,
    "update_match_score": _handle_update_match_score,
}


# ===================================================================
# ROUTES
# ===================================================================

# --- Page routes ---
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/control")
def control():
    return render_template("control.html")


@app.route("/teams")
def teams_page():
    return render_template("teams.html")


@app.route("/register")
def register_page():
    return render_template("register.html")


# --- Game API ---
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

    handler = _ACTION_HANDLERS.get(action)
    if handler is None:
        return jsonify({"status": "error", "message": f"Unknown action: {action}"}), 400

    payload = data.get("payload", {})

    with _state_lock:
        game_state["command_id"] = _next_command_id()
        game_state["command"] = action
        handler(payload)
        snapshot = _state_snapshot()

    _game_sse.broadcast(snapshot)
    return jsonify({"status": "success", "state": snapshot})


@app.route("/api/stream")
def stream():
    with _state_lock:
        initial = _state_snapshot()
    return _make_sse_response(_game_sse, initial)


# --- Team API ---
@app.route("/api/register_player", methods=["POST"])
def register_player():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"status": "error", "message": "Name darf nicht leer sein"}), 400
    if len(name) > 50:
        return jsonify({"status": "error", "message": "Name zu lang (max 50 Zeichen)"}), 400

    with _team_lock:
        # Reject duplicate names (case-insensitive)
        existing = {p["name"].lower() for p in team_state["players"]}
        if name.lower() in existing:
            return jsonify({"status": "error", "message": "Name bereits registriert"}), 409

        position = data.get("position", "field")
        if position not in ("field", "goalkeeper"):
            position = "field"
        player = {"id": uuid.uuid4().hex[:12], "name": name, "position": position}
        team_state["players"].append(player)
        _save_team_state()
        snapshot = _team_state_snapshot()

    _team_sse.broadcast(snapshot)
    return jsonify({"status": "success", "player": player})


@app.route("/api/team_state")
def get_team_state():
    with _team_lock:
        return jsonify(_team_state_snapshot())


@app.route("/api/team_command", methods=["POST"])
def team_command():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    action = data.get("action")
    if not action:
        return jsonify({"status": "error", "message": "Missing action"}), 400

    handler = _TEAM_ACTION_HANDLERS.get(action)
    if handler is None:
        return jsonify({"status": "error", "message": f"Unknown action: {action}"}), 400

    payload = data.get("payload", {})

    with _team_lock:
        handler(payload)
        _save_team_state()
        snapshot = _team_state_snapshot()

    _team_sse.broadcast(snapshot)
    return jsonify({"status": "success", "state": snapshot})


@app.route("/api/team_stream")
def team_stream():
    with _team_lock:
        initial = _team_state_snapshot()
    return _make_sse_response(_team_sse, initial)


@app.route("/api/qr_code")
def qr_code():
    url = request.url_root.rstrip("/") + "/register"
    qr = segno.make(url)
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=8, dark="#ffffff", light="#1a2332")
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-cache"})


# ===================================================================
if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
