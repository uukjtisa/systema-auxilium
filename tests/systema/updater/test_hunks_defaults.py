"""
Tests for the conflict-resolution defaults in systema/updater/hunks.py.

The 2026-07-20 update-window rework made conflict hunks default to TAKING the
update everywhere EXCEPT protected files (providers/skills), where they default
to keeping local so live keys aren't wiped. Dependency-free (pure stdlib).
"""
from systema.updater.hunks import ReviewSession


# A tagged three-way review with one conflict block (local vs remote).
_TAGGED = [
    ("same", "line 1\n"),
    ("conflict_local", "MY_KEY = 'local-secret'\n"),
    ("conflict_base", "MY_KEY = 'base'\n"),
    ("conflict_remote", "MY_KEY = 'upstream-placeholder'\n"),
    ("same", "line 3\n"),
]


def _conflict_hunk(fr):
    return next(h for h in fr.hunks if h.kind == "conflict")


def test_normal_conflict_defaults_to_take_update():
    s = ReviewSession()
    s.add("engine/core.py", _TAGGED, sensitive=False)
    h = _conflict_hunk(s.files["engine/core.py"])
    assert h.decision == "update"
    assert "upstream-placeholder" in s.files["engine/core.py"].assembled()


def test_protected_conflict_defaults_to_keep_mine():
    s = ReviewSession()
    s.add("providers/openai.py", _TAGGED, sensitive=True)
    h = _conflict_hunk(s.files["providers/openai.py"])
    assert h.decision == "local"
    # Keeping local preserves the user's live secret, not the placeholder.
    assembled = s.files["providers/openai.py"].assembled()
    assert "local-secret" in assembled
    assert "upstream-placeholder" not in assembled


def test_take_update_then_assemble_uses_remote_side():
    s = ReviewSession()
    s.add("providers/openai.py", _TAGGED, sensitive=True)
    h = _conflict_hunk(s.files["providers/openai.py"])
    h.decision = "update"          # user explicitly takes the update
    assert "upstream-placeholder" in s.files["providers/openai.py"].assembled()
