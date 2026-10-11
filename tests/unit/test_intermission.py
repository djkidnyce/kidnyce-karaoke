"""Break music: the planner that fits songs to a time, and the manager that runs breaks."""

import math
import random
from unittest.mock import patch

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.intermission import (
    MAX_BREAK_MINUTES,
    IntermissionManager,
    Track,
    TrackLibrary,
    plan_break,
)
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.preference_manager import PreferenceManager


def tracks(*seconds: float) -> list[Track]:
    return [Track(f"song{i}.mp3", s) for i, s in enumerate(seconds)]


class TestPlanBreak:
    def test_never_runs_over_the_target(self):
        rng = random.Random(7)
        library = [Track(f"t{i}.mp3", rng.uniform(120, 330)) for i in range(200)]
        for minutes in (2, 3.5, 5, 8, 10, 15, 30):
            plan = plan_break(library, minutes * 60, rng=random.Random(minutes))
            assert plan.play_seconds <= minutes * 60
            if minutes >= 3:  # every track here is at least 2:00, so 2 minutes needs a fade
                assert plan.total_seconds <= minutes * 60
                assert not plan.fade_last

    def test_finds_an_exact_fit_when_one_exists(self):
        # 180 + 120 = 300 exactly; greedy largest first would stop at 240.
        plan = plan_break(tracks(240, 180, 120), 300, rng=random.Random(1))
        assert sorted(t.duration for t in plan.tracks) == [120, 180]

    def test_fills_close_to_the_target_with_real_song_lengths(self):
        rng = random.Random(3)
        library = [Track(f"t{i}.mp3", rng.uniform(150, 300)) for i in range(60)]
        plan = plan_break(library, 600, rng=random.Random(9))
        assert 600 - plan.total_seconds < 15

    def test_rounds_lengths_up_so_the_real_total_fits(self):
        plan = plan_break(tracks(100.4, 100.4, 100.4), 301, rng=random.Random(1))
        assert sum(math.ceil(t.duration) for t in plan.tracks) <= 301
        assert len(plan.tracks) == 2  # three would be 303 seconds once rounded up

    def test_avoids_recent_tracks_while_others_fit(self):
        library = tracks(200, 200, 200, 200)
        recent = frozenset({"song0.mp3", "song1.mp3"})
        plan = plan_break(library, 400, recent=recent, rng=random.Random(2))
        assert {t.path for t in plan.tracks} == {"song2.mp3", "song3.mp3"}

    def test_reuses_recent_tracks_when_nothing_else_fits(self):
        plan = plan_break(tracks(200, 500), 300, recent=frozenset({"song0.mp3"}))
        assert [t.path for t in plan.tracks] == ["song0.mp3"]

    def test_every_song_longer_than_the_break_plays_the_shortest_faded(self):
        plan = plan_break(tracks(400, 260, 500), 120)
        assert [t.duration for t in plan.tracks] == [260]
        assert plan.fade_last
        assert plan.play_seconds == 120

    def test_fill_mode_tops_up_and_fades(self):
        plan = plan_break(tracks(200, 200, 200), 500, fill=True, rng=random.Random(4))
        assert len(plan.tracks) == 3
        assert plan.fade_last
        assert plan.play_seconds == 500

    def test_fill_mode_leaves_a_near_fit_alone(self):
        plan = plan_break(tracks(250, 245, 400), 500, fill=True, rng=random.Random(4))
        assert not plan.fade_last
        assert plan.total_seconds == 495

    def test_empty_library_and_zero_target(self):
        assert plan_break([], 300).tracks == []
        assert plan_break(tracks(100), 0).tracks == []

    def test_target_is_capped(self):
        plan = plan_break(tracks(*[200] * 30), 10_000)
        assert plan.target_seconds == MAX_BREAK_MINUTES * 60

    def test_different_breaks_get_different_mixes(self):
        library = [Track(f"t{i}.mp3", 200) for i in range(30)]
        mixes = {tuple(t.path for t in plan_break(library, 400).tracks) for _ in range(20)}
        assert len(mixes) > 1


class FakeLibrary:
    def __init__(self, items):
        self._items = items

    @property
    def tracks(self):
        return list(self._items)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def prefs(tmp_path):
    p = PreferenceManager(str(tmp_path / "config.ini"))
    p.set("enable_intermission", True)
    p.set("intermission_minutes", 5)
    p.set("intermission_every", 2)
    return p


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def events():
    return EventSystem()


@pytest.fixture
def manager(prefs, events, clock):
    library = FakeLibrary(tracks(100, 100, 100, 200))
    return IntermissionManager(
        prefs, events, library, lambda rel: f"/intermission/track/{rel}", clock
    )


class TestIntermissionManager:
    def test_due_after_the_set_number_of_singers(self, manager, events):
        events.emit("song_ending", "complete")
        assert not manager.is_due()
        events.emit("song_ending", "skip")
        assert manager.is_due()

    def test_a_transpose_is_not_a_new_singer(self, manager, events):
        events.emit("song_ending", "complete")
        events.emit("song_ending", "transpose")
        assert not manager.is_due()

    def test_disabled_means_never_due(self, manager, events, prefs):
        prefs.set("enable_intermission", False)
        for _ in range(5):
            events.emit("song_ending", "complete")
        assert not manager.is_due()

    def test_the_host_can_force_a_break(self, manager, prefs):
        prefs.set("enable_intermission", False)
        manager.request_break()
        assert manager.is_due()
        assert manager.as_payload() == {"pending": True}

    def test_no_music_means_no_break(self, prefs, events, clock):
        empty = IntermissionManager(prefs, events, FakeLibrary([]), str, clock)
        empty.request_break()
        assert not empty.is_due()

    def test_a_break_runs_for_its_planned_length(self, manager, clock):
        plan = manager.start()
        assert plan.total_seconds == 300
        assert manager.tick()
        clock.now += 299
        assert manager.tick()
        clock.now += 2
        assert not manager.tick()
        assert not manager.is_active

    def test_starting_resets_the_singer_count(self, manager, events):
        events.emit("song_ending", "complete")
        events.emit("song_ending", "complete")
        manager.start()
        manager.finish()
        assert manager.singers_since_break == 0
        assert not manager.is_due()

    def test_payload_tells_screens_where_the_break_is(self, manager, clock):
        manager.start(minutes=3)
        clock.now += 30
        payload = manager.as_payload()
        assert payload["planned_seconds"] == 100  # only one 100s track fits in 3:00
        assert payload["elapsed_seconds"] == 30
        assert payload["remaining_seconds"] == payload["planned_seconds"] - 30
        assert all(t["url"].startswith("/intermission/track/") for t in payload["tracks"])
        assert payload["volume"] == 0.8

    def test_the_host_can_end_a_break(self, manager, events):
        heard = []
        events.on("now_playing_update", lambda: heard.append(1))
        manager.start()
        manager.finish()
        assert manager.as_payload() is None
        assert heard  # the screens were told

    def test_a_short_break_uses_the_preview_minutes(self, manager):
        plan = manager.preview(minutes=2)
        assert plan.total_seconds <= 120


class TestTrackLibrary:
    def test_scans_probes_once_and_caches(self, tmp_path):
        music = tmp_path / "music"
        (music / "sub").mkdir(parents=True)
        (music / "a.mp3").write_bytes(b"x")
        (music / "sub" / "b.m4a").write_bytes(b"x")
        (music / "notes.txt").write_text("not audio")
        (music / ".hidden.mp3").write_bytes(b"x")
        db = KaraokeDatabase(str(tmp_path / "lib.db"))
        library = TrackLibrary(db, lambda: str(music))

        with patch("pikaraoke.lib.intermission.get_media_duration", return_value=200.0) as probe:
            found = library.scan()
            assert sorted(t.path for t in found) == ["a.mp3", "sub/b.m4a"]
            assert probe.call_count == 2
            library.scan()
            assert probe.call_count == 2  # cached by mtime

        (music / "a.mp3").unlink()
        with patch("pikaraoke.lib.intermission.get_media_duration", return_value=200.0):
            assert [t.path for t in library.scan()] == ["sub/b.m4a"]
        assert len(db.query("SELECT * FROM intermission_tracks")) == 1
        db.close()

    def test_unprobeable_files_are_skipped(self, tmp_path):
        music = tmp_path / "music"
        music.mkdir()
        (music / "broken.mp3").write_bytes(b"x")
        db = KaraokeDatabase(str(tmp_path / "lib.db"))
        library = TrackLibrary(db, lambda: str(music))
        with patch("pikaraoke.lib.intermission.get_media_duration", return_value=None):
            assert library.scan() == []
        db.close()

    def test_missing_folder_is_an_empty_library(self, tmp_path):
        db = KaraokeDatabase(str(tmp_path / "lib.db"))
        assert TrackLibrary(db, lambda: str(tmp_path / "nope")).scan() == []
        db.close()
