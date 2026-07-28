"""
tests/systema/common/test_run_context.py

Log cross-referencing. A session file says WHAT was rendered and a log file says
WHAT HAPPENED; nothing linked them, so diagnosing a bad card meant eyeballing
timestamps across several logs by hand.

Every history entry now carries a `_run` stamp naming the log file it was
written during. The app restarts often, so one session routinely spans several
log files — that is the case these tests exist to protect.
"""
import pytest

from systema.common import run_context as rc
from systema.common.run_context import STAMP_KEY, StampedHistory

_LOG_A = "log_2026_jul_28_tuesday_h03_m38_s16_ms53_am.txt"
_LOG_B = "log_2026_jul_28_tuesday_h09_m01_s02_ms00_pm.txt"


@pytest.fixture(autouse=True)
def restore_log_path():
    prev = rc.log_path()
    yield
    rc.set_log_path(prev)


# ── stamping ─────────────────────────────────────────────────────────────────

def test_an_appended_entry_records_log_time_and_pid():
    rc.set_log_path(f"data/logs/{_LOG_A}")
    h = StampedHistory()

    h.append({'role': 'user', 'content': 'hi'})

    stamp = h[0][STAMP_KEY]
    assert stamp['log'] == _LOG_A
    assert stamp['pid'] == rc.pid()
    # Matches the log's own line prefix so it greps the named file directly.
    assert len(stamp['at'].split(':')) == 3 and '.' in stamp['at']


def test_the_full_path_is_never_stored():
    """A session file is portable between the working dir and the repo copy; an
    absolute path recorded on one machine is misleading on another."""
    rc.set_log_path(f"C:/somewhere/else/data/logs/{_LOG_A}")
    h = StampedHistory()

    h.append({'role': 'user', 'content': 'hi'})

    assert h[0][STAMP_KEY]['log'] == _LOG_A
    assert 'somewhere' not in str(h[0][STAMP_KEY])


def test_an_entry_that_already_has_a_stamp_is_left_alone():
    rc.set_log_path(f"data/logs/{_LOG_B}")
    h = StampedHistory()

    h.append({'role': 'user', 'content': 'x', STAMP_KEY: {'log': _LOG_A, 'at': '01:02:03.004', 'pid': 1}})

    assert h[0][STAMP_KEY]['log'] == _LOG_A, "must not be overwritten with today's run"


def test_append_loaded_never_stamps():
    """Entries replayed from disk belong to the run that wrote them. Stamping
    them with today's log would be a confident lie about where to look."""
    rc.set_log_path(f"data/logs/{_LOG_A}")
    h = StampedHistory()

    h.append_loaded({'role': 'user', 'content': 'from an older run'})

    assert STAMP_KEY not in h[0]


def test_non_dict_entries_do_not_explode():
    rc.set_log_path(f"data/logs/{_LOG_A}")
    h = StampedHistory()

    h.append("a bare string")

    assert h[0] == "a bare string"


# ── the slice trap ───────────────────────────────────────────────────────────

def test_a_slice_stays_a_stamped_history():
    """`ui/chat/bubbles.py` rewinds by assigning `history[:n]` back over
    conversation_history. On a plain list subclass that returns a bare list, and
    every later append would silently lose its stamp."""
    rc.set_log_path(f"data/logs/{_LOG_A}")
    h = StampedHistory()
    h.append({'role': 'user', 'content': 'one'})
    h.append({'role': 'user', 'content': 'two'})

    rewound = h[:1]

    assert isinstance(rewound, StampedHistory)
    rewound.append({'role': 'user', 'content': 'three'})
    assert STAMP_KEY in rewound[1]


def test_constructing_from_existing_history_does_not_restamp():
    h = StampedHistory([{'role': 'user', 'content': 'x',
                         STAMP_KEY: {'log': _LOG_A, 'at': '01:02:03.004', 'pid': 1}},
                        {'role': 'user', 'content': 'unstamped'}])

    assert h[0][STAMP_KEY]['log'] == _LOG_A
    assert STAMP_KEY not in h[1], "list.__init__ must not route through append"


# ── the multi-log rollup ─────────────────────────────────────────────────────

def test_a_session_spanning_two_runs_reports_both_files_newest_first():
    rc.set_log_path(f"data/logs/{_LOG_A}")
    h = StampedHistory()
    h.append({'role': 'user', 'content': 'before the restart'})
    h.append({'role': 'assistant', 'content': 'ok'})

    rc.set_log_path(f"data/logs/{_LOG_B}")
    h.append({'role': 'user', 'content': 'after the restart'})

    spans = rc.logs_in(h)

    assert [r['log'] for r in spans] == [_LOG_B, _LOG_A]
    assert [r['entries'] for r in spans] == [1, 2]


def test_rollup_ignores_unstamped_and_malformed_entries():
    h = StampedHistory()
    h.append_loaded({'role': 'user', 'content': 'ancient, no stamp'})
    h.append_loaded({'role': 'user', 'content': 'x', STAMP_KEY: "not a dict"})
    h.append_loaded({'role': 'user', 'content': 'x', STAMP_KEY: {'log': ''}})
    h.append_loaded("a bare string")

    assert rc.logs_in(h) == []


def test_rollup_of_an_empty_history_is_empty():
    assert rc.logs_in([]) == []
    assert rc.logs_in(None) == []
