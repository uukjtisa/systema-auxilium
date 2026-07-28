"""
tests/systema/common/test_hot_reload.py

Hot reload used to refuse any module that was not already in `sys.modules`,
reporting "was it ever imported, or is the path wrong?". Several windows are
imported LAZILY the first time they are opened (settings_window at
floating_window.py:655), so reloading Settings in a run where you had never
opened it failed on a perfectly correct path.

Importing it is the reload — it reads the current file from disk, which is the
entire point of the button.
"""
import sys

from systema.common.hot_reload import reload_module


def test_a_loaded_module_reloads():
    ok, msg = reload_module("systema.common.token_est")

    assert ok, msg
    assert "Reloaded" in msg


def test_a_not_yet_imported_module_is_loaded_rather_than_refused(monkeypatch):
    """The regression: a lazily-imported window in a run that never opened it."""
    target = "systema.common.token_est"
    real = sys.modules[target]
    monkeypatch.delitem(sys.modules, target, raising=False)

    try:
        ok, msg = reload_module(target)
        assert ok, msg
        assert "was not imported yet" in msg
        assert target in sys.modules
    finally:
        sys.modules[target] = real


def test_a_genuinely_wrong_path_still_fails_clearly():
    ok, msg = reload_module("systema.this_module_does_not_exist")

    assert not ok
    assert "Failed to import" in msg
    assert "ModuleNotFoundError" in msg


def test_a_module_that_raises_on_import_reports_the_traceback(monkeypatch, tmp_path):
    """A broken edit must surface its traceback, not a bare False."""
    broken = tmp_path / "systema_broken_probe.py"
    broken.write_text("raise ValueError('deliberate')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    ok, msg = reload_module("systema_broken_probe")

    assert not ok
    assert "ValueError" in msg and "deliberate" in msg
