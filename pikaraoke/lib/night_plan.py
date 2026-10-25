"""How much of the queue fits before the night ends.

The host sets when the night ends. From the song playing, every queued song's
length, the changeover between singers and the break music settings, this
works out when each queued song should start, and which ones will not fit.
Lengths come from the files themselves (ffprobe), kept so each file is read
once; a file that cannot be read counts as an average song.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass

# A song whose length cannot be read counts as this long.
DEFAULT_SONG_SECONDS = 240
# An end time this long in the past means the night is over, not tomorrow's.
OVER_GRACE = dt.timedelta(hours=6)

_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def end_of_night(ends_at: str, now: dt.datetime) -> dt.datetime | None:
    """The moment the night ends, from "HH:MM" on the local clock, or None.

    "01:00" at 23:00 is tomorrow; "01:00" at 01:30 means the night just ended.
    """
    m = _TIME.match((ends_at or "").strip())
    if not m:
        return None
    end = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    if end < now - OVER_GRACE:
        end += dt.timedelta(days=1)
    elif end > now + (dt.timedelta(days=1) - OVER_GRACE):
        end -= dt.timedelta(days=1)
    return end


@dataclass(frozen=True)
class BreakRule:
    enabled: bool
    every: int
    minutes: float
    singers_since_break: int


def plan(
    now: dt.datetime,
    ends_at: dt.datetime | None,
    queue: list[dict],
    seconds_of: Callable[[str], float | None],
    playing_left: float,
    playing: bool,
    changeover: float,
    breaks: BreakRule,
) -> dict:
    """Start times for the queue and where the night runs out."""
    t = now + dt.timedelta(seconds=max(0.0, playing_left))
    since = breaks.singers_since_break + (1 if playing else 0)
    every = max(1, breaks.every)
    items = []
    first_late = None
    estimated = 0
    for index, item in enumerate(queue):
        if breaks.enabled and since >= every:
            t += dt.timedelta(minutes=breaks.minutes)
            since = 0
        if playing or index > 0:
            t += dt.timedelta(seconds=changeover)
        seconds = seconds_of(item["file"])
        if seconds is None:
            seconds = DEFAULT_SONG_SECONDS
            estimated += 1
        starts = t
        t += dt.timedelta(seconds=seconds)
        since += 1
        fits = ends_at is None or t <= ends_at
        if not fits and first_late is None:
            first_late = index
        items.append(
            {
                "starts_in": round((starts - now).total_seconds()),
                "starts_at": starts.strftime("%H:%M"),
                "fits": fits,
            }
        )
    queue_seconds = round((t - now).total_seconds())
    payload = {
        "ends_at": ends_at.strftime("%H:%M") if ends_at else None,
        "time_left": max(0, round((ends_at - now).total_seconds())) if ends_at else None,
        "queue_seconds": queue_seconds,
        "queue_ends_at": t.strftime("%H:%M"),
        "first_late_index": first_late,
        "late_count": 0 if first_late is None else len(queue) - first_late,
        "estimated_songs": estimated,
        "items": items,
    }
    return payload
