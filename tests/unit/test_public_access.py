"""Requests from the internet (Cloudflare Tunnel) are guests only.

Nginx's tunnel server marks them with X-Kidnyce-Public: 1. Host controls must
refuse them even with the right password, and even with no password set.
"""

from types import SimpleNamespace

from flask import Flask, session

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib import auth as auth_module
from pikaraoke.lib.admin_auth import AdminAuth
from pikaraoke.lib.current_app import is_admin
from pikaraoke.lib.guest_gate import is_logged_in_host, is_public_request
from pikaraoke.lib.login_throttle import LoginThrottle
from pikaraoke.lib.preference_manager import PreferenceManager

PUBLIC = {"X-Kidnyce-Public": "1", "X-Real-IP": "203.0.113.9"}
VENUE = {"X-Real-IP": "192.168.6.50"}


def make_app(tmp_path, password=None):
    app = Flask(__name__)
    app.secret_key = "test"
    admin_auth = AdminAuth(PreferenceManager(str(tmp_path / "config.ini")))
    if password:
        admin_auth.set_password(password)
    app.config["ADMIN_AUTH"] = admin_auth
    app.config["LOGIN_THROTTLE"] = LoginThrottle()
    return app


class TestPublicRequest:
    def test_header_marks_internet_requests(self, tmp_path):
        app = make_app(tmp_path)
        with app.test_request_context(headers=PUBLIC):
            assert is_public_request()
        with app.test_request_context(headers=VENUE):
            assert not is_public_request()
        with app.test_request_context(headers={"X-Kidnyce-Public": "yes"}):
            assert not is_public_request()

    def test_no_password_still_no_admin_from_internet(self, tmp_path):
        app = make_app(tmp_path)
        with app.test_request_context(headers=VENUE):
            assert is_admin()
        with app.test_request_context(headers=PUBLIC):
            assert not is_admin()

    def test_admin_session_cookie_is_ignored_from_internet(self, tmp_path):
        app = make_app(tmp_path, password="correct horse")
        token = app.config["ADMIN_AUTH"].session_token
        with app.test_request_context(headers=PUBLIC):
            session["admin"] = token
            assert not is_admin()
            assert not is_logged_in_host()
        with app.test_request_context(headers=VENUE):
            session["admin"] = token
            assert is_admin()
            assert is_logged_in_host()

    def test_login_refused_from_internet_even_with_the_right_password(self, tmp_path):
        app = make_app(tmp_path, password="correct horse")
        with app.test_request_context(headers=PUBLIC):
            outcome = auth_module.log_in("correct horse")
            assert outcome.refused and not outcome.granted
            assert "admin" not in session
        with app.test_request_context(headers=VENUE):
            assert auth_module.log_in("correct horse").granted


class TestGuestUrl:
    def karaoke(self, public_url, join_via_internet):
        prefs = SimpleNamespace(
            get_or_default=lambda key: join_via_internet if key == "join_via_internet" else None
        )
        return SimpleNamespace(
            url="https://karaoke.kidnyce.com", public_url=public_url, preferences=prefs
        )

    def test_venue_address_by_default(self):
        k = self.karaoke("https://karaoke-test.kidnyce.com", False)
        assert Karaoke.guest_url(k) == "https://karaoke.kidnyce.com"

    def test_internet_address_when_chosen(self):
        k = self.karaoke("https://karaoke-test.kidnyce.com", True)
        assert Karaoke.guest_url(k) == "https://karaoke-test.kidnyce.com"

    def test_choice_ignored_without_a_tunnel(self):
        k = self.karaoke(None, True)
        assert Karaoke.guest_url(k) == "https://karaoke.kidnyce.com"
