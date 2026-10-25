"""Time left tonight: which queued songs fit before the night ends."""

import datetime as dt

from pikaraoke.lib.night_plan import DEFAULT_SONG_SECONDS, BreakRule, end_of_night, plan

NOW = dt.datetime(2026, 10, 24, 23, 0, 0)
NO_BREAKS = BreakRule(enabled=False, every=1, minutes=5, singers_since_break=0)


def queue(n):
    return [{"file": f"/songs/s{i}.mp4"} for i in range(n)]


class TestEndOfNight:
    def test_after_midnight_is_tomorrow(self):
        assert end_of_night("01:00", NOW) == dt.datetime(2026, 10, 25, 1, 0)

    def test_later_today(self):
        assert end_of_night("23:30", NOW) == dt.datetime(2026, 10, 24, 23, 30)

    def test_just_passed_means_over_not_tomorrow(self):
        late = dt.datetime(2026, 10, 25, 1, 30)
        assert end_of_night("01:00", late) == dt.datetime(2026, 10, 25, 1, 0)

    def test_empty_or_bad_means_no_end(self):
        assert end_of_night("", NOW) is None
        assert end_of_night("25:00", NOW) is None
        assert end_of_night("tonight", NOW) is None


class TestPlan:
    def test_songs_past_the_end_are_flagged(self):
        # Each song 4 minutes plus 1 minute changeover; 30 minutes left.
        result = plan(
            NOW,
            NOW + dt.timedelta(minutes=30),
            queue(8),
            lambda f: 240,
            playing_left=0,
            playing=False,
            changeover=60,
            breaks=NO_BREAKS,
        )
        # Starts at 0, 5, 10, 15, 20, 25 min; the 6th ends at 29, the 7th at 34.
        assert [i["fits"] for i in result["items"]] == [True] * 6 + [False] * 2
        assert result["first_late_index"] == 6
        assert result["late_count"] == 2
        assert result["time_left"] == 1800
        assert result["items"][1]["starts_in"] == 300

    def test_current_song_and_changeover_come_first(self):
        result = plan(
            NOW,
            None,
            queue(1),
            lambda f: 200,
            playing_left=90,
            playing=True,
            changeover=30,
            breaks=NO_BREAKS,
        )
        assert result["items"][0]["starts_in"] == 120
        assert result["queue_seconds"] == 320
        assert result["first_late_index"] is None and result["ends_at"] is None

    def test_break_music_counts(self):
        breaks = BreakRule(enabled=True, every=2, minutes=5, singers_since_break=1)
        result = plan(
            NOW,
            None,
            queue(3),
            lambda f: 240,
            playing_left=0,
            playing=False,
            changeover=0,
            breaks=breaks,
        )
        # One singer since the last break: the first song plays, then a break.
        assert [i["starts_in"] for i in result["items"]] == [0, 240 + 300, 240 + 300 + 240]

    def test_unknown_lengths_count_as_an_average_song(self):
        result = plan(
            NOW,
            None,
            queue(2),
            lambda f: None,
            playing_left=0,
            playing=False,
            changeover=0,
            breaks=NO_BREAKS,
        )
        assert result["queue_seconds"] == 2 * DEFAULT_SONG_SECONDS
        assert result["estimated_songs"] == 2
