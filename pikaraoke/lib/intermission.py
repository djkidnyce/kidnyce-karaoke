"""Break music between singers, planned to fit the time the host sets.

The host picks a break length. The planner looks at the length of every track
in the intermission music folder and picks whole songs whose lengths add up as
close to that time as possible without going over. A 5 minute break might come
out as three tracks totalling 4:52.
"""

import logging
import math
import os
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.ffmpeg import get_media_duration
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.preference_manager import PreferenceManager

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav")

# The planner is exact, so its cost grows with tracks times seconds. A random
# sample this size still finds a near exact fit for any realistic break, and
# keeps planning well under a second on a Pi 4.
MAX_CANDIDATES = 120
MAX_BREAK_MINUTES = 30

# A fill track is only added when the whole songs leave a gap this long.
FILL_GAP_SECONDS = 20

# Tracks played in the last few breaks are avoided while others still fit.
RECENT_TRACK_MEMORY = 40

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intermission_tracks (
    path TEXT PRIMARY KEY,
    duration REAL NOT NULL,
    mtime REAL NOT NULL
);
"""


@dataclass(frozen=True)
class Track:
    """One break song. `path` is relative to the music folder."""

    path: str
    duration: float

    @property
    def title(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]


@dataclass
class BreakPlan:
    """The songs one break will play, in order."""

    tracks: list[Track]
    target_seconds: float
    # True when the last song is cut at the target with a fade, either because
    # every song is longer than the break or because fill mode topped it up.
    fade_last: bool = False

    @property
    def total_seconds(self) -> float:
        return sum(t.duration for t in self.tracks)

    @property
    def play_seconds(self) -> float:
        """How long the break actually runs."""
        if self.fade_last:
            return min(self.total_seconds, self.target_seconds)
        return self.total_seconds


def plan_break(
    tracks: list[Track],
    target_seconds: float,
    recent: frozenset[str] = frozenset(),
    rng: random.Random | None = None,
    fill: bool = False,
) -> BreakPlan:
    """Pick whole songs that fill the break as closely as possible without running over.

    Exact subset sum at one second resolution over a shuffled sample, so the
    best fit is always found and a different mix of songs comes out each time.
    Durations are rounded up, so the real total never exceeds the target.
    """
    rng = rng or random.Random()
    target = int(max(0, min(target_seconds, MAX_BREAK_MINUTES * 60)))
    playable = [t for t in tracks if t.duration > 0]
    if target == 0 or not playable:
        return BreakPlan([], target)

    fresh = [t for t in playable if t.path not in recent]
    pool = fresh if any(math.ceil(t.duration) <= target for t in fresh) else playable
    pool = pool[:]
    rng.shuffle(pool)
    pool = pool[:MAX_CANDIDATES]

    # reachable[s] holds the indices of one combination that sums to s seconds.
    reachable: dict[int, tuple[int, ...]] = {0: ()}
    for index, track in enumerate(pool):
        length = math.ceil(track.duration)
        if length > target:
            continue
        for total, combo in list(reachable.items()):
            new_total = total + length
            if new_total <= target and new_total not in reachable:
                reachable[new_total] = combo + (index,)
        if target in reachable:
            break

    best = reachable[max(reachable)]
    chosen = [pool[i] for i in best]

    if not chosen:
        # Every song is longer than the break: play the shortest and fade it out.
        shortest = min(playable, key=lambda t: t.duration)
        return BreakPlan([shortest], target, fade_last=True)

    if fill and target - sum(math.ceil(t.duration) for t in chosen) >= FILL_GAP_SECONDS:
        leftovers = [t for t in pool if t not in chosen]
        if leftovers:
            chosen.append(rng.choice(leftovers))
            return BreakPlan(chosen, target, fade_last=True)

    return BreakPlan(chosen, target)


class TrackLibrary:
    """The intermission music folder, with each track's length cached in SQLite.

    Probing a file with ffprobe takes a noticeable fraction of a second on a
    Pi, so lengths are cached against the file's mtime and only new or changed
    files are probed again.
    """

    def __init__(self, db: KaraokeDatabase, get_music_path: Callable[[], str | None]) -> None:
        self._db = db
        self._get_music_path = get_music_path
        self._tracks: list[Track] = []
        self._scan_lock = threading.Lock()
        self.last_scan: float | None = None
        db.execute(_SCHEMA)

    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks)

    @property
    def music_path(self) -> str | None:
        return self._get_music_path()

    def scan(self) -> list[Track]:
        """Walk the music folder, probe what is new, and forget what is gone."""
        root = self._get_music_path()
        if not root or not os.path.isdir(root):
            self._tracks = []
            return []
        with self._scan_lock:
            cached = {
                row["path"]: (row["duration"], row["mtime"])
                for row in self._db.query("SELECT path, duration, mtime FROM intermission_tracks")
            }
            found: list[Track] = []
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in sorted(filenames):
                    if not name.lower().endswith(AUDIO_EXTENSIONS) or name.startswith("."):
                        continue
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, root)
                    try:
                        mtime = os.path.getmtime(full)
                    except OSError as e:
                        logging.warning(f"Skipping unreadable track {rel}: {e}")
                        continue
                    hit = cached.get(rel)
                    if hit and hit[1] == mtime:
                        duration = hit[0]
                    else:
                        duration = get_media_duration(full) or 0.0
                        self._db.execute(
                            "INSERT OR REPLACE INTO intermission_tracks (path, duration, mtime)"
                            " VALUES (?, ?, ?)",
                            (rel, duration, mtime),
                        )
                    if duration > 0:
                        found.append(Track(rel, duration))
            gone = set(cached) - {t.path for t in found}
            for rel in gone:
                self._db.execute("DELETE FROM intermission_tracks WHERE path = ?", (rel,))
            self._tracks = found
            self.last_scan = time.time()
        logging.info(f"Intermission library: {len(found)} tracks in {root}")
        return found

    def scan_in_background(self) -> None:
        threading.Thread(target=self.scan, daemon=True).start()


@dataclass
class _ActiveBreak:
    plan: BreakPlan
    started_at: float  # wall clock, for the screens
    ends_at: float  # wall clock
    track_urls: list[str] = field(default_factory=list)


class IntermissionManager:
    """Decides when a break is due, plans it, and reports it to the screens."""

    def __init__(
        self,
        preferences: PreferenceManager,
        events: EventSystem,
        library: TrackLibrary,
        track_url: Callable[[str], str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._preferences = preferences
        self._events = events
        self.library = library
        self._track_url = track_url
        self._clock = clock
        self._recent: deque[str] = deque(maxlen=RECENT_TRACK_MEMORY)
        self._active: _ActiveBreak | None = None
        self._forced = False
        self.singers_since_break = 0
        events.on("song_ending", self._on_song_ending)

    def _on_song_ending(self, reason: str | None = None) -> None:
        # A transpose restarts the same singer, so it is not a turn ending.
        if reason != "transpose":
            self.singers_since_break += 1

    @property
    def enabled(self) -> bool:
        return bool(self._preferences.get_or_default("enable_intermission"))

    @property
    def is_active(self) -> bool:
        return self._active is not None

    def request_break(self) -> None:
        """Take a break before the next singer, whatever the settings say."""
        self._forced = True
        self._events.emit("now_playing_update")

    def is_due(self) -> bool:
        """True when the next singer should wait for break music first."""
        if self._active is not None:
            return False
        if not self.library.tracks:
            return False
        if self._forced:
            return True
        every = max(1, int(self._preferences.get_or_default("intermission_every")))
        return self.enabled and self.singers_since_break >= every

    def preview(self, minutes: float | None = None) -> BreakPlan:
        """What a break of this length would play, without starting it."""
        return plan_break(
            self.library.tracks,
            self._target_seconds(minutes),
            recent=frozenset(self._recent),
            fill=bool(self._preferences.get_or_default("intermission_fill")),
        )

    def start(self, minutes: float | None = None) -> BreakPlan | None:
        """Plan and begin a break. None when there is nothing to play."""
        plan = self.preview(minutes)
        self._forced = False
        self.singers_since_break = 0
        if not plan.tracks:
            logging.warning("Intermission skipped: no track fits")
            return None
        now = self._clock()
        self._active = _ActiveBreak(
            plan=plan,
            started_at=now,
            ends_at=now + plan.play_seconds,
            track_urls=[self._track_url(t.path) for t in plan.tracks],
        )
        self._recent.extend(t.path for t in plan.tracks)
        logging.info(
            f"Intermission: {len(plan.tracks)} tracks, "
            f"{plan.play_seconds:.0f}s of {plan.target_seconds:.0f}s"
        )
        self._events.emit("now_playing_update")
        return plan

    def tick(self) -> bool:
        """True while the break should keep going."""
        if self._active is None:
            return False
        if self._clock() >= self._active.ends_at:
            self.finish()
            return False
        return True

    def finish(self) -> None:
        """End the break now, from the timer or from the host."""
        if self._active is None and not self._forced:
            return
        self._active = None
        self._forced = False
        self._events.emit("now_playing_update")

    def as_payload(self) -> dict | None:
        """The break as the screens need it, riding the now_playing payload."""
        if self._active is None:
            return {"pending": True} if self._forced else None
        active = self._active
        now = self._clock()
        return {
            "pending": False,
            "tracks": [
                {"url": url, "title": t.title, "duration": round(t.duration, 2)}
                for url, t in zip(active.track_urls, active.plan.tracks)
            ],
            "fade_last": active.plan.fade_last,
            "planned_seconds": round(active.plan.play_seconds, 1),
            "target_seconds": active.plan.target_seconds,
            "elapsed_seconds": round(now - active.started_at, 1),
            "remaining_seconds": round(max(0.0, active.ends_at - now), 1),
            "volume": float(self._preferences.get_or_default("intermission_volume")),
        }

    def _target_seconds(self, minutes: float | None) -> float:
        if minutes is None:
            minutes = float(self._preferences.get_or_default("intermission_minutes"))
        return max(0.0, min(float(minutes), MAX_BREAK_MINUTES)) * 60
