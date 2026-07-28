"""
tests/systema/common/test_greeting_registers.py

At 04:15 on a Tuesday the banner said "Monday again, Thirdy."

Monday was CORRECT — `greeting_weekday()` deliberately rolls back before 05:00
because at 4am you are still living Monday night. The wrong part was the
register: `_DAY_LINES` mixed any-hour phrasings with ones that assume the day is
just STARTING ("A fresh week", "Back to it"), and about a third of every night
greeting came from that bucket.

The fix filed the two registers apart rather than deleting anything. These tests
hold that line: opening phrasings never appear once the day is over, every
phrasing stays reachable somewhere, and the small-hours rollback still works.
"""
import re
from datetime import datetime, timedelta

import pytest

from systema.common.greeting import (_DAY_LINES, _DAY_OPENING_LINES,
                                     _SIGNAL_LINES, all_phrasings,
                                     collect_signals, greeting_weekday,
                                     pool_for, time_bucket)

_OPENING = {named for pairs in _DAY_OPENING_LINES.values() for named, _ in pairs}

# 2026-07-27 is a Monday, 2026-07-28 a Tuesday.
_MON = datetime(2026, 7, 27)
_TUE = datetime(2026, 7, 28)


def _named(pool):
    return {named for named, _anon in pool}


# ── the reported bug ─────────────────────────────────────────────────────────

def test_the_small_hours_still_belong_to_the_previous_day():
    """Not a regression to undo: at 04:15 Tuesday you are living Monday night."""
    assert greeting_weekday(_TUE.replace(hour=4, minute=15)) == 0
    assert greeting_weekday(_TUE.replace(hour=9)) == 1


def test_no_day_opening_line_survives_into_the_small_hours():
    pool = _named(pool_for(_TUE.replace(hour=4, minute=15)))

    assert not (pool & _OPENING), "a day that ended hours ago cannot be 'a fresh week'"


def test_no_day_opening_line_appears_late_in_the_evening_either():
    """The defect was never only about midnight — 'Back to it' at 22:30 is
    equally wrong, which is why the split is by register and not by hour."""
    pool = _named(pool_for(_MON.replace(hour=22, minute=30)))

    assert not (pool & _OPENING)


@pytest.mark.parametrize("hour", [6, 9, 11, 13, 16])
def test_day_opening_lines_are_still_offered_while_the_day_is_young(hour):
    pool = _named(pool_for(_MON.replace(hour=hour)))

    assert pool & _OPENING, "variety must survive where the phrasing is true"


# ── nothing was lost ─────────────────────────────────────────────────────────

def test_every_moved_line_is_still_reachable():
    everything = {named for named, _anon in all_phrasings()}

    missing = _OPENING - everything
    assert not missing, f"phrasings dropped out of all_phrasings(): {missing}"


def test_the_night_pool_is_still_a_healthy_size():
    """Correctness must not be bought with a threadbare pool."""
    pool = pool_for(_TUE.replace(hour=4, minute=15))

    assert len(pool) >= 20, f"night pool collapsed to {len(pool)} phrasings"


def test_every_weekday_keeps_some_any_hour_day_line():
    """Otherwise a night greeting loses all day identity for that weekday."""
    for weekday in range(7):
        assert _DAY_LINES.get(weekday), f"weekday {weekday} has no any-hour lines"


# ── the same defect, one pool over: signal lines ─────────────────────────────
#
# "Late again. Sleep is also a feature." arrived at 10:36 in the morning. The
# streak signal was computed from HISTORY alone (3+ of the last 5 sessions
# started before 05:00), and leaving the app running overnight satisfies that
# all day. Every line in the pool is present tense, and signal lines are
# weighted SIGNAL_WEIGHT times, so it did not just appear — it dominated.

_LATE = {named for named, _ in _SIGNAL_LINES["late_night_streak"]}

# Four of five sessions started in the small hours: a genuine streak.
_STREAK_TIMES = [datetime(2026, 7, 24, 2, 10), datetime(2026, 7, 25, 1, 40),
                 datetime(2026, 7, 26, 3, 5), datetime(2026, 7, 27, 0, 30),
                 datetime(2026, 7, 28, 2, 15)]


@pytest.mark.parametrize("hour,minute", [(10, 36), (7, 0), (14, 0), (19, 30)])
def test_a_late_night_streak_is_silent_outside_the_night(hour, minute):
    now = datetime(2026, 7, 28, hour, minute)

    signals = collect_signals(_STREAK_TIMES, now)
    pool = _named(pool_for(now, signals))

    assert "late_night_streak" not in signals
    assert not (pool & _LATE), "told at breakfast that they are up late"


@pytest.mark.parametrize("hour", [23, 2])
def test_the_streak_still_fires_at_night(hour):
    """The observation is real — it just has to be said at the right time."""
    day = 28 if hour == 23 else 29
    now = datetime(2026, 7, day, hour, 0)

    signals = collect_signals(_STREAK_TIMES, now)
    pool = _named(pool_for(now, signals))

    assert "late_night_streak" in signals
    assert pool & _LATE


def test_a_normal_sleeper_never_gets_the_streak_even_at_night():
    daytime = [datetime(2026, 7, 24 + i, 14, 0) for i in range(5)]
    now = datetime(2026, 7, 28, 23, 30)

    assert "late_night_streak" not in collect_signals(daytime, now)


# ── the invariant that stops this whole class recurring ──────────────────────
#
# Three separate bugs shipped from the same mistake: a line whose WORDING fixes
# it to a time of day, offered from a pool that is not gated to that time.
#   * `_DAY_LINES` mixed "A fresh week" with any-hour lines  -> 04:00 Monday
#   * `late_night_streak` fired from history alone           -> 10:36 morning
#   * `back_soon` hard-coded "Good afternoon. Round two"     -> any hour at all
#
# Rather than a fourth one-off assertion, this walks every weekday and every
# bucket with signals resolved the way the app resolves them, and fails on ANY
# line that names a time of day other than the one it is being shown in.

_TIME_WORDS = {
    'morning':   (r'\bmorning\b', r'\bsunrise\b', r'\bdawn\b'),
    'afternoon': (r'\bafternoon\b',),
    'evening':   (r'\bevening\b',),
    'night':     (r'\bnight\b', r'\btonight\b', r'\bmidnight\b'),
}


def _busy_history(now):
    """A history that lights up as many signals as it honestly can at `now` —
    including `back_soon`, which is the one that fires at any hour."""
    return [now - timedelta(minutes=10),
            now - timedelta(hours=3),
            now - timedelta(days=1),
            now - timedelta(days=2)]


@pytest.mark.parametrize("offset", range(7))
@pytest.mark.parametrize("hour", [2, 6, 8, 11, 14, 17, 19, 22, 23])
def test_no_greeting_ever_names_the_wrong_time_of_day(offset, hour):
    now = (_MON + timedelta(days=offset)).replace(hour=hour)
    bucket = time_bucket(now)
    # Through collect_signals, so genuinely hour-gated signals stay gated and
    # only the ones that really can fire at this hour are included.
    signals = collect_signals(_busy_history(now), now)

    for named, _anon in pool_for(now, signals):
        low = named.lower()
        for other, patterns in _TIME_WORDS.items():
            if other == bucket:
                continue
            offender = next((p for p in patterns if re.search(p, low)), None)
            assert offender is None, (
                f"{now:%A %H:%M} is {bucket!r}, but this line says {other!r}:\n"
                f"    {named}\n"
                f"  active signals: {sorted(signals)}")


@pytest.mark.parametrize("offset", range(7))
def test_night_pools_keep_day_identity(offset):
    when = (_MON + timedelta(days=offset)).replace(hour=23)
    pool = _named(pool_for(when))
    day_names = {named for named, _ in _DAY_LINES[greeting_weekday(when)]}

    assert pool & day_names, "the night pool dropped every day-specific line"
