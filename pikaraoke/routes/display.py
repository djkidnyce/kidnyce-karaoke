"""The crowd screen: QR code with the join code, the singing order, and the lyrics.

It runs on the Pi's second HDMI output. The singer screen is the upstream
splash page, opened with ?singer=1 so it drops the QR code and stays clean.
"""

from flask import abort, jsonify, render_template
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.guest_gate import gate_enabled, is_local_display, is_logged_in_host

display_bp = Blueprint("display", __name__)

# How much of the queue the crowd screen lists. More than this is unreadable
# from across a room at 1080p.
QUEUE_PREVIEW = 10


def _require_screen() -> None:
    """The crowd screen carries the join code, so only the Pi or the host may open it."""
    if not (is_local_display() or is_logged_in_host()):
        abort(404)


@display_bp.route("/crowd")
@public
def crowd():
    """Second screen for the room."""
    _require_screen()
    k = get_karaoke_instance()
    return render_template(
        "crowd.html",
        site_title=get_site_name(),
        blank_page=True,
        url=k.url,
    )


@display_bp.route("/api/display/state")
@public
def state():
    """Everything the crowd screen draws apart from the video itself."""
    _require_screen()
    k = get_karaoke_instance()
    session_uuid = k.current_session_uuid()
    queue = [{"title": item["title"], "user": item["user"]} for item in k.queue_manager.queue]
    return jsonify(
        {
            "join": {
                "required": gate_enabled(),
                "code": k.join_codes.code,
                "join_url": k.join_codes.join_url(k.url),
                "url": k.url,
                "qr_version": k.qr_version,
            },
            "queue": queue[:QUEUE_PREVIEW],
            "queue_length": len(queue),
            "top_requested_tonight": (
                k.request_stats.number_one(session_uuid) if session_uuid else None
            ),
            "top_requested_all_time": k.request_stats.number_one(),
        }
    )
