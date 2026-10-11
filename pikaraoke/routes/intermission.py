"""Break music between singers: the audio the screens play, and the host's controls."""

import os

from flask import abort, jsonify, request, send_from_directory
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance
from pikaraoke.lib.intermission import AUDIO_EXTENSIONS, MAX_BREAK_MINUTES, BreakPlan

intermission_bp = Blueprint("intermission", __name__)


def _plan_json(plan: BreakPlan) -> dict:
    return {
        "tracks": [{"title": t.title, "duration": round(t.duration, 1)} for t in plan.tracks],
        "track_count": len(plan.tracks),
        "fade_last": plan.fade_last,
        "planned_seconds": round(plan.play_seconds, 1),
        "target_seconds": plan.target_seconds,
    }


@intermission_bp.route("/intermission/track/<path:name>")
@public
def track(name: str):
    """Stream one break song. send_from_directory refuses paths that escape the folder."""
    if not name.lower().endswith(AUDIO_EXTENSIONS):
        abort(404)
    k = get_karaoke_instance()
    root = k.get_intermission_music_path()
    if not root or not os.path.isdir(root):
        abort(404)
    return send_from_directory(root, name)


@intermission_bp.route("/api/intermission")
def status():
    """Break settings, library size and the break in progress. Host only."""
    k = get_karaoke_instance()
    tracks = k.intermission_library.tracks
    return jsonify(
        {
            "enabled": k.intermission.enabled,
            "active": k.intermission.as_payload(),
            "singers_since_break": k.intermission.singers_since_break,
            "music_path": k.get_intermission_music_path(),
            "track_count": len(tracks),
            "library_minutes": round(sum(t.duration for t in tracks) / 60, 1),
            "last_scan": k.intermission_library.last_scan,
        }
    )


@intermission_bp.route("/api/intermission/preview")
def preview():
    """How many songs a break of this length would play. Host only."""
    k = get_karaoke_instance()
    minutes = request.args.get("minutes", type=float)
    if minutes is not None and not 0 < minutes <= MAX_BREAK_MINUTES:
        return jsonify({"error": f"minutes must be between 0 and {MAX_BREAK_MINUTES}"}), 400
    return jsonify(_plan_json(k.intermission.preview(minutes)))


@intermission_bp.route("/api/intermission/start", methods=["POST"])
def start():
    """Take a break before the next singer. Host only."""
    k = get_karaoke_instance()
    k.intermission.request_break()
    return jsonify({"requested": True})


@intermission_bp.route("/api/intermission/skip", methods=["POST"])
def skip():
    """End the break now and bring up the next singer. Host only."""
    k = get_karaoke_instance()
    k.intermission.finish()
    return jsonify({"ended": True})


@intermission_bp.route("/api/intermission/rescan", methods=["POST"])
def rescan():
    """Look for new break music. Host only."""
    k = get_karaoke_instance()
    tracks = k.intermission_library.scan()
    return jsonify({"track_count": len(tracks)})
