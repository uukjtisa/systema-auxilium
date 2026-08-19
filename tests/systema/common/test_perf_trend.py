"""The hitch-report degradation verdict.

Guards the instrument that answers "does the UI get worse the longer it runs".
It was reported for weeks with nothing behind it, and the first version of this
verdict called a HEALTHY run degrading because hitch rate rose while stall
duration fell. These tests exist so that specific wrong answer cannot come back.
"""
from systema.common.perf_monitor import HitchMonitor


def _trend(events, up_s):
    m = HitchMonitor.__new__(HitchMonitor)
    m._events = list(events)
    return "\n".join(m._trend_lines(up_s))


def test_short_run_refuses_to_guess():
    out = _trend([(5, 700), (10, 5600)], 200)
    assert "needs" in out and "uptime to compare" in out
    assert "Verdict" not in out


def test_flat_run_is_not_degradation():
    flat = [(t, 300) for t in range(70, 2100, 90)]
    assert "no degradation with uptime" in _trend(flat, 2100)


def test_rate_and_duration_both_rising_is_degradation():
    deg = ([(t, 200) for t in range(70, 660, 100)]
           + [(t, 900) for t in range(1500, 2100, 40)])
    assert "DEGRADING with uptime" in _trend(deg, 2100)


def test_more_but_shorter_hitches_is_not_degradation():
    """The 2026-08-19 shape: rate up, mean stall down. That is a user
    interacting, not an app rotting — it must never read as DEGRADING."""
    ev = ([(70 + i * 60, 2000) for i in range(4)]          # early: rare, long
          + [(1500 + i * 20, 250) for i in range(12)])      # late: frequent, short
    out = _trend(ev, 2100)
    assert "DEGRADING" not in out
    assert "MIXED" in out


def test_startup_warmup_is_excluded_from_the_early_baseline():
    """Cold-import hitches in the first minute are a fixed startup tax. Counting
    them as the early baseline hides real decay behind a huge early mean."""
    ev = ([(5, 5600), (10, 5100), (20, 1100)]               # startup tax
          + [(t, 200) for t in range(70, 660, 100)]         # early, calm
          + [(t, 900) for t in range(1500, 2100, 40)])      # late, bad
    out = _trend(ev, 2100)
    assert "excl. first 60s warm-up" in out
    assert "DEGRADING with uptime" in out


def test_uptime_is_formatted_as_hms():
    assert HitchMonitor._fmt_uptime(3725) == "01:02:05"
    assert HitchMonitor._fmt_uptime(-5) == "00:00:00"
