# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TGLWheel** is a sports game wheel-of-fortune web app. A display screen shows an animated spinning wheel with challenge cards; a separate control panel lets an operator spin, reset, manage scores, and configure timers. Designed for live use (e.g., handball matches) where the display runs on one device and the operator controls from another.

## Commands

```bash
# Install dependencies (use py -3 on Windows if python/pip not in PATH)
py -3 -m pip install -r requirements.txt

# Run development server (port 5000)
py -3 app.py

# Production (gunicorn — config is picked up from gunicorn.conf.py automatically)
gunicorn app:app
```

No tests exist in this project.

## Hosting

The app is hosted on **Render** (onrender.com). Key deployment facts:

- Render runs `gunicorn app:app` from its dashboard start command — the `Procfile` is ignored
- `gunicorn.conf.py` in the project root is auto-loaded by gunicorn and enforces `workers=1, threads=4, worker_class=gthread`
- **CRITICAL**: Must run with a single worker process. `game_state` and the SSE `_subscribers` list are in-memory. Multiple workers each get a private copy, so commands POSTed to one worker are never seen by the SSE stream on another worker. This was the root cause of broken two-screen communication on Render.
- State is **in-memory only** — resets on every deploy/restart.

## Architecture

### Two-Screen Design

- **`/` (index.html + script.js)** — The public display screen. Shows the wheel canvas, scoreboard overlays, global timer, and the result animation/timer after a spin.
- **`/control` (control.html)** — The operator control panel. Sends commands via fetch POST calls. Has no persistent state of its own.

### State Synchronization (SSE)

The display screen connects to `/api/stream` (Server-Sent Events). The server pushes a full state snapshot on every command. The control panel POSTs to `/api/send_command`, which updates `game_state`, increments `command_id`, and notifies all SSE subscribers immediately.

The first SSE message on connect carries the full current state, which initialises `lastCommandId` on the client and prevents stale commands from replaying after a page refresh.

**`game_state` structure:**
```python
{
    "command_id": <int>,          # Increments on every new command
    "command": <string>,          # 'spin', 'reset', 'update_score', etc.
    "scores": {"left": 0, "right": 0},
    "show_events": False,         # Whether the events popup is visible on display
    "config": {
        "result_duration": 60,        # Seconds the result screen stays visible
        "global_time_remaining": 600, # Game timer in seconds (decremented server-side)
        "global_timer_running": False,
        "global_timer_start": None,   # time.time() when timer was last started
        "global_timer_size": 3.0,     # rem units for global timer font on display
        "score_size": 5.0,            # rem units for score digits on display
    }
}
```

**API actions** (`/api/send_command` POST, `action` field):
| Action | Payload | Effect |
|--------|---------|--------|
| `spin` | — | Triggers spin sequence on display |
| `reset` | — | Resets display to wheel view |
| `update_score` | `{side, change}` | Adjusts left/right score |
| `reset_scores` | — | Sets both scores to 0 |
| `set_timers` | `{result_duration?, global_time?}` | Sets timer durations |
| `control_global_timer` | `{state: 'start'/'stop'}` | Starts/pauses game clock |
| `set_timer_size` | `{size}` | Sets global timer font size (rem, 1–12) |
| `set_score_size` | `{size}` | Sets score digit font size (rem, 1–20) |
| `toggle_events` | — | Flips `show_events` boolean |

### Wheel Content

- Images live in `static/wheel_images/` (`.png`, `.jpg`, `.jpeg`, `.gif`)
- `wheel_data.json` maps filenames to display texts (e.g. `"TeamTor.png": "Jeder muss..."`)
- If a filename has no entry in `wheel_data.json`, the filename (without extension) is used as the label
- Adding/removing images or editing `wheel_data.json` takes effect immediately without a restart (read on each `/api/get_wheel_data` call)
- **Maximum 9 events** — the events popup grid is designed for up to 9 items

### Wheel Rendering

The wheel is drawn on an HTML5 `<canvas>` (internal resolution 600×600, responsive via CSS). `drawWheel()` in `script.js` uses 8 layered canvas passes:

1. **Outer rim** — dark `#0d1520` circle at full radius (300px)
2. **Sector fills** — alternating `SECTOR_COLORS = ['#B03030', '#1C3455', '#FFFFFF']` up to `discR=278`
3. **Divider lines** — `rgba(255,255,255,0.15)` from centre to rim edge
4. **Circular images** — white border ring + cover-scale clip, centred at `IMG_DIST=181`
5. **Rim tick marks** — `rgba(255,255,255,0.4)` lines in the 22px rim band
6. **Rim dots** — one dot per sector midpoint in the rim band
7. **Edge rings** — `rgba(255,255,255,0.12)` arcs on inner/outer rim borders
8. **Centre backing disc** — `#1a2332` circle at radius 78px, sits under the HTML hub

Key constants: `outerR=300`, `RIM_W=22`, `discR=278`, `IMG_SIZE=82`, `IMG_DIST≈181`.

Three alternating sector colours ensure no two adjacent sectors ever share a colour (works for any number of sectors).

Spin animation uses CSS `transform: rotate()` with a 5-second transition (`--spin-duration`). `SPIN_DURATION_MS` is read from the computed CSS transition duration so it can never silently diverge. Winner is pre-selected before the spin; rotation is calculated to land exactly on that sector.

### Events Popup

The display screen has a fullscreen overlay (`#events-popup`) that shows all wheel events in a 3×3 grid with large circular images and text. It is toggled by the `toggle_events` command from the control panel. The popup fetches fresh wheel data from `/api/get_wheel_data` each time it is shown.

### Score & Timer Sizing

Both the global game timer and the score digits (including Heim/Gast labels) can be resized live from the control panel. The display screen applies `style.fontSize` inline when it receives `global_timer_size` or `score_size` in the SSE state. Score labels are scaled at 20% of the score digit size.

The `.score-board` panel uses `width: max-content` so it grows horizontally (not vertically) as font size increases.

### Team Management & Tournament

A second display flow for team-based play. Players self-register via QR code on their phones, the operator distributes them into random teams with a live animation on a projector, and a round-robin tournament schedule is generated.

**Pages:**
- **`/teams`** — Projector-ready display page. Shows QR code + live player list during registration, team creation animation, then team columns + schedule table.
- **`/register`** — Mobile registration form. Players scan the QR code and enter their name.

**SSE:** `/api/team_stream` pushes `team_state` snapshots (separate broadcaster from the wheel SSE).

**Persistence:** `team_data.json` — written atomically (`.tmp` + `os.replace`) after every team mutation. Stores players, teams, schedule, settings, and phase. Survives server restarts.

**`team_state` structure:**
```python
{
    "players": [{"id": "uuid-hex", "name": "Max", "position": "field"}],  # "field" | "goalkeeper"
    "teams": [{"name": "Team 1", "color": "#B03030", "players": ["uuid1"]}],
    "schedule": [{"game": 1, "home": "Team 1", "away": "Team 2", "score_home": None, "score_away": None}],
    "phase": "registration",  # "registration" | "teams_created"
    "settings": {"num_teams": 4, "num_games": 1}
}
```

**Team API actions** (`/api/team_command` POST, `action` field):
| Action | Payload | Effect |
|--------|---------|--------|
| `create_teams` | — | Shuffle players into N teams (goalkeepers distributed first, one per team), generate round-robin schedule |
| `reset_teams` | — | Clear teams + schedule, keep players, phase → `registration` |
| `reset_all` | — | Clear everything, phase → `registration` |
| `remove_player` | `{id}` | Remove player by UUID (registration phase only) |
| `update_settings` | `{num_teams?, num_games?}` | Update settings, persist |
| `update_match_score` | `{game_index, score_home, score_away}` | Update match result |

**Other team endpoints:**
| Route | Method | Purpose |
|-------|--------|---------|
| `/api/register_player` | POST | Register a player `{name, position?}` — position is `"field"` (default) or `"goalkeeper"` |
| `/api/team_state` | GET | Returns current `team_state` snapshot |
| `/api/team_stream` | GET | SSE stream for team state changes |
| `/api/qr_code` | GET | Returns SVG QR code pointing to `/register` |

**Control panel integration:** The control panel has a "TURNIER" panel with a button that opens a settings popup overlay. The popup loads/saves `num_teams` and `num_games` via `/api/team_command` with `update_settings`, and has a link to open `/teams` in a new tab.

### Key Files

| File | Purpose |
|------|---------|
| `app.py` | Flask app, API routes, game state, team state, SSE broadcasters |
| `gunicorn.conf.py` | Forces single worker — critical for Render deployment |
| `Procfile` | Fallback start command (Render ignores this in favour of dashboard) |
| `wheel_data.json` | Filename → display text mapping |
| `team_data.json` | Team state persistence (players, teams, schedule, settings) |
| `static/script.js` | Wheel display logic: draw, SSE handling, spin, timers, scores, events popup |
| `static/style.css` | Wheel display screen styling |
| `static/tokens.css` | Shared CSS design tokens (colors, radii, shadows) |
| `static/teams.js` | Teams display logic: SSE, player list, team animation, schedule |
| `static/teams.css` | Teams display page styling |
| `templates/index.html` | Wheel display screen HTML |
| `templates/control.html` | Control panel — self-contained HTML+JS, tournament settings popup |
| `templates/teams.html` | Teams display page — three-phase layout (registration, animation, teams) |
| `templates/register.html` | Mobile player registration form |
| `static/wheel_images/` | Wheel segment images |
| `static/tg_logo.png` | Logo displayed at wheel center hub |

## Constraints

- No `sudo` commands without explicit permission
- No secrets or `.env` files in logs or output
- On Windows, use `py -3` instead of `python` / `pip` — the system Python is in MSYS2 and has no pip; the real Python 3.13 is at `C:\Users\tobia\AppData\Local\Programs\Python\Python313\`
