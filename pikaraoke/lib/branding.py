"""The host's own logo above the navigation (Settings, Look). Optional.

Only PNG, JPEG and WebP are accepted, checked by their first bytes, not by the
file name or the browser's word for it. SVG is refused: it can carry script.
The file lives in the data directory, so it survives app updates.
"""

from __future__ import annotations

import os

from pikaraoke.lib.get_platform import get_data_directory

# Under Nginx's 2 MB request limit, with room for the form around it.
MAX_BYTES = 1_500_000
_NAME = "header-logo"
_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def image_type(data: bytes) -> str | None:
    """'png', 'jpg' or 'webp' from the file's first bytes, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _folder(root: str | None) -> str:
    return os.path.join(root or get_data_directory(), "branding")


def current(root: str | None = None) -> tuple[str, str] | None:
    """(path, mimetype) of the saved logo, or None when there is none."""
    folder = _folder(root)
    for ext, mime in _TYPES.items():
        path = os.path.join(folder, f"{_NAME}.{ext}")
        if os.path.isfile(path):
            return path, mime
    return None


def version(root: str | None = None) -> int | None:
    """Changes whenever the logo does, for cache busting; None with no logo."""
    found = current(root)
    return int(os.path.getmtime(found[0])) if found else None


def save(data: bytes, root: str | None = None) -> str:
    """Store an uploaded logo, replacing any earlier one. Raises ValueError."""
    if not data:
        raise ValueError("empty")
    if len(data) > MAX_BYTES:
        raise ValueError("too_big")
    kind = image_type(data)
    if kind is None:
        raise ValueError("not_an_image")
    folder = _folder(root)
    os.makedirs(folder, exist_ok=True)
    remove(root)
    path = os.path.join(folder, f"{_NAME}.{kind}")
    tmp = path + ".new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def remove(root: str | None = None) -> None:
    folder = _folder(root)
    for ext in _TYPES:
        try:
            os.remove(os.path.join(folder, f"{_NAME}.{ext}"))
        except FileNotFoundError:
            pass
