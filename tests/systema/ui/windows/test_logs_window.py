"""
tests/systema/ui/windows/test_logs_window.py

The Logs window reads the `_run` stamps back off the loaded session and shows
which log files it spans — usually more than one, because the app restarts.

Offscreen-safe: per the suite's convention these check `isHidden()`, never
`isVisible()`.
"""
import inspect

import pytest

from systema.common.run_context import STAMP_KEY, StampedHistory

_LOG_A = "log_2026_jul_28_tuesday_h03_m38_s16_ms53_am.txt"
_LOG_B = "log_2026_jul_28_tuesday_h09_m01_s02_ms00_pm.txt"


def _stamped(role, content, logname, pid=100):
    return {'role': role, 'content': content,
            STAMP_KEY: {'log': logname, 'at': '03:39:28.683', 'pid': pid}}


class _AI:
    def __init__(self, history):
        self.conversation_history = history


class _SessionManager:
    def __init__(self, name):
        self.session_metadata = {'sid': {'name': name}}


class _Controller:
    def __init__(self, history, name="A Session"):
        self.ai = _AI(history)
        self.session_manager = _SessionManager(name)
        self.current_session_id = 'sid'
        self.settings = {}


@pytest.fixture
def window(qapp):
    from systema.ui.windows.logs_window import LogsWindow

    history = StampedHistory([
        _stamped('user', 'before restart', _LOG_A, pid=111),
        _stamped('assistant', 'ok', _LOG_A, pid=111),
        _stamped('user', 'after restart', _LOG_B, pid=222),
    ])
    w = LogsWindow(_Controller(history))
    yield w
    w.deleteLater()


def test_it_constructs_without_showing(window):
    assert window.isHidden()


def test_it_names_both_log_files_the_session_spans(window):
    labels = [window._list.item(i).text() for i in range(window._list.count())]
    flat = "\n".join(labels)

    assert _LOG_A in flat
    assert _LOG_B in flat


def test_the_subtitle_counts_the_files(window):
    assert "2 log files" in window._subtitle.text()


def test_entry_counts_and_pids_are_shown(window):
    labels = "\n".join(window._list.item(i).text()
                       for i in range(window._list.count()))

    assert "2 entries" in labels and "pid 111" in labels
    assert "1 entries" in labels and "pid 222" in labels


def test_a_session_with_no_stamps_says_so_rather_than_looking_broken(qapp):
    from systema.ui.windows.logs_window import LogsWindow

    w = LogsWindow(_Controller(StampedHistory()))
    try:
        assert "no log stamps" in w._subtitle.text()
    finally:
        w.deleteLater()


def test_the_tail_reader_never_loads_a_whole_large_file(tmp_path):
    from systema.ui.windows.logs_window import LogsWindow

    big = tmp_path / "big.txt"
    big.write_text("x" * 300_000, encoding="utf-8")

    out = LogsWindow._tail(big, tail_bytes=1024)

    assert len(out) < 2000, "must tail, not slurp"
    assert "showing the last" in out


def test_the_tail_reader_reports_a_missing_file_instead_of_raising(tmp_path):
    from systema.ui.windows.logs_window import LogsWindow

    out = LogsWindow._tail(tmp_path / "nope.txt")

    assert "Could not read" in out


def test_the_debug_window_no_longer_builds_a_maximize_button():
    """Removed 2026-07-28 at the user's request; guard against reintroduction.

    Checks for CONSTRUCTION (`MaximizeButton(`) rather than the bare name, so
    the comment explaining the removal doesn't fail the test that enforces it.
    """
    from systema.ui.windows import debug_window

    assert 'MaximizeButton(' not in inspect.getsource(debug_window)
