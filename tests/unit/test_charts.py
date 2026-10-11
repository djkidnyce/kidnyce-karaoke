"""Most requested songs and the Hot 100 number ones."""

import json

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.number_ones import SEED_PATH, NumberOnes
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager
from pikaraoke.lib.request_stats import RequestStatsManager


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "charts.db"))
    yield d
    d.close()


class TestRequestStats:
    @pytest.fixture
    def setup(self, db, tmp_path):
        events = EventSystem()
        session = {"uuid": "night-1"}
        ids = {"/songs/Dont Stop---1111111111a.mp4": "1111111111a"}
        stats = RequestStatsManager(db, events, lambda: session["uuid"], lambda path: ids.get(path))
        queue = QueueManager(
            preferences=PreferenceManager(str(tmp_path / "config.ini")),
            events=events,
            filename_from_path=lambda p, *_: p.rsplit("/", 1)[-1].split("---")[0],
        )
        return stats, queue, session

    def test_queueing_a_song_counts_a_request(self, setup):
        stats, queue, _ = setup
        queue.enqueue("/songs/Dont Stop---1111111111a.mp4", "Ana")
        top = stats.top_requested()
        assert top[0]["title"] == "Dont Stop"
        assert top[0]["requests"] == 1

    def test_transposes_and_the_randomizer_are_not_requests(self, setup):
        stats, queue, _ = setup
        queue.enqueue("/songs/a.mp4", "Ana", 2, True, count_as_request=False)
        queue.enqueue("/songs/b.mp4", "Randomizer", count_as_request=False)
        assert stats.total_requests() == 0

    def test_ranking_and_session_scope(self, setup, db):
        stats, _, session = setup
        for user in ("Ana", "Bo", "Cy"):
            stats.record("/songs/Dont Stop---1111111111a.mp4", user, "Dont Stop")
        stats.record("/songs/Halo.mp4", "Ana", "Halo")
        session["uuid"] = "night-2"
        stats.record("/songs/Halo.mp4", "Bo", "Halo")

        all_time = stats.top_requested()
        assert [(s["title"], s["requests"]) for s in all_time] == [("Dont Stop", 3), ("Halo", 2)]
        assert all_time[0]["singers"] == 3
        tonight = stats.top_requested(session_uuid="night-2")
        assert [(s["title"], s["requests"]) for s in tonight] == [("Halo", 1)]
        assert stats.number_one()["title"] == "Dont Stop"
        assert stats.total_requests("night-1") == 4

    def test_requests_before_the_first_song_belong_to_tonight(self, db):
        """A session auto starts with the first song, after the requests that queued it."""
        events = EventSystem()
        session = {"uuid": None}
        stats = RequestStatsManager(db, events, lambda: session["uuid"], lambda path: None)
        stats.record("/songs/Halo.mp4", "Ana", "Halo")
        session["uuid"] = "night-3"
        events.emit("session_changed")
        assert stats.top_requested(session_uuid="night-3")[0]["title"] == "Halo"

    def test_one_youtube_video_is_one_song_whatever_its_title(self, setup):
        stats, _, _ = setup
        path = "/songs/Dont Stop---1111111111a.mp4"
        stats.record(path, "Ana", "Dont Stop")
        stats.record(path, "Bo", "Don't Stop Believin'")
        top = stats.top_requested()
        assert len(top) == 1
        assert top[0]["requests"] == 2
        assert top[0]["title"] == "Don't Stop Believin'"  # the latest title wins

    def test_local_rips_group_by_title_ignoring_case(self, setup):
        stats, _, _ = setup
        stats.record("/songs/halo.mp4", "Ana", "Halo")
        stats.record("/songs/HALO.mp4", "Bo", "HALO")
        assert stats.top_requested()[0]["requests"] == 2


class TestNumberOnes:
    def test_seed_loads_every_number_one_once(self, db):
        with open(SEED_PATH, encoding="utf-8") as f:
            seed = json.load(f)
        ones = NumberOnes(db, fetch=None)
        assert ones.count() == len(seed["songs"]) > 1100
        assert ones.last_chart == seed["last_chart"]
        NumberOnes(db, fetch=None)  # a restart does not seed twice
        assert ones.count() == len(seed["songs"])

    def test_the_first_hot_100_number_one(self, db):
        ones = NumberOnes(db, fetch=None)
        fifties = ones.search(decade=1950, limit=500)
        oldest = min(fifties, key=lambda s: s["first_week"])
        assert (oldest["song"], oldest["artist"], oldest["first_week"]) == (
            "Poor Little Fool",
            "Ricky Nelson",
            "1958-08-04",
        )

    def test_search_and_decades(self, db):
        ones = NumberOnes(db, fetch=None)
        assert 1950 in ones.decades() and 2020 in ones.decades()
        hits = ones.search(query="hey jude")
        assert [(s["artist"], s["first_week"][:4], s["weeks"]) for s in hits] == [
            ("The Beatles", "1968", 9)
        ]
        assert all(s["first_week"].startswith("199") for s in ones.search(decade=1990, limit=500))

    def test_refresh_counts_new_weeks(self, tmp_path):
        seed = tmp_path / "seed.json"
        seed.write_text(
            json.dumps(
                {
                    "last_chart": "2026-10-10",
                    "songs": [["Patient Zero", "Taylor Swift", "2026-10-10", 1]],
                }
            )
        )
        charts = {
            "valid_dates.json": ["2026-10-03", "2026-10-10", "2026-10-17", "2026-10-24"],
            "date/2026-10-17.json": {
                "data": [{"song": "Patient Zero", "artist": "Taylor Swift", "this_week": 1}]
            },
            "date/2026-10-24.json": {
                "data": [{"song": "New Song", "artist": "New Artist", "this_week": 1}]
            },
        }
        fetched = []

        def fake_fetch(url):
            key = url.split("/main/", 1)[1]
            fetched.append(key)
            return charts[key]

        db = KaraokeDatabase(str(tmp_path / "refresh.db"))
        ones = NumberOnes(db, seed_path=str(seed), fetch=fake_fetch)
        assert ones.refresh() == 2
        assert "date/2026-10-03.json" not in fetched  # already counted
        rows = {s["song"]: s for s in ones.search()}
        assert rows["Patient Zero"]["weeks"] == 2
        assert rows["New Song"]["first_week"] == "2026-10-24"
        assert ones.last_chart == "2026-10-24"
        assert ones.refresh() == 0  # nothing new the second time
        db.close()
