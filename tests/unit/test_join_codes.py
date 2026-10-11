"""Join codes: format, binding to the hosted session, and the guess limiter."""

import pytest

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.join_codes import (
    ALPHABET,
    CODE_LENGTH,
    AttemptLimiter,
    JoinCodeManager,
    generate_code,
    normalize_code,
)
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.play_history_manager import PlayHistoryManager


@pytest.fixture
def db(tmp_path):
    d = KaraokeDatabase(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def events():
    return EventSystem()


@pytest.fixture
def history(db, events):
    return PlayHistoryManager(db=db, events=events)


@pytest.fixture
def codes(db, events, history):
    def current_uuid():
        session = history.get_current_session()
        return session["uuid"] if session else None

    return JoinCodeManager(db, events, current_uuid)


def test_generated_codes_use_the_unambiguous_alphabet():
    for _ in range(500):
        code = generate_code()
        assert len(code) == CODE_LENGTH
        assert set(code) <= set(ALPHABET)
    assert not set("01OIL") & set(ALPHABET)


def test_normalize_accepts_what_people_type():
    assert normalize_code(" abc-234 ") == "ABC234"
    assert normalize_code("abc 234") == "ABC234"
    assert normalize_code(None) == ""


def test_verify_accepts_the_current_code_in_any_case(codes):
    assert codes.verify(codes.code.lower())
    assert codes.verify(codes.code[:3] + " " + codes.code[3:])


def test_verify_rejects_wrong_and_malformed_codes(codes):
    wrong = "".join(ALPHABET[(ALPHABET.index(c) + 1) % len(ALPHABET)] for c in codes.code)
    assert not codes.verify(wrong)
    assert not codes.verify("")
    assert not codes.verify(codes.code + "X")
    assert not codes.verify(None)


def test_code_survives_a_restart(db, events, history, codes):
    before = codes.code
    again = JoinCodeManager(db, events, lambda: None)
    assert again.code == before


def test_first_session_adopts_the_lobby_code(codes, history):
    """Phones that joined before the first song must not be kicked when it starts."""
    lobby = codes.code
    history.start_session("Friday")
    assert codes.code == lobby


def test_renaming_the_session_keeps_the_code(codes, history):
    uuid = history.start_session("Friday")
    code = codes.code
    history.rename_session(uuid, "Friday Night")
    assert codes.code == code


def test_ending_the_session_rotates_the_code(codes, history):
    history.start_session("Friday")
    code = codes.code
    history.end_session()
    assert codes.code != code


def test_switching_sessions_rotates_the_code(codes, history):
    history.start_session("Friday")
    code = codes.code
    history.start_session("Saturday")
    assert codes.code != code


def test_rotation_is_announced(codes, events):
    heard = []
    events.on("join_code_changed", lambda: heard.append(True))
    codes.rotate()
    assert heard == [True]


def test_join_url_carries_the_code(codes):
    url = codes.join_url("https://karaoke.djkidnyce.com/")
    assert url == f"https://karaoke.djkidnyce.com/join/{codes.code}"


class TestAttemptLimiter:
    def test_blocks_after_max_failures_in_window(self):
        limiter = AttemptLimiter(max_failures=3, window_seconds=60)
        for i in range(3):
            assert not limiter.is_blocked("1.2.3.4", now=100 + i)
            limiter.record_failure("1.2.3.4", now=100 + i)
        assert limiter.is_blocked("1.2.3.4", now=103)

    def test_other_clients_are_unaffected(self):
        limiter = AttemptLimiter(max_failures=1, window_seconds=60)
        limiter.record_failure("1.2.3.4", now=100)
        assert limiter.is_blocked("1.2.3.4", now=101)
        assert not limiter.is_blocked("5.6.7.8", now=101)

    def test_failures_expire(self):
        limiter = AttemptLimiter(max_failures=2, window_seconds=60)
        limiter.record_failure("a", now=100)
        limiter.record_failure("a", now=101)
        assert limiter.is_blocked("a", now=102)
        assert not limiter.is_blocked("a", now=162)

    def test_reset_clears_a_client(self):
        limiter = AttemptLimiter(max_failures=1, window_seconds=60)
        limiter.record_failure("a", now=100)
        limiter.reset("a")
        assert not limiter.is_blocked("a", now=101)

    def test_memory_stays_bounded(self):
        limiter = AttemptLimiter(max_failures=5, window_seconds=60)
        for i in range(10_050):
            limiter.record_failure(f"10.0.{i // 256}.{i % 256}", now=100)
        assert len(limiter._failures) <= 10_000

    def test_brute_force_is_infeasible_at_the_default_limits(self):
        """A code lives one night. At 10 guesses per 5 minutes, one client guessing for
        12 straight hours has about a 1 in 600,000 chance against 31^6 codes."""
        limiter = AttemptLimiter()
        guesses_per_night = limiter.max_failures * (12 * 3600 / limiter.window_seconds)
        chance = guesses_per_night / len(ALPHABET) ** CODE_LENGTH
        assert chance < 2e-6
