"""What the box can drive: the board, its display ports, the TVs on them, and the layout.

Ports come from sysfs, which the kernel keeps current on hotplug, so nothing
here needs X or root. The kiosk session reports what X sees every few seconds;
that report, the host's choice in Settings and the board decide the layout.
"""

import logging
import os
import re
import threading
import time
from dataclasses import dataclass

SCREEN_MODES = ("auto", "single", "dual")

# Two Chromium windows decoding the same video want a 4 GB board. A 4 GB Pi 4
# reports about 3.8 GB, a 2 GB one about 1.8 GB.
LOW_MEMORY_KB = 3_500_000

# A report older than this means the screens are not running.
REPORT_STALE_SECONDS = 30

_MODEL_FILES = ("proc/device-tree/model", "sys/firmware/devicetree/base/model")

# "card1-HDMI-A-2": card, connector type, index.
_CONNECTOR_DIR = re.compile(r"^card\d+-(?P<kind>.+)-(?P<index>\d+)$")

# Connector types that are not a screen a TV plugs into.
_NOT_SCREENS = frozenset({"Writeback"})

# X's modesetting driver names a port after the kernel type, except these.
_X_KIND_NAMES = {"HDMI-A": "HDMI", "Unknown": "None"}

# Singer screen first: HDMI before the other kinds, then by port number.
_KIND_ORDER = ("HDMI", "DP", "eDP", "DVI-D", "DVI-I", "VGA", "DSI", "DPI", "Composite")


@dataclass(frozen=True)
class Board:
    model: str
    memory_kb: int

    @property
    def is_raspberry_pi(self) -> bool:
        return self.model.startswith("Raspberry Pi")

    @property
    def low_memory(self) -> bool:
        return 0 < self.memory_kb < LOW_MEMORY_KB


@dataclass(frozen=True)
class Port:
    output: str  # X's name for it, which the kiosk and Settings use: "HDMI-1"
    label: str  # what is printed on the board: "HDMI0"
    connected: bool
    monitor: str | None  # the TV's name from its EDID
    preferred_mode: str | None


@dataclass(frozen=True)
class Screen:
    role: str  # "singer", "crowd" or "all"
    output: str
    path: str


@dataclass(frozen=True)
class Layout:
    mode: str  # "dual", "single" or "none"
    screens: tuple[Screen, ...]
    # Why this layout, as a code Settings translates: see SCREEN_NOTES in info.html.
    note: str

    @property
    def signature(self) -> str:
        """Changes whenever the kiosk has to relaunch its windows."""
        return " ".join(f"{s.role}={s.output}" for s in self.screens) or "none"

    def as_payload(self) -> dict:
        return {
            "mode": self.mode,
            "note": self.note,
            "signature": self.signature,
            "screens": [{"role": s.role, "output": s.output, "path": s.path} for s in self.screens],
        }


SINGER_PATH = "/splash?singer=1"
CROWD_PATH = "/crowd"
ALL_IN_ONE_PATH = "/splash?all=1"


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def detect_board(root: str = "/") -> Board:
    model = None
    for rel in _MODEL_FILES:
        model = _read_text(os.path.join(root, rel))
        if model:
            break
    memory_kb = 0
    meminfo = _read_text(os.path.join(root, "proc/meminfo")) or ""
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo, re.MULTILINE)
    if match:
        memory_kb = int(match.group(1))
    return Board(model=(model or "").strip("\0 \n") or "Unknown computer", memory_kb=memory_kb)


def monitor_name(edid: bytes) -> str | None:
    """The TV's own name, from the display name descriptor (tag 0xFC) of its EDID."""
    if len(edid) < 128 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    for offset in (54, 72, 90, 108):
        block = edid[offset : offset + 18]
        if block[0:2] == b"\x00\x00" and block[3] == 0xFC:
            name = block[5:18].split(b"\n")[0].decode("ascii", "replace").strip()
            if name:
                return name
    # No name descriptor: the three letter manufacturer code, such as "SAM".
    packed = int.from_bytes(edid[8:10], "big")
    letters = [(packed >> shift) & 0x1F for shift in (10, 5, 0)]
    if all(1 <= c <= 26 for c in letters):
        return "".join(chr(ord("A") + c - 1) for c in letters)
    return None


def _x_output_name(kind: str, index: str) -> str:
    return f"{_X_KIND_NAMES.get(kind, kind)}-{index}"


def _board_label(output: str, hdmi_count: int, board: Board) -> str:
    """Raspberry Pi boards print HDMI0 and HDMI1; the kernel counts from 1."""
    kind, _, index = output.rpartition("-")
    if not board.is_raspberry_pi or kind != "HDMI":
        return output
    if hdmi_count == 1:
        return "HDMI"
    label = f"HDMI{int(index) - 1}"
    if index == "1" and "Pi 4 Model B" in board.model:
        label += ", next to USB C"
    return label


def output_sort_key(output: str) -> tuple[int, int, str]:
    kind, _, index = output.rpartition("-")
    rank = _KIND_ORDER.index(kind) if kind in _KIND_ORDER else len(_KIND_ORDER)
    return (rank, int(index) if index.isdigit() else 0, output)


def list_ports(board: Board, root: str = "/") -> list[Port]:
    drm = os.path.join(root, "sys/class/drm")
    try:
        entries = sorted(os.listdir(drm))
    except OSError:
        return []
    found = []
    for entry in entries:
        match = _CONNECTOR_DIR.match(entry)
        if not match or match.group("kind") in _NOT_SCREENS:
            continue
        base = os.path.join(drm, entry)
        status = (_read_text(os.path.join(base, "status")) or "").strip()
        modes = (_read_text(os.path.join(base, "modes")) or "").split()
        try:
            with open(os.path.join(base, "edid"), "rb") as f:
                edid = f.read()
        except OSError:
            edid = b""
        output = _x_output_name(match.group("kind"), match.group("index"))
        found.append(
            (output, status == "connected", monitor_name(edid), modes[0] if modes else None)
        )
    hdmi_count = sum(1 for output, *_ in found if output.startswith("HDMI-"))
    ports = [
        Port(output, _board_label(output, hdmi_count, board), connected, monitor, mode)
        for output, connected, monitor, mode in found
    ]
    return sorted(ports, key=lambda p: output_sort_key(p.output))


def plan_layout(
    requested: str,
    connected: list[str],
    single_output: str = "auto",
    swap: bool = False,
    low_memory: bool = False,
) -> Layout:
    """Which page goes on which port.

    requested is the host's choice: "auto" uses two screens when two are plugged
    in and the board has the memory for them, "single" puts everything on one,
    "dual" asks for two even on a small board.
    """
    outputs = sorted(dict.fromkeys(connected), key=output_sort_key)
    if requested not in SCREEN_MODES:
        requested = "auto"
    if not outputs:
        return Layout("none", (), "none_connected")

    if len(outputs) >= 2 and (requested == "dual" or (requested == "auto" and not low_memory)):
        singer, crowd = outputs[0], outputs[1]
        if swap:
            singer, crowd = crowd, singer
        return Layout(
            "dual",
            (Screen("singer", singer, SINGER_PATH), Screen("crowd", crowd, CROWD_PATH)),
            "dual_low_memory" if low_memory else "dual",
        )

    one = single_output if single_output in outputs else outputs[0]
    if requested == "single":
        note = "single_chosen"
    elif len(outputs) == 1:
        note = "dual_needs_two" if requested == "dual" else "one_connected"
    else:
        note = "low_memory"
    return Layout("single", (Screen("all", one, ALL_IN_ONE_PATH),), note)


class KioskReport:
    """The ports X last saw connected, as the kiosk session reported them."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._outputs: list[str] = []
        self._at: float | None = None

    def update(self, outputs: list[str]) -> None:
        with self._lock:
            if outputs != self._outputs:
                logging.info(f"Screens connected: {', '.join(outputs) or 'none'}")
            self._outputs = list(outputs)
            self._at = self._clock()

    def seconds_ago(self) -> float | None:
        with self._lock:
            return None if self._at is None else self._clock() - self._at

    def outputs(self) -> list[str] | None:
        """None when the kiosk has not reported lately."""
        age = self.seconds_ago()
        if age is None or age > REPORT_STALE_SECONDS:
            return None
        with self._lock:
            return list(self._outputs)


@dataclass(frozen=True)
class SoundCard:
    id: str  # ALSA's name for it, as in plughw:CARD=<id>,DEV=0
    name: str
    usb: bool


_CARD_LINE = re.compile(r"^\s*\d+ \[(?P<id>[^\]\s]+)\s*\]: (?P<driver>\S+) - (?P<name>.+)$")


def list_sound_cards(root: str = "/") -> list[SoundCard]:
    """The sound cards ALSA sees. /proc stays readable inside the app's sandbox."""
    text = _read_text(os.path.join(root, "proc/asound/cards")) or ""
    cards = []
    for line in text.splitlines():
        match = _CARD_LINE.match(line)
        if match:
            cards.append(
                SoundCard(
                    match.group("id"),
                    match.group("name").strip(),
                    match.group("driver") == "USB-Audio",
                )
            )
    return cards


def parse_outputs(raw: str | None) -> list[str]:
    """The kiosk's comma separated list, keeping only names X could have made."""
    names = [n.strip() for n in (raw or "").split(",")]
    return [n for n in names if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,31}", n)][:8]
