"""
Tests for the inline conflict editor (ReviewPane) in
systema/ui/windows/update_window.py — the replacement for the removed Manage
dialog. Per-hunk take/keep drives the assembled text; Confirm emits the path.
Only needs a palette dict + a colour resolver, so no full UpdateWindow.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from systema.updater.hunks import ReviewSession                # noqa: E402
from systema.ui.windows.update_window import ReviewPane, _CHANGE_STYLE  # noqa: E402

_PALETTE = {
    "bg": "#0D1117", "surface": "#161B22", "surface2": "#21262D",
    "border": "#30363D", "accent": "#58A6FF", "text": "#E6EDF3", "muted": "#8B949E",
}

_TAGGED = [
    ("same", "a\n"),
    ("conflict_local", "mine\n"),
    ("conflict_base", "base\n"),
    ("conflict_remote", "theirs\n"),
    ("same", "b\n"),
]


def _pane_with_conflict(qapp, sensitive=False):
    s = ReviewSession()
    s.add("engine/x.py", _TAGGED, sensitive=sensitive)
    fr = s.files["engine/x.py"]
    pane = ReviewPane(_PALETTE, lambda k: _PALETTE.get(k, k))
    pane.edit_conflict("engine/x.py", fr)
    qapp.processEvents()
    return pane, fr


def test_decided_change_style_exists():
    assert "decided" in _CHANGE_STYLE


def test_take_update_radio_sets_decision(qapp):
    pane, fr = _pane_with_conflict(qapp, sensitive=False)
    pane.take_radio.setChecked(True)
    qapp.processEvents()
    assert fr.hunks[0].decision == "update"
    assert "theirs" in fr.assembled()


def test_keep_mine_radio_sets_decision(qapp):
    pane, fr = _pane_with_conflict(qapp, sensitive=False)
    pane.keep_radio.setChecked(True)
    qapp.processEvents()
    assert fr.hunks[0].decision == "local"
    assert "mine" in fr.assembled()
    assert "theirs" not in fr.assembled()


def test_take_all_and_keep_all(qapp):
    pane, fr = _pane_with_conflict(qapp, sensitive=True)   # starts on local
    pane._set_all("update")
    assert all(h.decision == "update" for h in fr.hunks if h.kind == "conflict")
    pane._set_all("local")
    assert all(h.decision == "local" for h in fr.hunks if h.kind == "conflict")


def test_confirm_emits_path(qapp):
    pane, fr = _pane_with_conflict(qapp, sensitive=False)
    seen = []
    pane.confirmed.connect(seen.append)
    pane._on_confirm()
    assert seen == ["engine/x.py"]


def test_protected_starts_on_keep_mine(qapp):
    pane, fr = _pane_with_conflict(qapp, sensitive=True)
    # The protected default keeps local — the radio reflects it.
    assert fr.hunks[0].decision == "local"
    assert pane.keep_radio.isChecked()
