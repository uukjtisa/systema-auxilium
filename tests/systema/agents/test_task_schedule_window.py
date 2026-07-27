"""
tests/systema/agents/test_task_schedule_window.py

Daily-schedule windows, including the ones that cross midnight.

THE BUG THESE EXIST FOR
A task scheduled 22:00 -> 01:00 (a sleep reminder — the shape most likely to
wrap) went quiet the moment the date rolled over, and a restart appeared to fix
it. Three defects compounded:

  1. `_in_window` was `start <= now <= end`, which cannot express a wrapping
     window: at 23:00 it evaluates 23:00 <= 01:00, which is False.
  2. `_next_schedule_relative_seconds` anchored to TODAY's start time, so at
     00:30 it measured to 22:00 *tonight* and slept ~21 hours.
  3. The run loop treated any date change as "the day ended", ending a window
     that was still open.

Driven against the real methods with a stub `self` — TaskThread is a QThread
with a controller, and none of that is needed to test the arithmetic.
"""
import math
from datetime import datetime, timedelta

import pytest

from systema.agents.task_manager import TaskThread


def _thread(start, end, interval_minutes=15, whole_day=False):
    class _T:
        _task = {
            'daily_schedule': ({'whole_day': True} if whole_day
                               else {'start': start, 'end': end}),
            'interval_minutes': interval_minutes,
        }
        _window_times = TaskThread._window_times
        _window_wraps = TaskThread._window_wraps
        _in_window = TaskThread._in_window
        _window_started_at = TaskThread._window_started_at
        _next_schedule_relative_seconds = TaskThread._next_schedule_relative_seconds
    return _T()


MON = datetime(2026, 7, 27)          # a Monday


# ── ordinary windows still behave ────────────────────────────────────────────

@pytest.mark.parametrize("hour,inside", [
    (3, False), (4, True), (12, True), (19, True), (20, False),
])
def test_a_same_day_window_is_unchanged(hour, inside):
    t = _thread('04:00', '19:00')
    assert t._window_wraps() is False
    assert t._in_window(MON.replace(hour=hour)) is inside


def test_a_whole_day_task_is_always_in_window():
    t = _thread(None, None, whole_day=True)
    for hour in range(24):
        assert t._in_window(MON.replace(hour=hour)) is True


# ── the wrapping window ──────────────────────────────────────────────────────

@pytest.mark.parametrize("hour,minute,inside", [
    (21, 30, False),     # before it opens
    (22, 0, True),       # opens
    (23, 0, True),       # <-- was False: 23:00 <= 01:00 is not true
    (23, 59, True),
    (0, 0, True),        # <-- midnight, where the reminders used to stop
    (0, 30, True),
    (1, 0, True),        # closes
    (1, 1, False),
    (12, 0, False),
])
def test_a_window_that_crosses_midnight_stays_open(hour, minute, inside):
    t = _thread('22:00', '01:00')
    assert t._window_wraps() is True
    assert t._in_window(MON.replace(hour=hour, minute=minute)) is inside


def test_the_after_midnight_half_belongs_to_yesterdays_window():
    """The anchor for interval alignment. Getting this wrong is what produced
    the ~21 hour sleep."""
    t = _thread('22:00', '01:00')

    before = t._window_started_at(MON.replace(hour=23, minute=0))
    after = t._window_started_at(MON.replace(hour=0, minute=30))

    assert before == MON.replace(hour=22)
    assert after == (MON - timedelta(days=1)).replace(hour=22)


def test_the_window_is_closed_between_occurrences():
    t = _thread('22:00', '01:00')
    assert t._window_started_at(MON.replace(hour=12)) is None


def test_pings_keep_their_cadence_across_midnight():
    """THE regression. Every wait must stay within one interval — never the
    ~21 hours the old anchor produced at 00:01."""
    t = _thread('22:00', '01:00', interval_minutes=15)

    for hour, minute in ((22, 0), (23, 50), (0, 1), (0, 30), (0, 55)):
        now = MON.replace(hour=hour, minute=minute)
        started = t._window_started_at(now)
        assert started is not None

        elapsed = (now - started).total_seconds()
        nxt = started + timedelta(seconds=(math.floor(elapsed / 900) + 1) * 900)
        wait_min = (nxt - now).total_seconds() / 60

        assert 0 < wait_min <= 15, (
            f"at {hour:02d}:{minute:02d} the next ping was {wait_min:.0f} min away")


def test_the_live_wait_never_exceeds_one_interval_inside_the_window(monkeypatch):
    """_next_schedule_relative_seconds reads the wall clock, so pin it and
    drive the real method — this is the function that produced the 21-hour
    sleep."""
    import systema.agents.task_manager as tm

    t = _thread('22:00', '01:00', interval_minutes=15)

    class _FixedNow(datetime):
        _fixed = MON

        @classmethod
        def now(cls, tz=None):
            return cls._fixed

    monkeypatch.setattr(tm, 'datetime', _FixedNow)

    for hour, minute in ((22, 0), (23, 50), (0, 1), (0, 30), (0, 55)):
        _FixedNow._fixed = MON.replace(hour=hour, minute=minute)
        wait_min = t._next_schedule_relative_seconds() / 60
        assert 0 <= wait_min <= 15, (
            f"at {hour:02d}:{minute:02d} the next ping was {wait_min:.0f} min away")


def test_an_unparseable_schedule_keeps_the_task_running():
    """A task that cannot read its own window must not go silent."""
    t = _thread('not-a-time', 'nonsense')
    assert t._window_times() is None
    assert t._in_window(MON.replace(hour=3)) is True
