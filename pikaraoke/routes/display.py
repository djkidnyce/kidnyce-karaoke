"""The screens: the crowd page, what it draws, and which page goes on which port.

With two screens the singer screen is the upstream splash page opened with
?singer=1 (no QR code) and the crowd screen is /crowd. With one screen the
splash page opens with ?all=1 and carries the QR code and the singing order too.
"""

from flask import abort, jsonify, render_template, request
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.display_hardware import (
    Layout,
    Port,
    lan_ip,
    list_ports,
    list_sound_cards,
    parse_outputs,
    plan_layout,
)
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
        url=k.guest_url(),
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
                "join_url": k.join_codes.join_url(k.guest_url()),
                "url": k.guest_url(),
                "qr_version": k.qr_version,
            },
            # Time left tonight, only when the host shows it to guests.
            "night": _night_for_room(k),
            # Small print on the screens, for the host on the venue network.
            "host": {
                "show": k.preferences.get_or_default("show_host_address") is True,
                "ip": lan_ip(),
                "url": k.url,
            },
            "queue": queue[:QUEUE_PREVIEW],
            "queue_length": len(queue),
            "top_requested_tonight": (
                k.request_stats.number_one(session_uuid) if session_uuid else None
            ),
            "top_requested_all_time": k.request_stats.number_one(),
        }
    )


def _night_for_room(k) -> dict | None:
    plan = k.night_plan()
    if not plan["show_guests"] or plan["time_left"] is None:
        return None
    return {
        "time_left": plan["time_left"],
        "late_count": plan["late_count"],
        "ends_at": plan["ends_at"],
    }


def _plan(connected: list[str]) -> Layout:
    k = get_karaoke_instance()
    prefs = k.preferences
    return plan_layout(
        prefs.get_or_default("screen_mode"),
        connected,
        single_output=prefs.get_or_default("single_screen_output"),
        swap=prefs.get_or_default("swap_screens"),
        low_memory=k.board.low_memory,
    )


@display_bp.route("/api/display/layout")
@public
def layout():
    """Which page each port shows. The kiosk session asks every few seconds.

    It passes the ports X sees connected in ?connected=HDMI-1,HDMI-2. Only the
    Pi itself may report, so a phone cannot move the screens around.
    """
    if not is_local_display():
        abort(404)
    k = get_karaoke_instance()
    connected = parse_outputs(request.args.get("connected"))
    k.kiosk_report.update(connected)
    desktop = {"autostart": k.preferences.get_or_default("desktop_screens_on_login") is True}
    return jsonify({**_plan(connected).as_payload(), "audio": _audio(k), "desktop": desktop})


def _audio(k) -> dict:
    """The host's sound choice. The kiosk session checks and applies it."""
    return {
        "output": k.preferences.get_or_default("audio_output"),
        "level": k.preferences.get_or_default("audio_level"),
    }


@display_bp.route("/api/display/hardware")
def hardware():
    """The board, its ports, the TVs on them and the layout in use. Host only."""
    k = get_karaoke_instance()
    ports = list_ports(k.board)
    reported = k.kiosk_report.outputs()
    # X can see ports sysfs does not list, such as a virtual screen.
    known = {p.output for p in ports}
    ports += [Port(o, o, True, None, None) for o in reported or [] if o not in known]
    if reported is not None:
        connected = [p.output for p in ports if p.output in reported]
    else:
        connected = [p.output for p in ports if p.connected]
    plan = _plan(connected)
    roles = {s.output: s.role for s in plan.screens}
    prefs = k.preferences
    seconds_ago = k.kiosk_report.seconds_ago()
    return jsonify(
        {
            "board": {
                "model": k.board.model,
                "is_raspberry_pi": k.board.is_raspberry_pi,
                "memory_gb": round(k.board.memory_kb / 1048576, 1),
                "low_memory": k.board.low_memory,
            },
            "ports": [
                {
                    "output": p.output,
                    "label": p.label,
                    "connected": p.output in connected,
                    "monitor": p.monitor,
                    "preferred_mode": p.preferred_mode,
                    "role": roles.get(p.output),
                }
                for p in ports
            ],
            "dual_capable": len(ports) >= 2,
            "screens_running": reported is not None,
            "seconds_since_report": None if seconds_ago is None else round(seconds_ago),
            "settings": {
                "screen_mode": prefs.get_or_default("screen_mode"),
                "single_screen_output": prefs.get_or_default("single_screen_output"),
                "swap_screens": prefs.get_or_default("swap_screens"),
            },
            "layout": plan.as_payload(),
            "audio": _audio(k),
            "sound_cards": [{"id": c.id, "name": c.name, "usb": c.usb} for c in list_sound_cards()],
        }
    )
