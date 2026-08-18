"""
tests/systema/common/test_desktop_dir_cost.py

`desktop_dir()` must not spawn a process, and must not be recomputed.

It used to run `powershell -NoProfile -Command
[Environment]::GetFolderPath('Desktop')` on EVERY call, from whichever thread
asked — and `settings_window.__init__` asks during a click. PowerShell start-up
is a few hundred ms cold, and it produced the worst stall on record: a 3226 ms
freeze opening Settings (perf report 2026-08-18_20-15-36).

The Desktop location cannot change while the app runs, so the answer is cached;
on Windows it comes from the same shell-folder registry key Explorer uses, which
honours a OneDrive redirect exactly like the PowerShell call did.
"""
import subprocess
import sys

import pytest

from systema.common import shortcuts


def test_it_is_cached():
    assert hasattr(shortcuts.desktop_dir, "cache_info"), (
        "desktop_dir must be memoised — it was re-resolved on every call")
    shortcuts.desktop_dir.cache_clear()
    first = shortcuts.desktop_dir()
    second = shortcuts.desktop_dir()
    assert first == second
    assert shortcuts.desktop_dir.cache_info().hits >= 1


def test_it_does_not_spawn_a_subprocess(monkeypatch):
    """The cache alone is not enough: the FIRST call also lands on a GUI thread."""
    shortcuts.desktop_dir.cache_clear()

    def explode(*a, **k):
        raise AssertionError(f"desktop_dir spawned a subprocess: {a[:1]}")

    monkeypatch.setattr(subprocess, "run", explode)
    try:
        assert shortcuts.desktop_dir().name
    finally:
        shortcuts.desktop_dir.cache_clear()


@pytest.mark.skipif(sys.platform != "win32", reason="registry lookup is Windows-only")
def test_the_registry_lookup_agrees_with_the_resolved_path():
    shortcuts.desktop_dir.cache_clear()
    from_registry = shortcuts._win_desktop_from_registry()
    assert from_registry is not None, "the shell-folder key should always exist"
    assert shortcuts.desktop_dir() == from_registry
    shortcuts.desktop_dir.cache_clear()


@pytest.mark.skipif(sys.platform != "win32", reason="registry lookup is Windows-only")
def test_a_broken_registry_read_falls_back_instead_of_raising(monkeypatch):
    monkeypatch.setattr(shortcuts, "_win_desktop_from_registry", lambda: None)
    shortcuts.desktop_dir.cache_clear()
    try:
        assert shortcuts.desktop_dir().name        # PowerShell / USERPROFILE path
    finally:
        shortcuts.desktop_dir.cache_clear()
