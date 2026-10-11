"""Joining a hosted session with its six character code, and managing that code."""

import logging

import flask_babel
from flask import jsonify, redirect, render_template, request, url_for
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.guest_gate import (
    caller_allowed,
    client_address,
    gate_enabled,
    grant_guest_session,
    join_attempts,
)
from pikaraoke.lib.join_codes import normalize_code

_ = flask_babel.gettext

join_bp = Blueprint("join", __name__)


def _attempt(code: str | None) -> tuple[bool, int]:
    """Check a code for this client. Returns (joined, http_status)."""
    client = client_address()
    if join_attempts.is_blocked(client):
        logging.warning(f"Join attempts blocked for {client}")
        return False, 429
    k = get_karaoke_instance()
    if k.join_codes.verify(code):
        join_attempts.reset(client)
        grant_guest_session(normalize_code(code))
        return True, 200
    join_attempts.record_failure(client)
    return False, 403


def _render_join(error: str | None = None, status: int = 200, code: str = ""):
    return (
        render_template(
            "join.html",
            site_title=get_site_name(),
            blank_page=True,
            error=error,
            code=code,
        ),
        status,
    )


def _error_for(status: int) -> str:
    if status == 429:
        # MSG: Shown when a phone has typed too many wrong join codes.
        return _("Too many tries. Wait a few minutes and scan the QR code again.")
    # MSG: Shown when the typed join code does not match the current session.
    return _("That code didn't work. Check the code on the screen and try again.")


@join_bp.route("/join", methods=["GET"])
@public
def join_page():
    """Ask for the code shown on the crowd screen."""
    if not gate_enabled() or caller_allowed():
        return redirect(url_for("home.home"))
    return _render_join()


@join_bp.route("/join", methods=["POST"])
@public
def join_form():
    """The typed code, from the join page."""
    code = request.form.get("code", "")
    joined, status = _attempt(code)
    if joined:
        return redirect(url_for("home.home"))
    return _render_join(_error_for(status), status, normalize_code(code))


@join_bp.route("/join/<code>")
@public
def join_with_code(code: str):
    """What the QR code opens: join in one tap."""
    joined, status = _attempt(code)
    if joined:
        return redirect(url_for("home.home"))
    return _render_join(_error_for(status), status)


@join_bp.route("/api/join", methods=["POST"])
@public
def join_api():
    """Join from a script. Body: {"code": "ABC234"}."""
    payload = request.get_json(silent=True) or {}
    joined, status = _attempt(payload.get("code"))
    if joined:
        return jsonify({"joined": True})
    return jsonify({"joined": False, "error": _error_for(status)}), status


@join_bp.route("/api/healthz")
@public
def healthz():
    """Liveness probe for the installer, Nginx and monitoring. Reveals nothing."""
    return jsonify({"status": "ok"})


@join_bp.route("/api/join_code")
def get_join_code():
    """The current code and the address the QR code carries. Host only."""
    k = get_karaoke_instance()
    return jsonify({"code": k.join_codes.code, "join_url": k.join_codes.join_url(k.url)})


@join_bp.route("/api/join_code/rotate", methods=["POST"])
def rotate_join_code():
    """Issue a new code now. Every phone has to scan again. Host only."""
    k = get_karaoke_instance()
    code = k.join_codes.rotate()
    return jsonify({"code": code, "join_url": k.join_codes.join_url(k.url)})
