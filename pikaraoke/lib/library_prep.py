"""Fill the library ahead of a gig: every Hot 100 number one of a decade.

The host picks a decade on the charts page. For each number one that is not in
the library yet, this finds a karaoke version on YouTube and hands it to the
download manager without queueing it for anyone.

It never competes with a singer: it feeds one song at a time, and only while
nothing plays and no other download runs. So a prep left going when the night
starts pauses itself between songs instead of slowing the one on screen.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import gevent

PREP_USER = "Library prep"
# A karaoke track longer than this is a medley, a full album or a live stream.
MAX_SECONDS = 10 * 60
# Seconds between YouTube searches, so a long run does not look like a scraper.
SEARCH_GAP_SECONDS = 4.0
# How often a paused run checks whether it may go on.
IDLE_CHECK_SECONDS = 5.0
# The newest missed songs kept for the host to see.
MISSED_KEPT = 25

_SMALL_WORDS = {"the", "a", "an", "and", "of", "feat", "featuring", "ft", "with", "x"}


def normalize(text: str) -> str:
    """Lower case letters and digits only, accents dropped: 'Beyoncé!' -> 'beyonce'."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()


def _key_words(text: str) -> list[str]:
    words = [w for w in normalize(text).split() if w not in _SMALL_WORDS]
    return words or normalize(text).split()


def _lead_artist(artist: str) -> str:
    """'Mark Ronson Featuring Bruno Mars' -> 'mark ronson'."""
    return re.split(r"\s+(?:feat\.?|featuring|ft\.?|with|&|x|,|and)\s+", artist.lower())[0]


def title_matches(title: str, candidate: str) -> bool:
    """Every key word of the song title appears in the candidate."""
    text = f" {normalize(candidate)} "
    return all(f" {w} " in text for w in _key_words(title))


def matches(title: str, artist: str, candidate: str) -> bool:
    """Whether a file name or video title is this song by this artist."""
    text = f" {normalize(candidate)} "
    artist_words = _key_words(_lead_artist(artist))
    return title_matches(title, candidate) and any(f" {w} " in text for w in artist_words)


def _seconds(duration: str) -> int | None:
    """'3:45' or '1:02:03' -> seconds; empty or odd -> None."""
    if not duration or not re.fullmatch(r"\d+(:\d{1,2}){1,2}", duration):
        return None
    total = 0
    for part in duration.split(":"):
        total = total * 60 + int(part)
    return total


@dataclass
class PrepStatus:
    running: bool = False
    decade: int | None = None
    total: int = 0
    done: int = 0
    in_library: int = 0
    queued: int = 0
    missed: list[str] = field(default_factory=list)
    current: str | None = None
    waiting: bool = False
    stopped: bool = False

    def as_payload(self) -> dict:
        return {
            "running": self.running,
            "decade": self.decade,
            "total": self.total,
            "done": self.done,
            "in_library": self.in_library,
            "queued": self.queued,
            "missed": list(self.missed),
            "current": self.current,
            "waiting": self.waiting,
            "stopped": self.stopped,
        }


class LibraryPrep:
    """One prep run at a time, as a greenlet on the app's hub."""

    def __init__(
        self,
        number_ones,
        library_paths: Callable[[], Iterable[str]],
        known_youtube_ids: Callable[[list[str]], dict],
        search: Callable[[str], list],
        queue_download: Callable[..., None],
        busy: Callable[[], bool],
        sleep: Callable[[float], None] = gevent.sleep,
    ) -> None:
        self._number_ones = number_ones
        self._library_paths = library_paths
        self._known_youtube_ids = known_youtube_ids
        self._search = search
        self._queue_download = queue_download
        self._busy = busy
        self._sleep = sleep
        self.status = PrepStatus()
        self._stop = False
        self._greenlet = None

    def songs(self, decade: int) -> list[dict]:
        return self._number_ones.search(decade=decade, limit=1000)

    def _library_names(self) -> list[str]:
        return [normalize(p.rsplit("/", 1)[-1].rsplit(".", 1)[0]) for p in self._library_paths()]

    @staticmethod
    def _in(names: list[str], song: dict) -> bool:
        return any(matches(song["song"], song["artist"], name) for name in names)

    def preview(self, decade: int) -> dict:
        """How many number ones the decade has, and how many are here already."""
        songs = self.songs(decade)
        names = self._library_names()
        have = sum(1 for s in songs if self._in(names, s))
        return {"decade": decade, "total": len(songs), "in_library": have}

    def in_library_flags(self, songs: list[dict]) -> list[bool]:
        names = self._library_names()
        return [self._in(names, s) for s in songs]

    def pick(self, song: dict, results: list) -> object | None:
        """A karaoke version of this song that is not here yet, or None.

        Song and artist both in the video title wins; a title match alone is the
        fallback, since some karaoke channels leave the artist out.
        """
        known = self._known_youtube_ids([r.video_id for r in results]) if results else {}
        usable = []
        for r in results:
            if r.video_id in known:
                return None  # The library has this exact video under another name.
            seconds = _seconds(r.duration)
            if "karaoke" in r.title.lower() and (seconds is None or seconds <= MAX_SECONDS):
                usable.append(r)
        for r in usable:
            if matches(song["song"], song["artist"], r.title):
                return r
        for r in usable:
            if title_matches(song["song"], r.title):
                return r
        return None

    def start(self, decade: int) -> bool:
        if self.status.running:
            return False
        songs = self.songs(decade)
        self._stop = False
        self.status = PrepStatus(running=True, decade=decade, total=len(songs))
        self._greenlet = gevent.spawn(self._run, songs)
        return True

    def stop(self) -> None:
        self._stop = True

    def _wait_until_idle(self) -> bool:
        """False when the host stopped the run while it waited."""
        while self._busy():
            self.status.waiting = True
            if self._stop:
                return False
            self._sleep(IDLE_CHECK_SECONDS)
        self.status.waiting = False
        return not self._stop

    def _miss(self, song: dict) -> None:
        self.status.missed.append(f"{song['artist']} - {song['song']}")
        del self.status.missed[:-MISSED_KEPT]

    def _run(self, songs: list[dict]) -> None:
        try:
            for song in songs:
                if self._stop:
                    self.status.stopped = True
                    break
                self.status.current = f"{song['artist']} - {song['song']}"
                # The library grows as the run goes, so look each time.
                if self._in(self._library_names(), song):
                    self.status.in_library += 1
                    self.status.done += 1
                    continue
                if not self._wait_until_idle():
                    self.status.stopped = True
                    break
                try:
                    results = self._search(f'{song["artist"]} {song["song"]} "karaoke"')
                except Exception as e:  # yt-dlp failures must not end the whole run
                    logging.warning(f"Library prep search failed for {self.status.current}: {e}")
                    results = []
                choice = self.pick(song, results)
                if choice is None:
                    self._miss(song)
                else:
                    self._queue_download(choice.url, False, PREP_USER, choice.title)
                    self.status.queued += 1
                self.status.done += 1
                self._sleep(SEARCH_GAP_SECONDS)
        finally:
            self.status.running = False
            self.status.waiting = False
            self.status.current = None
            logging.info(
                f"Library prep {self.status.decade}s: {self.status.queued} queued, "
                f"{self.status.in_library} already here, {len(self.status.missed)} missed"
            )
