"""The looks the host can pick in Settings, Look."""

from pathlib import Path

from pikaraoke.lib.ui_themes import DEFAULT_THEME, THEMES, theme

CSS = (Path(__file__).resolve().parents[2] / "pikaraoke" / "static" / "kn-themes.css").read_text()


def test_the_five_designs_and_classic():
    assert [t.id for t in THEMES] == [
        "classic",
        "club-console",
        "daylight",
        "broadcast",
        "vinyl-lounge",
        "social-pulse",
    ]
    assert DEFAULT_THEME == "classic"


def test_unknown_or_empty_falls_back_to_classic():
    assert theme("social-pulse").id == "social-pulse"
    assert theme("neon").id == "classic"
    assert theme(None).id == "classic"


def test_light_themes_are_marked_light():
    assert {t.id for t in THEMES if t.light} == {"daylight", "vinyl-lounge", "social-pulse"}


def test_every_theme_but_classic_has_its_tokens():
    for t in THEMES:
        if t.id != "classic":
            assert f'html[data-kn-theme="{t.id}"] {{' in CSS
            assert f'html[data-kn-theme="{t.id}"]' in CSS.split("what makes each theme its own")[0]
