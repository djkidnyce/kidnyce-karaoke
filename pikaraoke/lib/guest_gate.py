"""The join code gate: a phone must present tonight's code before it sees anything.

Three kinds of caller get through:

1. The display browsers on the Pi itself. They reach the app straight on
   loopback. Everything else arrives through Nginx, which always sets
   X-Real-IP, so a loopback caller without that header can only be local.
2. The host, once logged in with the admin password.
3. A guest whose signed session cookie holds the current join code.
"""

import ipaddress
import logging

from flask import Flask, current_app, jsonify, redirect, request, session, url_for
from flask.typing import ResponseReturnValue

from pikaraoke.lib.join_codes import AttemptLimiter

_PROXY_HEADERS = ("X-Real-IP", "X-Forwarded-For")

# Reachable without a code: the join flow, the host login, a health probe for
# the installer and monitoring, and static assets the join page itself needs.
OPEN_ENDPOINTS = frozenset(
    {
        "static",
        "join.join_page",
        "join.join_with_code",
        "join.join_form",
        "join.join_api",
        "join.healthz",
        "admin.auth",
        "admin.logout",
        "auth_api.auth_status",
        "auth_api.login",
        "images.logo",
    }
)

SESSION_KEY = "join_code"

join_attempts = AttemptLimiter()


def gate_enabled() -> bool:
    """Off unless the server was started with --require-join-code."""
    return bool(current_app.config.get("REQUIRE_JOIN_CODE", False))


def client_address() -> str:
    """Who to rate limit. X-Real-IP is trustworthy here because only Nginx can set it."""
    forwarded = request.headers.get("X-Real-IP")
    return forwarded or request.remote_addr or "unknown"


def is_local_display() -> bool:
    """True for a browser running on the Pi itself, such as the two kiosk screens."""
    if any(request.headers.get(h) for h in _PROXY_HEADERS):
        return False
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def is_logged_in_host() -> bool:
    """A real admin login. Unlike is_admin(), never true just because no password is set."""
    auth = current_app.config.get("ADMIN_AUTH")
    if auth is None or not auth.is_password_set():
        return False
    return session.get("admin") == auth.session_token


def has_joined() -> bool:
    """The guest's cookie holds the code that is current right now."""
    from pikaraoke.lib.current_app import get_karaoke_instance

    return get_karaoke_instance().join_codes.verify(session.get(SESSION_KEY))


def caller_allowed() -> bool:
    """The whole rule, shared by HTTP requests and socket connections."""
    if not gate_enabled():
        return True
    if is_local_display() or is_logged_in_host():
        return True
    return has_joined()


def grant_guest_session(code: str) -> None:
    """Remember the code on the guest's signed cookie."""
    session[SESSION_KEY] = code
    session.permanent = True


def install_guest_gate(app: Flask) -> None:
    """Refuse every request from a phone that has not joined tonight's session."""

    @app.before_request
    def require_join_code() -> ResponseReturnValue | None:
        if not gate_enabled():
            return None
        if request.endpoint is None or request.endpoint in OPEN_ENDPOINTS:
            return None
        if caller_allowed():
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "Join code required"}), 401
        logging.debug(f"Join gate redirected {client_address()} from {request.path}")
        return redirect(url_for("join.join_page"))
