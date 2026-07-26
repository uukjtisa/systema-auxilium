"""
tests/systema/updater/test_dev_marker.py

The updater refuses to auto-update a developer working copy — applying there
would overwrite local work with the repo. It decides by looking for a marker
file, and that marker used to be the maintainer's own tooling-notes file, which
meant the shipped source (and the update window's banner) named a private local
file to every user.

The marker is now `.dev-copy` — neutral, purpose-named, and the UI says only
WHAT was detected, never which file gave it away.
"""
import pytest

from systema.updater import service


def test_the_marker_is_neutral_and_purpose_named():
    assert service.DEV_MARKER == ".dev-copy"


def test_the_marker_names_no_tooling():
    """The point of the rename: nothing about the maintainer's local editor,
    notes file, or assistant belongs in a shipped constant."""
    assert "claude" not in service.DEV_MARKER.lower()


def test_a_copy_with_the_marker_is_a_dev_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "APP_ROOT", tmp_path)
    assert service.in_dev_environment() is False

    (tmp_path / service.DEV_MARKER).write_text("dev", encoding="utf-8")

    assert service.in_dev_environment() is True


def test_a_shipped_copy_is_not_a_dev_copy(tmp_path, monkeypatch):
    """A user's install has no marker, so auto-update stays enabled for them."""
    monkeypatch.setattr(service, "APP_ROOT", tmp_path)
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    assert service.in_dev_environment() is False


# Assembled at runtime, never written out as one literal. The repo is not
# supposed to contain that filename anywhere — including in the test that
# checks the repo does not contain it, which would otherwise be the last
# remaining copy (and a history rewrite would silently rewrite it).
_OLD_MARKER = "CLAUDE" + "." + "md"


def test_the_old_marker_no_longer_triggers_it(tmp_path, monkeypatch):
    """Guard rail on the rename — if the check silently still honoured the old
    filename, none of this would have achieved anything."""
    monkeypatch.setattr(service, "APP_ROOT", tmp_path)
    (tmp_path / _OLD_MARKER).write_text("notes", encoding="utf-8")
    assert service.in_dev_environment() is False


# ── nothing shipped may name the old marker ──────────────────────────────────

def _sources():
    from pathlib import Path
    import systema
    root = Path(systema.__file__).resolve().parent
    return sorted(root.rglob("*.py"))


def test_no_shipped_module_mentions_the_old_marker():
    offenders = []
    for path in _sources():
        if _OLD_MARKER in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(path.name)
    assert offenders == [], (
        f"these ship to users and still name the maintainer's local notes "
        f"file: {offenders}")
