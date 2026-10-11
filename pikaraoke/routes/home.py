"""Home page route."""

import flask_babel
from flask import render_template
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin
from pikaraoke.lib.guest_gate import gate_enabled

_ = flask_babel.gettext


home_bp = Blueprint("home", __name__)


@home_bp.route("/")
@public
def home():
    """Home page with now playing info and controls."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    admin = is_admin()
    return render_template(
        "home.html",
        site_title=site_name,
        # MSG: Title of the home page, which shows the song playing now.
        title=_("Now Playing"),
        transpose_value=k.playback_controller.now_playing_transpose,
        admin=admin,
        is_transpose_enabled=k.is_transpose_enabled,
        volume=k.volume,
        mic_available=k.sound_manager.available,
        require_join_code=gate_enabled(),
        # Only the host's page carries the code; a guest already has it.
        join_code=k.join_codes.code if admin else None,
    )
