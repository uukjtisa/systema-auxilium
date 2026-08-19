"""Which paths under data/ an update is allowed to touch.

THREE TIERS, and the difference matters:

  EXCLUDED   never appears in a plan. Runtime state and secrets.
  PROTECTED  appears, UNTICKED, needs explicit opt-in. Files the app ships AND
             the user edits.
  NORMAL     appears and is auto-ticked. Ordinary source.

data/ used to be blanket-excluded, which put templates and instruction presets
in the wrong tier: the app ships them, so an upstream fix should be offerable,
but it could never be delivered at all -- the redacted email template had to be
force-added by hand. They are protected now.

The exclude list is an ENUMERATION, which a new data/ subdirectory can out-run,
so the last test here fails the moment one appears unclassified. That is the
whole safety story for this change: getting it wrong exposes sessions or
secrets to an overwrite.
"""
from pathlib import Path

import pytest

from systema import APP_ROOT
from systema.updater import service as svc


# -- the promoted paths ------------------------------------------------------

@pytest.mark.parametrize("path", [
    "data/templates/system_state_email.html",
    "data/templates/anything.html",
    "data/instruction_presets.json",
])
def test_promoted_paths_are_protected_not_excluded(path):
    assert svc.is_sensitive_path(path), f"{path} should be protected"
    assert not _is_excluded(path), f"{path} must be VISIBLE in a plan"


# -- what must never be touched ---------------------------------------------

@pytest.mark.parametrize("path", [
    "data/sessions/whatever.json",
    "data/memories/store.json",
    "data/settings.json",
    "data/security/keys.json",
    "data/logs/run.log",
    "data/updates/state.json",
    "data/tasks/task.json",
    "data/task-sessions/s.json",
    "data/temp/audio.mp3",
    "data/cache/x.bin",
    "data/received/file.bin",
    "data/token_usage.json",
    "data/api_requests.json",
])
def test_runtime_and_secrets_stay_excluded(path):
    assert _is_excluded(path), f"{path} must never appear in an update plan"


def _is_excluded(rel: str) -> bool:
    from gitplucker.fsutil import glob_match
    return glob_match(rel, svc._EXCLUDES)


# -- the tiers still cover everything ---------------------------------------

def test_every_data_entry_is_classified():
    """The guard. An enumeration is only safe while it is complete -- a new
    data/ subdirectory that nobody classified would silently become updatable,
    and if it held sessions or keys an update could overwrite them."""
    data = Path(APP_ROOT) / "data"
    if not data.is_dir():
        pytest.skip("no data/ directory in this checkout")
    known = (set(svc._DATA_RUNTIME_DIRS) | set(svc._DATA_RUNTIME_FILES)
             | set(svc._DATA_UPDATABLE))
    found = {e.name for e in data.iterdir()}
    unclassified = found - known
    assert not unclassified, (
        f"unclassified entries under data/: {sorted(unclassified)}. Add each to "
        f"_DATA_RUNTIME_DIRS/_DATA_RUNTIME_FILES (and _EXCLUDES) if it is "
        f"runtime state, or to _DATA_UPDATABLE (and _SENSITIVE_GLOBS) if an "
        f"update should be able to offer it.")


def test_the_runtime_lists_and_the_excludes_agree():
    """Every runtime entry named in the docs list is actually excluded."""
    for name in svc._DATA_RUNTIME_DIRS:
        assert _is_excluded(f"data/{name}/anything.txt"), f"data/{name} not excluded"
    for name in svc._DATA_RUNTIME_FILES:
        if name.endswith(".lock"):
            continue          # covered by the *.lock pattern
        assert _is_excluded(f"data/{name}"), f"data/{name} not excluded"


def test_the_updatable_list_and_the_sensitive_globs_agree():
    for name in svc._DATA_UPDATABLE:
        probe = f"data/{name}/x" if "." not in name else f"data/{name}"
        assert svc.is_sensitive_path(probe), f"data/{name} is not protected"


def test_source_is_still_ordinary():
    """Promoting data/ paths must not have made real source protected."""
    assert not svc.is_sensitive_path("systema/app/controller.py")
    assert not _is_excluded("systema/app/controller.py")
