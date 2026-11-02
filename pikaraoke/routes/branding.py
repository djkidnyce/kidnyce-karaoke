"""The host's optional header logo: upload and remove (host only), and serve."""

import flask_babel
from flask import abort, jsonify, request, send_file
from flask_smorest import Blueprint

from pikaraoke.lib import branding
from pikaraoke.lib.auth import public

_ = flask_babel.gettext

branding_bp = Blueprint("branding", __name__)


@branding_bp.route("/branding/logo")
@public
def logo():
    """The header logo, if the host uploaded one."""
    found = branding.current()
    if found is None:
        abort(404)
    path, mime = found
    response = send_file(path, mimetype=mime, max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@branding_bp.route("/api/branding/logo", methods=["POST"])
def upload_logo():
    """Save a new header logo. Host only."""
    upload = request.files.get("logo")
    data = upload.read(branding.MAX_BYTES + 1) if upload else b""
    try:
        branding.save(data)
    except ValueError as e:
        messages = {
            # MSG: Error when the uploaded logo is larger than allowed.
            "too_big": _("That file is too big. Use one under 1.5 MB."),
            # MSG: Error when the uploaded file is not a PNG, JPEG or WebP image.
            "not_an_image": _("Use a PNG, JPEG or WebP image."),
            # MSG: Error when no file was chosen for the logo.
            "empty": _("Choose an image first."),
        }
        return jsonify({"ok": False, "message": messages.get(str(e), str(e))}), 400
    # MSG: Message after the header logo was saved.
    return jsonify({"ok": True, "message": _("Logo saved."), "version": branding.version()})


@branding_bp.route("/api/branding/logo", methods=["DELETE"])
def remove_logo():
    """Remove the header logo. Host only."""
    branding.remove()
    # MSG: Message after the header logo was removed.
    return jsonify({"ok": True, "message": _("Logo removed.")})
