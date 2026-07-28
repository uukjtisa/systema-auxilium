"""
tests/systema/updater/test_resources_layout.py

`assets/` and `providers/` moved under `resources/` on 2026-07-28.

The move is only safe because of WHERE it went. `data/**` is in the updater's
_EXCLUDES and never appears in an update plan, so shipped content placed there
would stop reaching users permanently — the premade provider scripts would never
be delivered again, and an asset added by a future version would simply be
missing on updated installs. `resources/` keeps both in the tiers they belong to:

  * resources/providers/** — PROTECTED-visible: new scripts ship, but a change
    to one the user edited is never applied without an explicit opt-in.
  * resources/assets/**    — ordinary source: ships like any other file.

These tests lock that, and lock the two things a careless cleanup would break:
the retained pre-move glob (installs updating ACROSS the move) and the settings
adoption that repoints absolute provider paths.
"""
import pathlib

import pytest

from systema import APP_ROOT
from systema.updater.service import _EXCLUDES, is_sensitive_path


# ── the folders exist where the code now looks ───────────────────────────────

def test_resources_layout_exists_on_disk():
    assert (APP_ROOT / "resources" / "assets").is_dir()
    assert (APP_ROOT / "resources" / "providers" / "large-language-models").is_dir()
    assert (APP_ROOT / "resources" / "providers" / "text-to-speech").is_dir()
    assert not (APP_ROOT / "providers").exists(), "old providers/ still present"
    assert not (APP_ROOT / "assets").exists(), "old assets/ still present"


def test_app_icon_resolves():
    from systema.common.shortcuts import ICON_ICO
    assert ICON_ICO.is_file(), f"app icon missing at {ICON_ICO}"


# ── updater tiers: the whole reason resources/ was chosen over data/ ─────────

@pytest.mark.parametrize("relpath", [
    "resources/providers/large-language-models/provider_ollama.py",
    "resources/providers/text-to-speech/kokoro_tts_provider.py",
    "skills/pptx_skill/SKILL.md",
    # Retained so an install updating ACROSS the move still protects the
    # provider scripts sitting in its OLD layout.
    "providers/large-language-models/provider_ollama.py",
])
def test_user_owned_paths_are_protected(relpath):
    assert is_sensitive_path(relpath), f"{relpath} lost its PROTECTED tier"


@pytest.mark.parametrize("relpath", [
    "resources/assets/systema_auxilium.ico",
    "systema/app/controller.py",
    "main.py",
])
def test_shipped_source_is_not_protected(relpath):
    assert not is_sensitive_path(relpath), \
        f"{relpath} became PROTECTED — it would stop auto-updating"


def test_resources_is_not_excluded_from_updates():
    """The point of the move: resources/ must remain deliverable.

    If resources/ ever lands in _EXCLUDES it becomes invisible to the updater,
    which is exactly the failure that ruled out putting it under data/.
    """
    assert not any(pat.startswith("resources") for pat in _EXCLUDES), \
        f"resources/ is excluded from updates: {_EXCLUDES}"
    assert "data/**" in _EXCLUDES, "data/** must stay fully excluded"


# ── settings adoption: absolute paths survive the move ──────────────────────

def _adopt(settings: dict):
    """Run the adoption without constructing a controller.

    The stub carries the class attribute the method reads; building a real
    AssistantController here would boot the whole app.
    """
    from systema.app.controller import AssistantController

    class _Stub:
        _MOVED_PATH_KEYS = AssistantController._MOVED_PATH_KEYS

    return AssistantController._adopt_moved_provider_paths(_Stub(), settings)


def test_absolute_provider_path_is_repointed(monkeypatch, tmp_path):
    """An install updating across the move has an absolute path to the OLD
    location; leaving it alone silently unloads the user's provider."""
    real = (APP_ROOT / "resources" / "providers" / "large-language-models")
    script = next(real.glob("*.py"))
    stale = str(script).replace("resources\\providers", "providers") \
                       .replace("resources/providers", "providers")
    assert stale != str(script), "fixture failed to build a pre-move path"

    settings = {"custom_script_path": stale}
    saved = {}
    monkeypatch.setattr("systema.common.app_config.save_section",
                        lambda name, data: saved.update({name: data}))

    _adopt(settings)
    assert pathlib.Path(settings["custom_script_path"]) == script


def test_a_path_that_still_resolves_is_left_alone(monkeypatch):
    """Someone deliberately keeping scripts outside the app must not be
    rewritten out from under."""
    real = next((APP_ROOT / "resources" / "providers"
                 / "large-language-models").glob("*.py"))
    settings = {"custom_script_path": str(real)}
    monkeypatch.setattr("systema.common.app_config.save_section",
                        lambda name, data: None)

    _adopt(settings)
    assert settings["custom_script_path"] == str(real)


def test_missing_or_empty_paths_do_not_crash(monkeypatch):
    monkeypatch.setattr("systema.common.app_config.save_section",
                        lambda name, data: None)
    settings = {"custom_script_path": "", "tts_script_path": None}
    _adopt(settings)   # must not raise
    assert settings["custom_script_path"] == ""
