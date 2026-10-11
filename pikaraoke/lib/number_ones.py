"""Every Billboard Hot 100 number one, browsable by decade, one tap from a karaoke search.

Ships with a snapshot of every number one since the first Hot 100 in 1958.
The host can pull newer weeks on demand. Each new chart is about 12 KB.
"""

import json
import logging
import os
from collections.abc import Callable

import requests

from pikaraoke.lib.karaoke_database import KaraokeDatabase

SEED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "hot100_number_ones.json"
)
DATA_BASE_URL = "https://raw.githubusercontent.com/mhollingshead/billboard-hot-100/main"

# Enough to catch up two years of weekly charts in one refresh.
MAX_CHARTS_PER_REFRESH = 104

_LAST_CHART_KEY = "number_ones_last_chart"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chart_number_ones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song TEXT NOT NULL,
    artist TEXT NOT NULL,
    first_week TEXT NOT NULL,
    weeks INTEGER NOT NULL DEFAULT 1,
    UNIQUE (song, artist)
)
"""


def _get_json(url: str) -> object:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


class NumberOnes:
    """The number one hits table: seeded once, extended by refresh()."""

    def __init__(
        self,
        db: KaraokeDatabase,
        seed_path: str = SEED_PATH,
        fetch: Callable[[str], object] = _get_json,
    ) -> None:
        self._db = db
        self._fetch = fetch
        db.execute(_SCHEMA)
        self._seed(seed_path)

    def _seed(self, seed_path: str) -> None:
        """Load the bundled snapshot the first time, and only then."""
        if self._db.query("SELECT 1 FROM chart_number_ones LIMIT 1"):
            return
        with open(seed_path, encoding="utf-8") as f:
            seed = json.load(f)
        self._db.executemany(
            "INSERT OR IGNORE INTO chart_number_ones (song, artist, first_week, weeks)"
            " VALUES (?, ?, ?, ?)",
            [tuple(row) for row in seed["songs"]],
        )
        self._db.set_metadata(_LAST_CHART_KEY, seed["last_chart"])
        logging.info(f"Loaded {len(seed['songs'])} Hot 100 number ones")

    @property
    def last_chart(self) -> str | None:
        return self._db.get_metadata(_LAST_CHART_KEY)

    def apply_chart(self, chart_date: str, song: str, artist: str) -> None:
        """Count one week at number one."""
        updated = self._db.execute(
            "UPDATE chart_number_ones SET weeks = weeks + 1 WHERE song = ? AND artist = ?",
            (song, artist),
        )
        if not updated.rowcount:
            self._db.execute(
                "INSERT INTO chart_number_ones (song, artist, first_week, weeks) VALUES (?, ?, ?, 1)",
                (song, artist, chart_date),
            )
        self._db.set_metadata(_LAST_CHART_KEY, chart_date)

    def refresh(self) -> int:
        """Pull every chart newer than the last one counted. Returns how many were added."""
        last = self.last_chart or ""
        dates = [d for d in self._fetch(f"{DATA_BASE_URL}/valid_dates.json") if d > last]
        added = 0
        for chart_date in sorted(dates)[:MAX_CHARTS_PER_REFRESH]:
            chart = self._fetch(f"{DATA_BASE_URL}/date/{chart_date}.json")
            top = [e for e in chart.get("data", []) if e.get("this_week") == 1]
            if not top:
                logging.warning(f"Chart {chart_date} has no number one, skipping")
                continue
            self.apply_chart(chart_date, top[0]["song"].strip(), top[0]["artist"].strip())
            added += 1
        return added

    def decades(self) -> list[int]:
        rows = self._db.query(
            "SELECT DISTINCT CAST(substr(first_week, 1, 3) || '0' AS INTEGER) AS decade"
            " FROM chart_number_ones ORDER BY decade DESC"
        )
        return [row["decade"] for row in rows]

    def count(self) -> int:
        return self._db.query("SELECT COUNT(*) FROM chart_number_ones")[0][0]

    def search(
        self,
        decade: int | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Newest first, optionally narrowed to a decade or a title/artist match."""
        clauses, params = [], []
        if decade:
            clauses.append("first_week >= ? AND first_week < ?")
            params += [f"{decade:04d}", f"{decade + 10:04d}"]
        if query:
            clauses.append("(song LIKE ? OR artist LIKE ?)")
            like = f"%{query.strip()}%"
            params += [like, like]
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._db.query(
            f"SELECT song, artist, first_week, weeks FROM chart_number_ones {where}"
            " ORDER BY first_week DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params) + (int(limit), int(offset)),
        )
        return [dict(row) for row in rows]

    def longest_running(self, limit: int = 10) -> list[dict]:
        rows = self._db.query(
            "SELECT song, artist, first_week, weeks FROM chart_number_ones"
            " ORDER BY weeks DESC, first_week ASC LIMIT ?",
            (int(limit),),
        )
        return [dict(row) for row in rows]
