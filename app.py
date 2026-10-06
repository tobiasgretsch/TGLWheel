import hashlib
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
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Static files (wheel images, logo) are cached for a day. CSS/JS URLs carry a
# ?v=<startup time> query so a deploy always busts the cache for code.
STATIC_MAX_AGE_SECONDS = 86400
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = STATIC_MAX_AGE_SECONDS
STATIC_VERSION = str(int(time.time()))


@app.context_processor
def _inject_static_version():
    return {"static_version": STATIC_VERSION}

# --- CONFIGURATION ---
IMAGE_FOLDER = os.path.join('static', 'wheel_images')
DATA_FILE = 'wheel_data.json'
TEAM_DATA_FILE = 'team_data.json'
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif'}

TEAM_COLORS = [
    "#B03030", "#1C3455", "#27ae60", "#d35400",
    "#8e44ad", "#2980b9", "#f39c12", "#1abc9c",
]

# Display phase, owned by the server so a display can restore itself after a
# reload or reconnect and the control panel knows when a spin is allowed.
PHASE_WHEEL = "wheel"        # wheel visible, spin allowed
PHASE_SPINNING = "spinning"  # winner chosen, display is animating the spin
PHASE_RESULT = "result"      # result screen visible, winner disabled

# A real spin animation lands within ~8 s. If no display acknowledges the spin
# for this long (no display open, callback lost), the phase is stale and a new
# spin is allowed rather than forcing the operator to reset the whole wheel.
SPIN_STALE_SECONDS = 20

# Events added from the control panel. Beyond 12 the 82 px images no longer fit
# side by side on the 600 px wheel. Uploads are scaled down in the browser first,
# so the byte limit only guards against misuse.
MAX_WHEEL_EVENTS = 12
MAX_EVENT_TEXT_LENGTH = 200
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
# The file type is taken from the content, never from the uploaded name.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


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
            for q in self._subscribers:
                self._enqueue_latest(q, message)

    @staticmethod
    def _enqueue_latest(q: queue.Queue, message: str):
        """Every message is a full snapshot, so a client that cannot keep up only
        needs the newest one. Drop the oldest queued message rather than the client:
        an evicted client would keep its connection (heartbeats still flow) but never
        receive state again until someone refreshes the page."""
        while True:
            try:
                q.put_nowait(message)
                return
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass


_game_sse = SSEBroadcaster()
_team_sse = SSEBroadcaster()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
# Seeded from the clock so ids never repeat across restarts — a display that
# remembers an id from the previous process must not ignore a fresh command.
_command_counter = int(time.time())
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
    "phase": PHASE_WHEEL,
    "winner_index": None,     # index into the active (non-disabled) image list
    "winner_filename": None,  # survives the winner being disabled
    "spin_started_at": None,  # time.time() of the last accepted spin
    "scores": {"left": 0, "right": 0},
    "show_events": False,
    "disabled_events": [],
    "active_match": None,  # {"game_index": 0, "home": "Team 1", "away": "Team 2"}
    "config": {
        "result_duration": 60,
        "global_time_total": 600,      # last time set, so clients can tell START from WEITER
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


def _settle_global_timer():
    """A running clock that has reached zero is stopped, so every client sees it as
    expired instead of 'running at 00:00' (and PAUSE/START make sense again)."""
    cfg = game_state["config"]
    if cfg["global_timer_running"] and _effective_remaining() <= 0:
        cfg["global_time_remaining"] = 0
        cfg["global_timer_running"] = False
        cfg["global_timer_start"] = None


def _state_snapshot():
    _settle_global_timer()
    images = get_images()
    return {
        "command_id": game_state["command_id"],
        "command": game_state["command"],
        "phase": game_state["phase"],
        "winner_index": game_state["winner_index"],
        "winner_filename": game_state["winner_filename"],
        "scores": dict(game_state["scores"]),
        "show_events": game_state["show_events"],
        "disabled_events": list(game_state["disabled_events"]),
        "active_match": game_state["active_match"],
        # Lets every client notice added/removed images and edited texts without polling.
        "wheel_files": [img["filename"] for img in images],
        "wheel_signature": _wheel_signature(images),
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
    custom_texts = _read_wheel_texts()
    return [
        {
            "filename": filename,
            "path": f"wheel_images/{filename}",
            "text": custom_texts.get(filename, os.path.splitext(filename)[0]),
        }
        for filename in files
    ]


def _read_wheel_texts():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            texts = json.load(f)
        except json.JSONDecodeError:
            return {}
    return texts if isinstance(texts, dict) else {}


def _write_json_atomic(path, data, indent):
    """Writes via a temp file so a crash never leaves a half-written file behind."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def _wheel_signature(images):
    """Changes whenever an image or a text changes, so clients know to reload."""
    content = json.dumps([[img["filename"], img["text"]] for img in images], ensure_ascii=False)
    return hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]


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
    _write_json_atomic(TEAM_DATA_FILE, team_state, indent=2)


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
# A handler mutates game_state and returns None on success, or an error
# message (str) — in which case nothing is broadcast and the caller gets a 400.
# ---------------------------------------------------------------------------
def _spin_is_stale():
    started = game_state["spin_started_at"]
    return started is not None and time.time() - started > SPIN_STALE_SECONDS


def _handle_spin(payload):
    phase = game_state["phase"]
    if phase == PHASE_RESULT:
        return "Rad ist nicht bereit — Ergebnis wird noch angezeigt"
    if phase == PHASE_SPINNING and not _spin_is_stale():
        return "Rad dreht sich noch"
    active = [img for img in get_images()
              if img["filename"] not in game_state["disabled_events"]]
    if not active:
        return "Keine aktiven Ereignisse — bitte RESET drücken"
    winner_index = random.randrange(len(active))
    game_state["winner_index"] = winner_index
    game_state["winner_filename"] = active[winner_index]["filename"]
    game_state["spin_started_at"] = time.time()
    game_state["phase"] = PHASE_SPINNING
    game_state["show_events"] = False  # the audience must see the wheel turn
    cfg = game_state["config"]
    if cfg["global_timer_running"]:
        cfg["global_time_remaining"] = _effective_remaining()
        cfg["global_timer_running"] = False
        cfg["global_timer_start"] = None


def _handle_update_score(payload):
    side = payload.get("side")
    if side not in ("left", "right"):
        return "Ungültige Seite"
    change = _safe_int(payload.get("change"), 0)
    game_state["scores"][side] = max(0, game_state["scores"][side] + change)


def _handle_reset_scores(payload):
    game_state["scores"]["left"] = 0
    game_state["scores"]["right"] = 0


def _handle_set_timers(payload):
    if "result_duration" in payload:
        game_state["config"]["result_duration"] = _safe_int(
            payload["result_duration"], game_state["config"]["result_duration"])
    if "global_time" in payload:
        total = _safe_int(payload["global_time"], game_state["config"]["global_time_total"])
        game_state["config"]["global_time_total"] = total
        game_state["config"]["global_time_remaining"] = total
        game_state["config"]["global_timer_running"] = False
        game_state["config"]["global_timer_start"] = None


def _handle_set_score_size(payload):
    size = _safe_float(payload.get("size"), 5.0)
    game_state["config"]["score_size"] = max(1.0, min(20.0, size))


def _enter_wheel_phase():
    game_state["winner_index"] = None
    game_state["winner_filename"] = None
    game_state["spin_started_at"] = None
    game_state["phase"] = PHASE_WHEEL


def _handle_reset(payload):
    game_state["disabled_events"] = []
    _enter_wheel_phase()


def _handle_spin_finished(payload):
    """Sent by the display when the spin animation lands. Idempotent, so a second
    display (or a retry) cannot disable anything twice or change the phase."""
    if game_state["phase"] != PHASE_SPINNING:
        return None
    filename = game_state["winner_filename"]
    if filename and filename not in game_state["disabled_events"]:
        game_state["disabled_events"].append(filename)
    game_state["phase"] = PHASE_RESULT


def _handle_result_finished(payload):
    """Sent by the display when the result timer expires, or by the operator to end
    the result early. Keeps the winner disabled. A no-op outside the result phase so a
    late retry from a lagging display cannot abort the next spin."""
    if game_state["phase"] != PHASE_RESULT:
        return None
    _enter_wheel_phase()


def _handle_set_disabled_events(payload):
    events = payload.get("events", [])
    if not isinstance(events, list) or not all(isinstance(e, str) for e in events):
        return "Ungültige Ereignisliste"
    game_state["disabled_events"] = list(events)


def _handle_toggle_events(payload):
    game_state["show_events"] = not game_state["show_events"]


def _handle_set_timer_size(payload):
    size = _safe_float(payload.get("size"), 3.0)
    game_state["config"]["global_timer_size"] = max(1.0, min(12.0, size))


def _handle_control_global_timer(payload):
    state = payload.get("state")
    cfg = game_state["config"]
    if state == "start" and not cfg["global_timer_running"]:
        if cfg["global_time_remaining"] <= 0:
            return "Spielzeit abgelaufen — bitte neue Spielzeit setzen"
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
        return "Kein aktives Spiel"
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
    "spin_finished":        _handle_spin_finished,
    "result_finished":      _handle_result_finished,
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
        return f"Mindestens {n} Spieler nötig ({len(players)} registriert)"

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
    if not pid:
        return "Kein Spieler gewählt"
    if team_state["phase"] != "registration":
        return "Spieler können nur vor der Teameinteilung entfernt werden"
    team_state["players"] = [p for p in team_state["players"] if p["id"] != pid]


def _handle_update_settings(payload):
    if "num_teams" in payload:
        team_state["settings"]["num_teams"] = max(2, min(20, _safe_int(payload["num_teams"], 4)))
    if "num_games" in payload:
        team_state["settings"]["num_games"] = max(1, min(3, _safe_int(payload["num_games"], 1)))


def _handle_update_match_score(payload):
    idx = _safe_int(payload.get("game_index"), -1)
    if not 0 <= idx < len(team_state["schedule"]):
        return "Ungültiges Spiel"
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


# Team actions that replace the schedule: a loaded match's game_index would then
# point at a different pairing, so the active match is cleared.
_SCHEDULE_REPLACING_ACTIONS = {"create_teams", "reset_teams", "reset_all"}


def _error(message):
    return jsonify({"status": "error", "message": message}), 400


def _parse_command(handlers):
    """Returns (action, handler, payload, None), or an error response as the last item."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, None, None, _error("Invalid JSON")
    action = data.get("action")
    if not action:
        return None, None, None, _error("Missing action")
    handler = handlers.get(action)
    if handler is None:
        return None, None, None, _error(f"Unknown action: {action}")
    payload = data.get("payload")
    return action, handler, payload if isinstance(payload, dict) else {}, None


def _commit_game_command(action):
    """Records a successful game command; caller holds _state_lock. Returns the snapshot."""
    game_state["command_id"] = _next_command_id()
    game_state["command"] = action
    return _state_snapshot()


_wheel_data_lock = threading.Lock()


def _validate_event_text(text):
    if not text:
        return "Bitte einen Text eingeben"
    if len(text) > MAX_EVENT_TEXT_LENGTH:
        return f"Text zu lang (max. {MAX_EVENT_TEXT_LENGTH} Zeichen)"
    return None


def _detect_image_extension(data):
    for signature, ext in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return ext
    return None


def _unique_image_filename(stem, ext):
    """ASCII-safe filename that does not collide with an existing image
    (compared case-insensitively, as on Windows)."""
    stem = secure_filename(stem) or "ereignis"
    existing = {f.lower() for f in os.listdir(IMAGE_FOLDER)}
    candidate = stem + ext
    suffix = 2
    while candidate.lower() in existing:
        candidate = f"{stem}-{suffix}{ext}"
        suffix += 1
    return candidate


def _broadcast_wheel_change():
    """Pushes a snapshot with the new wheel_signature so every screen reloads its events."""
    with _state_lock:
        snapshot = _commit_game_command("update_events")
    _game_sse.broadcast(snapshot)


def _clear_active_match():
    with _state_lock:
        if game_state["active_match"] is None:
            return
        game_state["active_match"] = None
        snapshot = _commit_game_command("clear_active_match")
    _game_sse.broadcast(snapshot)


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
    action, handler, payload, error_response = _parse_command(_ACTION_HANDLERS)
    if error_response:
        return error_response

    with _state_lock:
        _settle_global_timer()  # so e.g. START sees an expired clock as stopped
        error = handler(payload)
        if error:
            return _error(error)
        snapshot = _commit_game_command(action)

    _game_sse.broadcast(snapshot)
    return jsonify({"status": "success", "state": snapshot})


@app.route("/api/events", methods=["POST"])
def add_event():
    """Adds a wheel event from the control panel: multipart `image` + `text`."""
    text = (request.form.get("text") or "").strip()
    error = _validate_event_text(text)
    if error:
        return _error(error)
    upload = request.files.get("image")
    if upload is None:
        return _error("Bitte ein Bild auswählen")
    data = upload.read()
    ext = _detect_image_extension(data)
    if ext is None:
        return _error("Nur PNG-, JPG- oder GIF-Bilder")

    with _wheel_data_lock:
        if len(get_images()) >= MAX_WHEEL_EVENTS:
            return _error(f"Maximal {MAX_WHEEL_EVENTS} Ereignisse auf dem Rad")
        filename = _unique_image_filename(os.path.splitext(upload.filename or "")[0], ext)
        with open(os.path.join(IMAGE_FOLDER, filename), "wb") as f:
            f.write(data)
        texts = _read_wheel_texts()
        texts[filename] = text
        _write_json_atomic(DATA_FILE, texts, indent=4)

    _broadcast_wheel_change()
    return jsonify({"status": "success", "filename": filename})


@app.route("/api/events/<filename>", methods=["PUT"])
def update_event_text(filename):
    data = request.get_json(silent=True)
    text = (data.get("text") or "").strip() if isinstance(data, dict) else ""
    error = _validate_event_text(text)
    if error:
        return _error(error)

    with _wheel_data_lock:
        if filename not in {img["filename"] for img in get_images()}:
            return jsonify({"status": "error", "message": "Ereignis nicht gefunden"}), 404
        texts = _read_wheel_texts()
        texts[filename] = text
        _write_json_atomic(DATA_FILE, texts, indent=4)

    _broadcast_wheel_change()
    return jsonify({"status": "success"})


@app.errorhandler(413)
def upload_too_large(_error_obj):
    return _error(f"Bild zu groß (max. {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")


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
    action, handler, payload, error_response = _parse_command(_TEAM_ACTION_HANDLERS)
    if error_response:
        return error_response

    with _team_lock:
        error = handler(payload)
        if error:
            return _error(error)
        _save_team_state()
        snapshot = _team_state_snapshot()

    _team_sse.broadcast(snapshot)
    # Taken after releasing _team_lock: _handle_set_active_match locks state → team,
    # so locking team → state here could deadlock.
    if action in _SCHEDULE_REPLACING_ACTIONS:
        _clear_active_match()
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
