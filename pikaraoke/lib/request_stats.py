"""Most requested songs: every song a guest puts in the queue, counted for good.

Plays only count songs that were sung through. Requests count demand, which is
what a DJ wants to know when building the library before the next gig.
"""

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase

_SCHEMA = """
CREATE TABLE IF NOT EXISTS song_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_uuid TEXT,
    youtube_id TEXT,
    song_title TEXT NOT NULL,
    performer TEXT NOT NULL,
    requested_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_requests_session ON song_requests(session_uuid);
CREATE INDEX IF NOT EXISTS idx_requests_requested_at ON song_requests(requested_at);
"""

# Same identity rule as play history: the YouTube id when there is one, so
# renames and re-downloads stay one song, else the title.
_SONG_KEY = "COALESCE('y:' || youtube_id, 't:' || LOWER(song_title))"


class RequestStatsManager:
    """Records requests and answers the leaderboard questions."""

    def __init__(
        self,
        db: KaraokeDatabase,
        events: EventSystem,
        get_session_uuid,
        get_youtube_id,
    ) -> None:
        self._db = db
        self._get_session_uuid = get_session_uuid
        self._get_youtube_id = get_youtube_id
        for statement in _SCHEMA.strip().split(";"):
            if statement.strip():
                db.execute(statement)
        events.on("song_requested", self.record)
        events.on("session_changed", self._adopt_lobby_requests)

    def _adopt_lobby_requests(self) -> None:
        """Give requests made while no session was open to the session that opens next.

        A session auto starts with the first song, so the requests that queued
        that song came in before it existed. They belong to tonight all the same.
        """
        session_uuid = self._get_session_uuid()
        if session_uuid:
            self._db.execute(
                "UPDATE song_requests SET session_uuid = ? WHERE session_uuid IS NULL",
                (session_uuid,),
            )

    def record(self, song_path: str, performer: str, title: str) -> None:
        """Count one request."""
        self._db.execute(
            "INSERT INTO song_requests (session_uuid, youtube_id, song_title, performer)"
            " VALUES (?, ?, ?, ?)",
            (self._get_session_uuid(), self._get_youtube_id(song_path), title, performer),
        )

    def top_requested(self, limit: int = 25, session_uuid: str | None = None) -> list[dict]:
        """Songs ranked by request count. The latest title wins for display."""
        where, params = ("WHERE session_uuid = ?", (session_uuid,)) if session_uuid else ("", ())
        rows = self._db.query(
            f"""
            SELECT g.song_key, g.youtube_id, g.requests, g.singers, g.last_requested,
                   r.song_title AS title
            FROM (
                SELECT {_SONG_KEY} AS song_key,
                       MAX(youtube_id) AS youtube_id,
                       COUNT(*) AS requests,
                       COUNT(DISTINCT LOWER(performer)) AS singers,
                       MAX(requested_at) AS last_requested,
                       MAX(id) AS last_id
                FROM song_requests
                {where}
                GROUP BY song_key
            ) g
            JOIN song_requests r ON r.id = g.last_id
            ORDER BY g.requests DESC, g.last_id DESC
            LIMIT ?
            """,
            params + (int(limit),),
        )
        return [dict(row) for row in rows]

    def total_requests(self, session_uuid: str | None = None) -> int:
        if session_uuid:
            rows = self._db.query(
                "SELECT COUNT(*) FROM song_requests WHERE session_uuid = ?", (session_uuid,)
            )
        else:
            rows = self._db.query("SELECT COUNT(*) FROM song_requests")
        return rows[0][0]

    def number_one(self, session_uuid: str | None = None) -> dict | None:
        """The single most requested song, for the crowd screen."""
        top = self.top_requested(1, session_uuid)
        return top[0] if top else None
