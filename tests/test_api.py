"""API-level tests for the wheel: caching, command ids, the phase machine and the
SSE queue. Run with `py -3 -m pytest` from the project root."""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as wheel  # noqa: E402


@pytest.fixture
def client():
    wheel.app.config["TESTING"] = True
    with wheel.app.test_client() as c:
        c.post("/api/send_command", json={"action": "reset", "payload": {}})
        yield c


def send(client, action, payload=None):
    r = client.post("/api/send_command", json={"action": action, "payload": payload or {}})
    return r.status_code, r.get_json()


def status(client):
    return client.get("/api/check_status").get_json()


def active_filenames():
    return [img["filename"] for img in wheel.get_images()]


# ---------------------------------------------------------------- static assets
def test_static_files_are_cached_for_a_day(client):
    r = client.get("/static/tg_logo.png")
    assert f"max-age={wheel.STATIC_MAX_AGE_SECONDS}" in r.headers["Cache-Control"]


def test_code_assets_carry_a_version_query(client):
    assert f"script.js?v={wheel.STATIC_VERSION}" in client.get("/").get_data(as_text=True)
    assert f"tokens.css?v={wheel.STATIC_VERSION}" in client.get("/control").get_data(as_text=True)


# ---------------------------------------------------------------- commands
def test_command_ids_are_seeded_from_the_clock(client):
    _, body = send(client, "reset_scores")
    assert body["state"]["command_id"] > 1_700_000_000


def test_unknown_action_is_rejected(client):
    code, body = send(client, "does_not_exist")
    assert code == 400 and "Unknown action" in body["message"]


def test_failed_command_does_not_bump_the_id_or_broadcast(client):
    before = status(client)["command_id"]
    code, body = send(client, "confirm_match_score")
    assert code == 400 and body["message"] == "Kein aktives Spiel"
    assert status(client)["command_id"] == before


def test_spin_with_no_active_events_is_refused(client):
    send(client, "set_disabled_events", {"events": active_filenames()})
    code, body = send(client, "spin")
    assert code == 400 and "Keine aktiven" in body["message"]
    assert status(client)["phase"] == wheel.PHASE_WHEEL


# ---------------------------------------------------------------- phase machine
def test_full_spin_cycle(client):
    code, body = send(client, "spin")
    st = body["state"]
    assert code == 200 and st["phase"] == wheel.PHASE_SPINNING
    winner = st["winner_filename"]
    assert active_filenames()[st["winner_index"]] == winner

    code, body = send(client, "spin")
    assert code == 400 and "dreht" in body["message"]

    _, body = send(client, "spin_finished")
    st = body["state"]
    assert st["phase"] == wheel.PHASE_RESULT and st["disabled_events"] == [winner]

    code, body = send(client, "spin")
    assert code == 400 and "Ergebnis" in body["message"]

    _, body = send(client, "result_finished")
    st = body["state"]
    assert st["phase"] == wheel.PHASE_WHEEL
    assert st["disabled_events"] == [winner]
    assert st["winner_index"] is None and st["winner_filename"] is None


def test_display_callbacks_are_idempotent(client):
    send(client, "spin")
    send(client, "spin_finished")
    _, body = send(client, "spin_finished")
    assert len(body["state"]["disabled_events"]) == 1
    send(client, "result_finished")
    _, body = send(client, "result_finished")
    assert body["state"]["phase"] == wheel.PHASE_WHEEL


def test_winner_index_refers_to_the_active_list(client):
    all_files = active_filenames()
    for _ in range(len(all_files) - 1):
        _, body = send(client, "spin")
        st = body["state"]
        remaining = [f for f in all_files if f not in st["disabled_events"]]
        assert remaining[st["winner_index"]] == st["winner_filename"]
        send(client, "spin_finished")
        send(client, "result_finished")


def test_reset_mid_spin_returns_to_wheel_with_everything_enabled(client):
    send(client, "spin")
    send(client, "spin_finished")
    _, body = send(client, "reset")
    st = body["state"]
    assert st["phase"] == wheel.PHASE_WHEEL
    assert st["disabled_events"] == [] and st["winner_filename"] is None


def test_stale_spin_is_replaced(client, monkeypatch):
    send(client, "spin")
    code, _ = send(client, "spin")
    assert code == 400

    started = wheel.game_state["spin_started_at"]
    monkeypatch.setattr(wheel.time, "time", lambda: started + wheel.SPIN_STALE_SECONDS + 1)
    code, body = send(client, "spin")
    assert code == 200 and body["state"]["phase"] == wheel.PHASE_SPINNING


def test_result_phase_never_goes_stale(client, monkeypatch):
    send(client, "spin")
    send(client, "spin_finished")
    started = wheel.game_state["spin_started_at"]
    monkeypatch.setattr(wheel.time, "time", lambda: started + 3600)
    code, _ = send(client, "spin")
    assert code == 400


# ---------------------------------------------------------------- SSE queue
def test_slow_subscriber_keeps_newest_messages_and_is_not_evicted():
    broadcaster = wheel.SSEBroadcaster()
    q = broadcaster.subscribe()
    for i in range(25):
        broadcaster.broadcast({"n": i})
    assert q in broadcaster._subscribers
    received = [json.loads(q.get_nowait()[len("data: "):])["n"] for _ in range(q.qsize())]
    assert received == list(range(15, 25))


def test_stream_first_message_is_a_full_snapshot(client):
    r = client.get("/api/stream", buffered=False)
    first = next(r.response)
    assert first.startswith(b"data: ")
    snapshot = json.loads(first[len(b"data: "):])
    for key in ("phase", "winner_index", "winner_filename", "scores", "config"):
        assert key in snapshot
    r.close()


# ---------------------------------------------------------------- robustness
def test_late_result_finished_cannot_abort_the_next_spin(client):
    send(client, "spin")
    send(client, "spin_finished")
    send(client, "result_finished")
    send(client, "spin")
    _, body = send(client, "result_finished")   # lagging retry from a second display
    assert body["state"]["phase"] == wheel.PHASE_SPINNING


def test_operator_can_end_the_result_early(client):
    send(client, "spin")
    _, body = send(client, "spin_finished")
    winner = body["state"]["winner_filename"]
    _, body = send(client, "result_finished")
    assert body["state"]["phase"] == wheel.PHASE_WHEEL
    assert body["state"]["disabled_events"] == [winner]


def test_spin_closes_the_events_popup(client):
    send(client, "toggle_events")
    _, body = send(client, "spin")
    assert body["state"]["show_events"] is False


def test_snapshot_lists_the_wheel_files(client):
    assert status(client)["wheel_files"] == active_filenames()


@pytest.mark.parametrize("payload", [{"side": "left", "change": "x"}, {"side": "middle", "change": 1}])
def test_bad_score_payload_never_crashes(client, payload):
    code, _ = send(client, "update_score", payload)
    assert code in (200, 400)
    assert status(client)["scores"] == {"left": 0, "right": 0}


def test_null_payload_is_treated_as_empty(client):
    r = client.post("/api/send_command", json={"action": "toggle_events", "payload": None})
    assert r.status_code == 200


def test_non_list_disabled_events_is_refused(client):
    code, _ = send(client, "set_disabled_events", {"events": "TeamTor.png"})
    assert code == 400


# ---------------------------------------------------------------- game clock
def test_expired_clock_is_reported_as_stopped(client, monkeypatch):
    send(client, "set_timers", {"global_time": 60})
    send(client, "control_global_timer", {"state": "start"})
    started = wheel.game_state["config"]["global_timer_start"]
    monkeypatch.setattr(wheel.time, "time", lambda: started + 61)
    cfg = status(client)["config"]
    assert cfg["global_timer_running"] is False and cfg["global_time_remaining"] == 0

    code, body = send(client, "control_global_timer", {"state": "start"})
    assert code == 400 and "abgelaufen" in body["message"]


# ---------------------------------------------------------------- teams
@pytest.fixture
def teams(client, tmp_path, monkeypatch):
    monkeypatch.setattr(wheel, "TEAM_DATA_FILE", str(tmp_path / "team_data.json"))
    monkeypatch.setattr(wheel, "team_state", wheel._default_team_state())
    send(client, "set_active_match", {"game_index": -1})
    return client


def team_send(client, action, payload=None):
    r = client.post("/api/team_command", json={"action": action, "payload": payload or {}})
    return r.status_code, r.get_json()


def register(client, *names):
    for name in names:
        client.post("/api/register_player", json={"name": name})


def test_create_teams_with_too_few_players_is_refused(teams):
    register(teams, "A", "B")
    code, body = team_send(teams, "create_teams")
    assert code == 400 and "Mindestens" in body["message"]
    assert wheel.team_state["phase"] == "registration"


def test_reshuffling_clears_the_active_match(teams):
    register(teams, "A", "B", "C", "D")
    team_send(teams, "update_settings", {"num_teams": 2})
    team_send(teams, "create_teams")
    send(teams, "set_active_match", {"game_index": 0})
    assert status(teams)["active_match"] is not None

    team_send(teams, "create_teams")
    assert status(teams)["active_match"] is None


def test_remove_player_after_team_creation_is_refused(teams):
    register(teams, "A", "B")
    team_send(teams, "update_settings", {"num_teams": 2})
    team_send(teams, "create_teams")
    code, _ = team_send(teams, "remove_player", {"id": wheel.team_state["players"][0]["id"]})
    assert code == 400


# ---------------------------------------------------------------- wheel events
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def wheel_dir(client, tmp_path, monkeypatch):
    """Points the wheel at an empty temp folder so tests never touch the real images."""
    images = tmp_path / "wheel_images"
    images.mkdir()
    monkeypatch.setattr(wheel, "IMAGE_FOLDER", str(images))
    monkeypatch.setattr(wheel, "DATA_FILE", str(tmp_path / "wheel_data.json"))
    return images


def add_event(client, text="Neues Ereignis", data=PNG_BYTES, name="Tor.png"):
    r = client.post("/api/events", data={"text": text, "image": (io.BytesIO(data), name)},
                    content_type="multipart/form-data")
    return r.status_code, r.get_json()


def test_added_event_is_saved_and_broadcast(wheel_dir, client):
    before = status(client)
    code, body = add_event(client, text="Nur mit links werfen", name="Linke Hand.png")
    assert code == 200 and body["filename"] == "Linke_Hand.png"
    assert (wheel_dir / "Linke_Hand.png").read_bytes() == PNG_BYTES
    assert json.loads((wheel_dir.parent / "wheel_data.json").read_text(encoding="utf-8")) == \
        {"Linke_Hand.png": "Nur mit links werfen"}
    after = status(client)
    assert after["wheel_files"] == ["Linke_Hand.png"]
    assert after["command_id"] > before["command_id"]
    assert after["wheel_signature"] != before["wheel_signature"]


def test_type_comes_from_content_not_name(wheel_dir, client):
    code, body = add_event(client, name="foto.gif", data=b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    assert code == 200 and body["filename"] == "foto.jpg"


def test_non_image_upload_is_refused(wheel_dir, client):
    code, body = add_event(client, data=b"<script>alert(1)</script>", name="x.png")
    assert code == 400 and "PNG" in body["message"]
    assert list(wheel_dir.iterdir()) == []


def test_duplicate_names_get_a_suffix(wheel_dir, client):
    add_event(client, name="Tor.png")
    _, body = add_event(client, name="tor.png")
    assert body["filename"] == "tor-2.png"


@pytest.mark.parametrize("text", ["", "   ", "x" * 201])
def test_event_text_is_validated(wheel_dir, client, text):
    code, _ = add_event(client, text=text)
    assert code == 400


def test_event_limit(wheel_dir, client):
    for i in range(wheel.MAX_WHEEL_EVENTS):
        assert add_event(client, name=f"e{i}.png")[0] == 200
    code, body = add_event(client, name="zuviel.png")
    assert code == 400 and "Maximal" in body["message"]


def test_event_text_can_be_edited(wheel_dir, client):
    add_event(client, text="Alt", name="Tor.png")
    signature = status(client)["wheel_signature"]
    r = client.put("/api/events/Tor.png", json={"text": "Neu"})
    assert r.status_code == 200
    assert wheel.get_images()[0]["text"] == "Neu"
    assert status(client)["wheel_signature"] != signature


def test_editing_an_unknown_event_is_refused(wheel_dir, client):
    r = client.put("/api/events/gibtsnicht.png", json={"text": "Neu"})
    assert r.status_code == 404
    assert not (wheel_dir.parent / "wheel_data.json").exists()
