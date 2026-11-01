"""The looks the host can pick for the guest and host pages (Settings, Look).

One set of pages and behaviour; each theme is only colours, type and shape,
in static/kn-themes.css. "classic" is the look the app always had.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    light: bool
    blurb: str
    # Background, panel, accent and text, for the preview card in Settings.
    swatch: tuple[str, str, str, str]


THEMES: tuple[Theme, ...] = (
    Theme(
        "classic",
        "Classic",
        False,
        "The original dark look.",
        ("#14161a", "#1f2229", "#00d1b2", "#f5f5f5"),
    ),
    Theme(
        "club-console",
        "Club Console",
        False,
        "Dark DJ booth with a red accent.",
        ("#101a22", "#1b2b36", "#f15b49", "#f5f7f7"),
    ),
    Theme(
        "daylight",
        "Daylight",
        True,
        "Bright and easy to read in a lit room.",
        ("#f8f4ed", "#fffdf9", "#c94e3c", "#2c2827"),
    ),
    Theme(
        "broadcast",
        "Broadcast",
        False,
        "Bold live show type, square edges.",
        ("#181819", "#232325", "#ed5148", "#f5f4f1"),
    ),
    Theme(
        "vinyl-lounge",
        "Vinyl Lounge",
        True,
        "Warm cream around a dark record sleeve.",
        ("#eee5d8", "#fff9ef", "#127d84", "#27231f"),
    ),
    Theme(
        "social-pulse",
        "Social Pulse",
        True,
        "Friendly and modern, rounded pills.",
        ("#ecf7f7", "#ffffff", "#d94e3e", "#173038"),
    ),
)

DEFAULT_THEME = "classic"
_BY_ID = {t.id: t for t in THEMES}


def theme(theme_id: str | None) -> Theme:
    """A known theme, or the default for anything else."""
    return _BY_ID.get(theme_id or "", _BY_ID[DEFAULT_THEME])
