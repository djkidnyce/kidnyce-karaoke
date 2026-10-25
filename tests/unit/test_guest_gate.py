"""The join code gate, end to end through Flask: who gets in, who gets sent to /join."""

from types import SimpleNamespace

import pytest

from pikaraoke.lib import guest_gate
from pikaraoke.lib.auth import public
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.guest_gate import install_guest_gate
from pikaraoke.lib.join_codes import JoinCodeManager
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.routes.display import display_bp
from pikaraoke.routes.join import join_bp
from tests.conftest import make_route_app

PROXIED = {"X-Real-IP": "192.168.1.50", "X-Forwarded-For": "192.168.1.50"}
LINKED = [("/logo", "images.logo"), ("/auth", "admin.auth"), ("/api/now_playing", "np")]


@pytest.fixture(autouse=True)
def fresh_limiter():
    guest_gate.join_attempts._failures.clear()
    yield
    guest_gate.join_attempts._failures.clear()


@pytest.fixture
def karaoke(tmp_path):
    db = KaraokeDatabase(str(tmp_path / "gate.db"))
    stats = SimpleNamespace(number_one=lambda *a: None)
    k = SimpleNamespace(
        join_codes=JoinCodeManager(db, EventSystem(), lambda: None),
        url="https://karaoke.djkidnyce.com",
        guest_url=lambda: "https://karaoke.djkidnyce.com",
        preferences=SimpleNamespace(get_or_default=lambda key: None),
        night_plan=lambda: {"show_guests": False, "time_left": None},
        qr_version=1,
        queue_manager=SimpleNamespace(queue=[{"title": "Song", "user": "Ana", "file": "x"}]),
        request_stats=stats,
        current_session_uuid=lambda: None,
    )
    yield k
    db.close()


def build_app(karaoke, require=True):
    app = make_route_app(join_bp, LINKED, admin=False)
    app.register_blueprint(display_bp)
    app.add_url_rule("/api/secret", "secret_api", public(lambda: {"ok": True}))
    app.add_url_rule("/songs", "songs_page", public(lambda: "songs"))
    app.config["KARAOKE_INSTANCE"] = karaoke
    app.config["SITE_NAME"] = "Test"
    app.config["REQUIRE_JOIN_CODE"] = require
    # The gate must run before the auth gate the helper already installed.
    app.before_request_funcs[None].reverse()
    install_guest_gate(app)
    app.before_request_funcs[None].reverse()
    return app


@pytest.fixture
def app(karaoke):
    return build_app(karaoke)


def test_a_phone_without_the_code_is_sent_to_join(client):
    response = client.get("/songs", headers=PROXIED)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/join")


def test_an_api_call_without_the_code_gets_401_json(client):
    response = client.get("/api/secret", headers=PROXIED)
    assert response.status_code == 401
    assert response.get_json()["error"] == "Join code required"


def test_the_pi_screens_on_loopback_are_let_through(client):
    assert client.get("/songs").status_code == 200


def test_loopback_through_nginx_is_not_local(client):
    """Every proxied request comes from 127.0.0.1, so the header decides."""
    response = client.get("/songs", headers={"X-Real-IP": "127.0.0.1"})
    assert response.status_code == 302


def test_scanning_the_qr_code_joins_in_one_tap(client, karaoke):
    response = client.get(f"/join/{karaoke.join_codes.code}", headers=PROXIED)
    assert response.status_code == 302
    assert client.get("/songs", headers=PROXIED).status_code == 200


def test_typing_the_code_joins(client, karaoke):
    code = karaoke.join_codes.code.lower()
    response = client.post("/join", data={"code": f"{code[:3]} {code[3:]}"}, headers=PROXIED)
    assert response.status_code == 302
    assert client.get("/api/secret", headers=PROXIED).status_code == 200


def test_the_json_join_endpoint(client, karaoke):
    response = client.post("/api/join", json={"code": karaoke.join_codes.code}, headers=PROXIED)
    assert response.status_code == 200
    assert response.get_json() == {"joined": True}


def test_a_wrong_code_is_refused(client):
    response = client.get("/join/ZZZZZZ", headers=PROXIED)
    assert response.status_code == 403
    assert client.get("/songs", headers=PROXIED).status_code == 302


def test_guessing_is_rate_limited(client, karaoke):
    for _ in range(guest_gate.join_attempts.max_failures):
        client.post("/api/join", json={"code": "ZZZZZZ"}, headers=PROXIED)
    # Even the right code is refused while the client is blocked.
    response = client.post("/api/join", json={"code": karaoke.join_codes.code}, headers=PROXIED)
    assert response.status_code == 429


def test_rate_limit_is_per_client(client, karaoke):
    for _ in range(guest_gate.join_attempts.max_failures):
        client.post("/api/join", json={"code": "ZZZZZZ"}, headers=PROXIED)
    other = {"X-Real-IP": "192.168.1.77"}
    response = client.post("/api/join", json={"code": karaoke.join_codes.code}, headers=other)
    assert response.status_code == 200


def test_rotating_the_code_locks_out_joined_phones(client, karaoke):
    client.get(f"/join/{karaoke.join_codes.code}", headers=PROXIED)
    karaoke.join_codes.rotate()
    assert client.get("/songs", headers=PROXIED).status_code == 302


def test_the_logged_in_host_needs_no_code(client):
    with client.session_transaction() as session:
        session["admin"] = "not-the-session-cookie"
    assert client.get("/songs", headers=PROXIED).status_code == 200


def test_join_page_and_health_are_open(client):
    assert client.get("/join", headers=PROXIED).status_code == 200
    assert client.get("/api/healthz", headers=PROXIED).get_json() == {"status": "ok"}


def test_gate_off_lets_everyone_through(karaoke):
    client = build_app(karaoke, require=False).test_client()
    assert client.get("/songs", headers=PROXIED).status_code == 200


class TestCrowdScreenEndpoints:
    def test_a_joined_guest_cannot_read_the_display_state(self, client, karaoke):
        client.get(f"/join/{karaoke.join_codes.code}", headers=PROXIED)
        assert client.get("/api/display/state", headers=PROXIED).status_code == 404
        assert client.get("/crowd", headers=PROXIED).status_code == 404

    def test_the_pi_reads_the_display_state(self, client, karaoke):
        state = client.get("/api/display/state").get_json()
        assert state["join"]["code"] == karaoke.join_codes.code
        assert state["join"]["join_url"].endswith(karaoke.join_codes.code)
        assert state["queue"] == [{"title": "Song", "user": "Ana"}]
