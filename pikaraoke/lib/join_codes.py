"""Six character join codes that tie a guest's phone to the session being hosted.

The code rides in the QR code on the crowd screen, so scanning it is the whole
join flow. Rotating it locks out every phone that joined with the old one,
which is what ending or switching a hosted session should do.
"""

import hmac
import logging
import secrets
import threading
import time
from collections.abc import Callable

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase

# No 0/O, 1/I/L: the code is read off a TV across a room and typed on a phone.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6

_CODE_KEY = "join_code"
# The session uuid the code belongs to. Empty means "lobby": issued while no
# session was open, and adopted by the next one rather than replaced, so phones
# that joined before the first song are not kicked when that song starts.
_SESSION_KEY = "join_code_session"


def normalize_code(candidate: str | None) -> str:
    """Uppercase and strip what people add when they type a code by hand."""
    if not candidate:
        return ""
    return "".join(ch for ch in candidate.upper() if ch not in " -_\t")


def generate_code() -> str:
    """A fresh code from the OS CSPRNG. 31^6 is about 887 million codes."""
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


class JoinCodeManager:
    """Owns the current join code and rotates it when the hosted session changes."""

    def __init__(
        self,
        db: KaraokeDatabase,
        events: EventSystem,
        get_session_uuid: Callable[[], str | None],
        fresh: bool = False,
    ) -> None:
        """fresh issues a new code at once, so every start of the app is a new night."""
        self._db = db
        self._events = events
        self._get_session_uuid = get_session_uuid
        self._lock = threading.Lock()
        self._reconcile()
        if fresh:
            self.rotate()
        events.on("session_changed", self._reconcile)

    @property
    def code(self) -> str:
        """The code guests must present right now."""
        code = self._db.get_metadata(_CODE_KEY)
        if not code:
            code = self.rotate()
        return code

    def verify(self, candidate: str | None) -> bool:
        """Constant time comparison, so response timing leaks nothing about the code."""
        given = normalize_code(candidate)
        if len(given) != CODE_LENGTH:
            return False
        return hmac.compare_digest(given.encode(), self.code.encode())

    def rotate(self) -> str:
        """Issue a new code and lock out every phone that joined with the old one."""
        with self._lock:
            code = generate_code()
            self._db.set_metadata(_CODE_KEY, code)
            self._db.set_metadata(_SESSION_KEY, self._get_session_uuid() or "")
        logging.info("Join code rotated")
        self._events.emit("join_code_changed")
        return code

    def join_url(self, base_url: str) -> str:
        """The address the QR code carries."""
        return f"{base_url.rstrip('/')}/join/{self.code}"

    def _reconcile(self) -> None:
        """Keep the code bound to the session being hosted.

        A rename keeps the same uuid and changes nothing. A lobby code is adopted
        by the first session to open. Any other change of session, including
        ending one, is a new night and gets a new code.
        """
        current = self._get_session_uuid() or ""
        bound = self._db.get_metadata(_SESSION_KEY)
        if bound is None or not self._db.get_metadata(_CODE_KEY):
            self.rotate()
            return
        if bound == current:
            return
        if bound == "" and current:
            self._db.set_metadata(_SESSION_KEY, current)
            return
        self.rotate()


class AttemptLimiter:
    """Counts failed join attempts per client so the code cannot be guessed.

    Nginx rate limits too. This is the second layer, and the one that still
    holds when the app is reached without the proxy in front.
    """

    def __init__(self, max_failures: int = 10, window_seconds: int = 300) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        stamps = [t for t in self._failures.get(key, []) if t > cutoff]
        if stamps:
            self._failures[key] = stamps
        else:
            self._failures.pop(key, None)
        return stamps

    def is_blocked(self, key: str, now: float | None = None) -> bool:
        """True once a client has used up its failures for the window."""
        now = time.monotonic() if now is None else now
        with self._lock:
            return len(self._recent(key, now)) >= self.max_failures

    def record_failure(self, key: str, now: float | None = None) -> None:
        """Count one wrong code against the client."""
        now = time.monotonic() if now is None else now
        with self._lock:
            # Bound memory against a flood of distinct addresses.
            if key not in self._failures and len(self._failures) >= 10_000:
                for stale in [k for k in self._failures if not self._recent(k, now)]:
                    self._failures.pop(stale, None)
                if len(self._failures) >= 10_000:
                    self._failures.pop(next(iter(self._failures)))
            self._failures.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        """Forget a client's failures once it gets the code right."""
        with self._lock:
            self._failures.pop(key, None)
