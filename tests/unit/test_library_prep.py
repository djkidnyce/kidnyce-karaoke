"""Library prep: a decade of number ones into the library before a gig."""

from types import SimpleNamespace

import gevent
import pytest

from pikaraoke.lib.library_prep import LibraryPrep, matches, normalize, title_matches

SONGS_1980S = [
    {"song": "Billie Jean", "artist": "Michael Jackson", "first_week": "1983-03-05", "weeks": 7},
    {"song": "Like A Virgin", "artist": "Madonna", "first_week": "1984-12-22", "weeks": 6},
    {"song": "Beat It", "artist": "Michael Jackson", "first_week": "1983-04-30", "weeks": 3},
    {
        "song": "Uptown Funk!",
        "artist": "Mark Ronson Featuring Bruno Mars",
        "first_week": "1985",
        "weeks": 1,
    },
]


def result(title, video_id, duration="3:40"):
    return SimpleNamespace(
        title=title,
        url=f"https://youtu.be/{video_id}",
        video_id=video_id,
        channel="Sing King",
        duration=duration,
    )


class FakeNumberOnes:
    def search(self, decade=None, limit=1000, **kw):
        return [dict(s) for s in SONGS_1980S][:limit]


def make(library=(), search=None, known=None, busy=None):
    queued = []
    prep = LibraryPrep(
        FakeNumberOnes(),
        library_paths=lambda: list(library),
        known_youtube_ids=lambda ids: {i: "x" for i in ids if i in (known or set())},
        search=search or (lambda q: []),
        queue_download=lambda url, enqueue, user, title: queued.append((url, enqueue, user, title)),
        busy=busy or (lambda: False),
        sleep=lambda s: gevent.sleep(0),
    )
    return prep, queued


class TestMatching:
    def test_normalize_drops_accents_and_punctuation(self):
        assert normalize("Beyoncé - Crazy In Love!") == "beyonce crazy in love"

    def test_song_and_artist_in_a_file_name(self):
        assert matches(
            "Billie Jean", "Michael Jackson", "Michael Jackson - Billie Jean (Karaoke)---abc"
        )
        assert not matches("Billie Jean", "Michael Jackson", "Michael Jackson - Beat It (Karaoke)")
        assert not matches("Billie Jean", "Michael Jackson", "Billie Jean - Some Cover Band")

    def test_featured_artists_and_small_words(self):
        assert matches(
            "Uptown Funk!",
            "Mark Ronson Featuring Bruno Mars",
            "Uptown Funk - Mark Ronson ft. Bruno Mars Karaoke Version",
        )
        assert title_matches("Like A Virgin", "LIKE A VIRGIN karaoke")


class TestPreview:
    def test_counts_what_is_here(self):
        prep, _ = make(library=["/songs/Michael Jackson - Billie Jean (Karaoke)---aaa.mp4"])
        assert prep.preview(1980) == {"decade": 1980, "total": 4, "in_library": 1}
        assert prep.in_library_flags(SONGS_1980S) == [True, False, False, False]


class TestPick:
    def test_prefers_song_and_artist(self):
        prep, _ = make()
        picked = prep.pick(
            SONGS_1980S[0],
            [
                result("Billie Jean (Karaoke) - Some Tribute", "t1"),
                result("Michael Jackson - Billie Jean (Karaoke Version)", "t2"),
            ],
        )
        assert picked.video_id == "t2"

    def test_title_alone_is_the_fallback(self):
        prep, _ = make()
        assert (
            prep.pick(SONGS_1980S[0], [result("Billie Jean | Karaoke Version", "t1")]).video_id
            == "t1"
        )

    def test_skips_non_karaoke_and_long_videos(self):
        prep, _ = make()
        assert (
            prep.pick(
                SONGS_1980S[0],
                [
                    result("Michael Jackson - Billie Jean (Official Video)", "v1"),
                    result(
                        "Michael Jackson - Billie Jean Karaoke 1 hour loop",
                        "v2",
                        duration="1:00:00",
                    ),
                ],
            )
            is None
        )

    def test_a_video_already_in_the_library_means_skip(self):
        prep, _ = make(known={"t2"})
        assert (
            prep.pick(SONGS_1980S[0], [result("Michael Jackson - Billie Jean (Karaoke)", "t2")])
            is None
        )


class TestRun:
    def wait(self, prep):
        for _ in range(200):
            if not prep.status.running:
                return
            gevent.sleep(0)
        pytest.fail("library prep did not finish")

    def test_downloads_what_is_missing_without_queueing(self):
        searched = []

        def search(q):
            searched.append(q)
            if "Like A Virgin" in q:
                return [result("Madonna - Like A Virgin (Karaoke Version)", "lav")]
            if "Beat It" in q:
                return [result("Michael Jackson - Beat It (Karaoke)", "bi")]
            return []

        prep, queued = make(
            library=["/s/Michael Jackson - Billie Jean (Karaoke)---a.mp4"], search=search
        )
        assert prep.start(1980)
        self.wait(prep)
        assert [q[0] for q in queued] == ["https://youtu.be/lav", "https://youtu.be/bi"]
        assert all(q[1] is False and q[2] == "Library prep" for q in queued)
        assert not any("Billie Jean" in q for q in searched)
        st = prep.status.as_payload()
        assert st["done"] == 4 and st["queued"] == 2 and st["in_library"] == 1
        assert st["missed"] == ["Mark Ronson Featuring Bruno Mars - Uptown Funk!"]
        assert st["running"] is False

    def test_one_run_at_a_time(self):
        prep, _ = make(busy=lambda: True)
        assert prep.start(1980)
        assert not prep.start(1990)
        prep.stop()
        self.wait(prep)

    def test_waits_while_a_song_plays(self):
        state = {"busy": True}
        prep, queued = make(
            search=lambda q: [result(f"{q} karaoke", "x")], busy=lambda: state["busy"]
        )
        prep.start(1980)
        for _ in range(20):
            gevent.sleep(0)
        assert queued == [] and prep.status.waiting
        state["busy"] = False
        self.wait(prep)
        assert len(queued) == 4

    def test_stop_ends_the_run(self):
        prep, queued = make(busy=lambda: True)
        prep.start(1980)
        gevent.sleep(0)
        prep.stop()
        self.wait(prep)
        assert prep.status.stopped and queued == []

    def test_a_failed_search_does_not_end_the_run(self):
        def search(q):
            raise RuntimeError("yt-dlp broke")

        prep, _ = make(search=search)
        prep.start(1980)
        self.wait(prep)
        assert prep.status.done == 4 and len(prep.status.missed) == 4
