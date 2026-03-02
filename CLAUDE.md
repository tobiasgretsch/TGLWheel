# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TGLWheel** is a sports game wheel-of-fortune web app. A display screen shows an animated spinning wheel with challenge cards; a separate control panel lets an operator spin, reset, manage scores, and configure timers. Designed for live use (e.g., handball matches) where the display runs on one device and the operator controls from another.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server (port 5000)
python app.py

# Production (gunicorn is included in requirements.txt)
gunicorn app:app
```

No tests exist in this project.

## Architecture

### Two-Screen Design

- **`/` (index.html + script.js)** — The public display screen. Shows the wheel canvas, scoreboard overlays, global timer, and the result animation/timer after a spin.
- **`/control` (control.html)** — The operator control panel. Sends commands via fetch POST calls. Has no persistent state of its own.

### State Synchronization (Polling)

The display screen (`script.js`) polls `/api/check_status` every 500ms. The server holds a `game_state` dict in memory (`app.py`). The control panel POSTs to `/api/send_command`, which updates `game_state` with a new `command_id` (timestamp). The display screen detects a new `command_id` and acts on the command.

**`game_state` structure:**
```python
{
    "command_id": <timestamp>,   # Changes on every new command
    "command": <string>,         # 'spin', 'reset', 'update_score', etc.
    "scores": {"left": 0, "right": 0},
    "config": {
        "result_duration": 60,       # Seconds the result screen stays visible
        "global_time_set": 600,      # Game timer in seconds
        "global_timer_running": False
    }
}
```

State is **in-memory only** — it resets on server restart.

### Wheel Content

- Images live in `static/wheel_images/` (`.png`, `.jpg`, `.jpeg`, `.gif`)
- `wheel_data.json` maps filenames to display texts (e.g. `"TeamTor.png": "Jeder muss..."`)
- If a filename has no entry in `wheel_data.json`, the filename (without extension) is used as the label
- Adding/removing images or editing `wheel_data.json` takes effect immediately without a restart (read on each `/api/get_wheel_data` call)

### Wheel Rendering

The wheel is drawn on an HTML5 `<canvas>` using `script.js`. Sectors are colored using `colorPalette` (alternating red/white scheme). Each sector shows a circular thumbnail of its image. Spin animation uses CSS `transform: rotate()` with a 5-second transition; winner calculation happens after 5 seconds by mapping the final rotation angle to a sector index.

### Key Files

| File | Purpose |
|------|---------|
| `app.py` | Flask app, API routes, in-memory game state |
| `wheel_data.json` | Filename → display text mapping |
| `static/script.js` | All display logic: wheel draw, polling, spin, timers, scores |
| `static/style.css` | Display screen styling |
| `templates/index.html` | Display screen HTML structure |
| `templates/control.html` | Control panel (self-contained HTML+JS, no external JS file) |
| `static/wheel_images/` | Wheel segment images |
| `static/tg_logo.png` | Logo displayed at wheel center hub |

## Constraints

- No `sudo` commands without explicit permission
- No secrets or `.env` files in logs or output
