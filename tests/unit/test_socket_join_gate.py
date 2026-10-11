"""Socket connections obey the join gate, and rotating the code drops guests."""

from unittest.mock import MagicMock

import pytest
from flask import Flask, session
from flask_socketio import SocketIO

from pikaraoke.routes import socket_events
from pikaraoke.routes.socket_events import kick_guests, setup_socket_events
from tests.conftest import StubAdminAuth

PROXIED = {"X-Real-IP": "192.168.1.50"}


@pytest.fixture(autouse=True)
def reset_state():
    yield
    socket_events.splash_connections.clear()
    socket_events.crowd_connections.clear()
    socket_events.guest_sids.clear()
    socket_events.master_splash_id = None


def local_screen(app, socketio):
    """A browser on the Pi. Flask's test client supplies REMOTE_ADDR 127.0.0.1,
    as the real server does; a bare socket test client supplies none."""
    return socketio.test_client(app, flask_test_client=app.test_client())


def make_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["ADMIN_AUTH"] = StubAdminAuth(admin=False)
    app.config["REQUIRE_JOIN_CODE"] = True
    karaoke = MagicMock()
    karaoke.join_codes.verify.side_effect = lambda code: code == "ABC234"
    app.config["KARAOKE_INSTANCE"] = karaoke

    @app.route("/fake_join")
    def fake_join():
        session["join_code"] = "ABC234"
        return "ok"

    socketio = SocketIO(app, async_mode="threading")
    setup_socket_events(socketio)
    return app, socketio


def test_a_phone_without_the_code_cannot_open_a_socket():
    app, socketio = make_app()
    client = socketio.test_client(app, headers=PROXIED)
    assert not client.is_connected()


def test_a_joined_phone_connects_and_is_tracked_as_a_guest():
    app, socketio = make_app()
    flask_client = app.test_client()
    flask_client.get("/fake_join")
    client = socketio.test_client(app, headers=PROXIED, flask_test_client=flask_client)
    assert client.is_connected()
    assert len(socket_events.guest_sids) == 1


def test_the_pi_screens_connect_without_a_code():
    app, socketio = make_app()
    client = local_screen(app, socketio)
    assert client.is_connected()
    assert socket_events.guest_sids == set()


def test_rotation_drops_guests_and_tells_only_the_screens():
    app, socketio = make_app()
    flask_client = app.test_client()
    flask_client.get("/fake_join")
    guest = socketio.test_client(app, headers=PROXIED, flask_test_client=flask_client)
    screen = local_screen(app, socketio)

    kick_guests(socketio)

    assert not guest.is_connected()
    assert screen.is_connected()
    assert any(m["name"] == "join_code_changed" for m in screen.get_received())


def test_a_crowd_screen_is_never_elected_master():
    app, socketio = make_app()
    crowd = local_screen(app, socketio)
    crowd.emit("register_splash", "crowd")
    roles = [m["args"][0] for m in crowd.get_received() if m["name"] == "splash_role"]
    assert roles == ["slave"]
    assert socket_events.master_splash_id is None

    singer = local_screen(app, socketio)
    singer.emit("register_splash")
    roles = [m["args"][0] for m in singer.get_received() if m["name"] == "splash_role"]
    assert roles == ["master"]
