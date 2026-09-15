"""API-level tests for the wheel: caching, command ids, the phase machine and the
SSE queue. Run with `py -3 -m pytest` from the project root."""
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
