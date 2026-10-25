"""Background music streaming routes."""

import os
import random

from flask import abort, jsonify, send_from_directory, url_for
from flask_smorest import Blueprint

from pikaraoke.lib.auth import public
from pikaraoke.lib.current_app import get_karaoke_instance

background_music_bp = Blueprint("bg_music", __name__)

_TRACK_EXTENSIONS = (".mp3", ".mp4", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac")


def _track_directory(path: str) -> str:
    """The folder tracks are served from, whether the path names one or holds them."""
    return os.path.dirname(path) if os.path.isfile(path) else path


def _shuffled_tracks(path: str, limit: int = 200) -> list[str]:
    """Every track in the folder and its subfolders, as paths relative to it, shuffled."""
    if os.path.isfile(path):
        name = os.path.basename(path)
        return [name] if name.lower().endswith(_TRACK_EXTENSIONS) else []
    tracks = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.lower().endswith(_TRACK_EXTENSIONS) and not f.startswith("."):
                tracks.append(os.path.relpath(os.path.join(root, f), path).replace(os.sep, "/"))
    random.shuffle(tracks)
    return tracks[:limit]


def _music_path(k) -> str | None:
    """The host's music folder, or the bundled track while that folder is empty."""
    path = k.bg_music_path
    if path and os.path.exists(path) and _shuffled_tracks(path, limit=1):
        return path
    default = getattr(k, "default_bg_music_path", None)
    return default if default and os.path.exists(default) else path


@background_music_bp.route("/bg_music/<path:file>", methods=["GET"])
@public
def bg_music(file):
    """Stream a background music file.

    send_from_directory, not send_file: `file` comes from the URL, and only this
    refuses one that escapes the directory.
    """
    k = get_karaoke_instance()
    path = _music_path(k)
    if path is None or not file.lower().endswith(_TRACK_EXTENSIONS):
        abort(404)
    # A path naming one track serves that track only. Its neighbours are in the
    # same folder, and pointing at a file is not consent to share them.
    if os.path.isfile(path) and file != os.path.basename(path):
        abort(404)
    return send_from_directory(_track_directory(path), file)


@background_music_bp.route("/api/bg_playlist", methods=["GET"])
@public
def bg_playlist():
    """A shuffled playlist from the music folder, subfolders included."""
    k = get_karaoke_instance()
    path = _music_path(k)
    if path is None or not os.path.exists(path):
        return jsonify([])
    tracks = _shuffled_tracks(path)
    return jsonify([url_for("bg_music.bg_music", file=track) for track in tracks])
