"""Board and port detection from sysfs, the layout planner, and the two screen endpoints."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from pikaraoke.lib.display_hardware import (
    REPORT_STALE_SECONDS,
    Board,
    KioskReport,
    SoundCard,
    detect_board,
    list_ports,
    list_sound_cards,
    monitor_name,
    parse_outputs,
    plan_layout,
)
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.routes.display import display_bp
from tests.conftest import make_route_app

PI4 = Board("Raspberry Pi 4 Model B Rev 1.5", 3_884_000)
PI4_2GB = Board("Raspberry Pi 4 Model B Rev 1.5", 1_890_000)
PROXIED = {"X-Real-IP": "192.168.1.50"}


def edid(name: str | None = "SONY TV", maker: str = "SNY") -> bytes:
    data = bytearray(128)
    data[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    packed = sum((ord(c) - ord("A") + 1) << shift for c, shift in zip(maker, (10, 5, 0)))
    data[8:10] = packed.to_bytes(2, "big")
    if name:
        block = b"\x00\x00\x00\xfc\x00" + (name.encode() + b"\n").ljust(13, b" ")
        data[72:90] = block
    return bytes(data)


def fake_sysfs(root: Path, model: str | None, mem_kb: int, connectors: dict) -> Path:
    if model is not None:
        (root / "proc/device-tree").mkdir(parents=True)
        (root / "proc/device-tree/model").write_bytes(model.encode() + b"\0")
    (root / "proc").mkdir(exist_ok=True)
    (root / "proc/meminfo").write_text(f"MemTotal:       {mem_kb} kB\nMemFree: 1 kB\n")
    drm = root / "sys/class/drm"
    drm.mkdir(parents=True)
    (drm / "card0").mkdir()
    (drm / "renderD128").mkdir()
    (drm / "version").write_text("drm 1.1.0\n")
    for name, (status, modes, raw_edid) in connectors.items():
        d = drm / name
        d.mkdir()
        (d / "status").write_text(status + "\n")
        (d / "modes").write_text("".join(m + "\n" for m in modes))
        (d / "edid").write_bytes(raw_edid)
    return root


@pytest.fixture
def pi4_root(tmp_path):
    return fake_sysfs(
        tmp_path,
        "Raspberry Pi 4 Model B Rev 1.5",
        3_884_000,
        {
            "card1-HDMI-A-1": ("connected", ["1920x1080", "1280x720"], edid("SONY TV")),
            "card1-HDMI-A-2": ("disconnected", [], b""),
            "card1-Writeback-1": ("unknown", [], b""),
        },
    )


def test_detects_a_pi_4_and_its_memory(pi4_root):
    board = detect_board(str(pi4_root))
    assert board.model == "Raspberry Pi 4 Model B Rev 1.5"
    assert board.is_raspberry_pi
    assert not board.low_memory


def test_a_pc_without_a_device_tree_is_named_plainly(tmp_path):
    board = detect_board(str(fake_sysfs(tmp_path, None, 16_000_000, {})))
    assert board.model == "Unknown computer"
    assert not board.is_raspberry_pi


def test_a_2_gb_board_is_low_on_memory():
    assert PI4_2GB.low_memory
    assert not Board("x", 0).low_memory  # unknown memory never blocks two screens


def test_lists_the_pi_4_hdmi_ports_by_their_board_labels(pi4_root):
    ports = list_ports(PI4, str(pi4_root))
    assert [p.output for p in ports] == ["HDMI-1", "HDMI-2"]
    assert [p.label for p in ports] == ["HDMI0, next to USB C", "HDMI1"]
    assert ports[0].connected and not ports[1].connected
    assert ports[0].monitor == "SONY TV"
    assert ports[0].preferred_mode == "1920x1080"
    assert ports[1].monitor is None and ports[1].preferred_mode is None


def test_a_single_hdmi_pi_calls_its_port_hdmi(tmp_path):
    root = fake_sysfs(
        tmp_path,
        "Raspberry Pi 3 Model B Plus Rev 1.3",
        926_000,
        {"card0-HDMI-A-1": ("connected", ["1920x1080"], edid())},
    )
    board = detect_board(str(root))
    assert [p.label for p in list_ports(board, str(root))] == ["HDMI"]
    assert board.low_memory


def test_a_pc_keeps_x_names_and_puts_hdmi_first(tmp_path):
    root = fake_sysfs(
        tmp_path,
        None,
        16_000_000,
        {
            "card0-DP-1": ("connected", ["2560x1440"], edid(None, "DEL")),
            "card0-eDP-1": ("connected", ["1920x1200"], b""),
            "card0-HDMI-A-1": ("connected", ["3840x2160"], edid("LG TV")),
        },
    )
    ports = list_ports(detect_board(str(root)), str(root))
    assert [(p.output, p.label) for p in ports] == [
        ("HDMI-1", "HDMI-1"),
        ("DP-1", "DP-1"),
        ("eDP-1", "eDP-1"),
    ]
    assert ports[1].monitor == "DEL"  # no name in the EDID: the maker's code


def test_no_drm_at_all_lists_nothing(tmp_path):
    assert list_ports(PI4, str(tmp_path)) == []


@pytest.mark.parametrize("raw", [b"", b"\x00" * 128, edid()[:100]])
def test_a_broken_edid_has_no_name(raw):
    assert monitor_name(raw) is None


def roles(layout):
    return [(s.role, s.output, s.path) for s in layout.screens]


def test_auto_with_two_tvs_uses_both():
    layout = plan_layout("auto", ["HDMI-2", "HDMI-1"])
    assert layout.mode == "dual"
    assert roles(layout) == [
        ("singer", "HDMI-1", "/splash?singer=1"),
        ("crowd", "HDMI-2", "/crowd"),
    ]
    assert layout.note == "dual"
    assert layout.signature == "singer=HDMI-1 crowd=HDMI-2"


def test_swap_puts_the_singer_on_the_second_port():
    layout = plan_layout("dual", ["HDMI-1", "HDMI-2"], swap=True)
    assert roles(layout)[0][:2] == ("singer", "HDMI-2")


def test_auto_with_one_tv_puts_everything_on_it():
    layout = plan_layout("auto", ["HDMI-2"])
    assert layout.mode == "single"
    assert roles(layout) == [("all", "HDMI-2", "/splash?all=1")]
    assert layout.note == "one_connected"


def test_one_screen_chosen_with_two_tvs():
    layout = plan_layout("single", ["HDMI-1", "HDMI-2"])
    assert roles(layout) == [("all", "HDMI-1", "/splash?all=1")]
    assert layout.note == "single_chosen"


def test_one_screen_on_the_chosen_port():
    layout = plan_layout("single", ["HDMI-1", "HDMI-2"], single_output="HDMI-2")
    assert roles(layout)[0][1] == "HDMI-2"


def test_a_chosen_port_that_is_unplugged_falls_back_to_one_that_is():
    layout = plan_layout("single", ["HDMI-1"], single_output="HDMI-2")
    assert roles(layout)[0][1] == "HDMI-1"


def test_two_screens_chosen_but_one_plugged_in():
    layout = plan_layout("dual", ["HDMI-1"])
    assert layout.mode == "single"
    assert layout.note == "dual_needs_two"


def test_auto_on_a_small_board_stays_on_one_screen():
    layout = plan_layout("auto", ["HDMI-1", "HDMI-2"], low_memory=True)
    assert layout.mode == "single"
    assert layout.note == "low_memory"


def test_the_host_can_still_force_two_screens_on_a_small_board():
    layout = plan_layout("dual", ["HDMI-1", "HDMI-2"], low_memory=True)
    assert layout.mode == "dual"
    assert layout.note == "dual_low_memory"


def test_nothing_plugged_in():
    layout = plan_layout("auto", [])
    assert layout.mode == "none" and layout.screens == () and layout.signature == "none"


def test_an_unknown_mode_is_treated_as_auto():
    assert plan_layout("banana", ["HDMI-1", "HDMI-2"]).mode == "dual"


def test_three_outputs_use_the_first_two():
    layout = plan_layout("auto", ["DP-1", "HDMI-2", "HDMI-1"])
    assert [s.output for s in layout.screens] == ["HDMI-1", "HDMI-2"]


def test_the_kiosk_report_goes_stale():
    now = [100.0]
    report = KioskReport(clock=lambda: now[0])
    assert report.outputs() is None
    report.update(["HDMI-1"])
    assert report.outputs() == ["HDMI-1"]
    now[0] += REPORT_STALE_SECONDS + 1
    assert report.outputs() is None
    assert report.seconds_ago() == pytest.approx(REPORT_STALE_SECONDS + 1)


def test_parse_outputs_keeps_only_output_names():
    assert parse_outputs("HDMI-1, HDMI-2,,DUMMY0") == ["HDMI-1", "HDMI-2", "DUMMY0"]
    assert parse_outputs("HDMI-1;rm -rf,<b>x</b>,../etc") == []
    assert parse_outputs(None) == []


# The endpoints


@pytest.fixture
def karaoke(tmp_path, pi4_root, monkeypatch):
    monkeypatch.setattr(
        "pikaraoke.routes.display.list_ports", lambda board: list_ports(board, str(pi4_root))
    )
    return SimpleNamespace(
        board=PI4,
        kiosk_report=KioskReport(),
        preferences=PreferenceManager(str(tmp_path / "config.ini")),
        events=EventSystem(),
    )


def build(karaoke, admin=True):
    app = make_route_app(display_bp, [("/splash", "splash.splash")], admin=admin)
    app.config["KARAOKE_INSTANCE"] = karaoke
    app.config["SITE_NAME"] = "Test"
    return app.test_client()


def test_the_kiosk_gets_its_layout_and_is_remembered(karaoke):
    client = build(karaoke, admin=False)
    body = client.get("/api/display/layout?connected=HDMI-1,HDMI-2").get_json()
    assert body["mode"] == "dual"
    assert body["signature"] == "singer=HDMI-1 crowd=HDMI-2"
    assert karaoke.kiosk_report.outputs() == ["HDMI-1", "HDMI-2"]


def test_the_layout_follows_the_host_setting(karaoke):
    karaoke.preferences.set("screen_mode", "single")
    karaoke.preferences.set("single_screen_output", "HDMI-2")
    body = build(karaoke).get("/api/display/layout?connected=HDMI-1,HDMI-2").get_json()
    assert body["screens"] == [{"role": "all", "output": "HDMI-2", "path": "/splash?all=1"}]


def test_a_phone_cannot_report_screens(karaoke):
    client = build(karaoke)
    response = client.get("/api/display/layout?connected=HDMI-9", headers=PROXIED)
    assert response.status_code == 404
    assert karaoke.kiosk_report.outputs() is None


def test_hardware_is_host_only(karaoke):
    assert build(karaoke, admin=False).get("/api/display/hardware").status_code == 403


def test_hardware_before_the_kiosk_reports_uses_sysfs(karaoke):
    body = build(karaoke).get("/api/display/hardware").get_json()
    assert body["board"]["model"].startswith("Raspberry Pi 4")
    assert body["board"]["memory_gb"] == 3.7
    assert body["dual_capable"] is True
    assert body["screens_running"] is False
    assert [(p["output"], p["connected"], p["role"]) for p in body["ports"]] == [
        ("HDMI-1", True, "all"),
        ("HDMI-2", False, None),
    ]
    assert body["ports"][0]["monitor"] == "SONY TV"
    assert body["layout"]["note"] == "one_connected"
    assert body["settings"] == {
        "screen_mode": "auto",
        "single_screen_output": "auto",
        "swap_screens": False,
    }


def test_hardware_trusts_what_x_reports(karaoke):
    karaoke.kiosk_report.update(["HDMI-1", "HDMI-2", "DUMMY0"])
    body = build(karaoke).get("/api/display/hardware").get_json()
    assert body["screens_running"] is True
    assert [(p["output"], p["connected"]) for p in body["ports"]] == [
        ("HDMI-1", True),
        ("HDMI-2", True),
        ("DUMMY0", True),
    ]
    assert body["layout"]["signature"] == "singer=HDMI-1 crowd=HDMI-2"


# Sound

PI_CARDS = """ 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 1 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1
                      vc4-hdmi-1
 2 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 3 [Device         ]: USB-Audio - USB PnP Audio Device
                      Generic USB PnP Audio Device at usb-0000:01:00.0-1.3, full speed
"""


def test_lists_the_pi_sound_cards(tmp_path):
    (tmp_path / "proc/asound").mkdir(parents=True)
    (tmp_path / "proc/asound/cards").write_text(PI_CARDS)
    cards = list_sound_cards(str(tmp_path))
    assert [(c.id, c.usb) for c in cards] == [
        ("vc4hdmi0", False),
        ("vc4hdmi1", False),
        ("Headphones", False),
        ("Device", True),
    ]
    assert cards[3].name == "USB PnP Audio Device"


def test_no_sound_cards_without_alsa(tmp_path):
    assert list_sound_cards(str(tmp_path)) == []


def test_the_kiosk_gets_the_sound_choice(karaoke):
    karaoke.preferences.set("audio_output", "headphones")
    karaoke.preferences.set("audio_level", 72)
    body = build(karaoke).get("/api/display/layout?connected=HDMI-1").get_json()
    assert body["audio"] == {"output": "headphones", "level": 72}


def test_by_default_the_kiosk_keeps_the_installer_sound(karaoke):
    body = build(karaoke).get("/api/display/layout?connected=HDMI-1").get_json()
    assert body["audio"] == {"output": "", "level": -1}


def test_hardware_lists_sound_cards_for_settings(karaoke, monkeypatch):
    monkeypatch.setattr(
        "pikaraoke.routes.display.list_sound_cards",
        lambda: [SoundCard("Device", "USB PnP Audio Device", True)],
    )
    body = build(karaoke).get("/api/display/hardware").get_json()
    assert body["sound_cards"] == [{"id": "Device", "name": "USB PnP Audio Device", "usb": True}]
    assert body["audio"] == {"output": "", "level": -1}
