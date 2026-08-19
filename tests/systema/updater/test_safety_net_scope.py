"""The update safety net must never touch a DEVELOPER working copy.

THE BUG THIS EXISTS FOR
UpdaterService.apply() gained a git snapshot and an in-progress marker, both
unconditional. tests/systema/updater/test_apply_resolved.py drives apply()
against the real APP_ROOT -- so running the suite silently created a git repo in
the developer's working directory and committed 184 MB of it (a 46 MB Minecraft
server.jar, ~90 MB of skill runtime, and DesktopSbS/.git, another repo's
internals), then left an .apply-in-progress marker behind that made the NEXT
launch report a broken install.

A dev copy has its own version control and its files are meant to be mid-edit.
The safety net is for real installs.

WHAT THIS FILE MAY AND MAY NOT ASSERT
The first version asserted the ENVIRONMENT -- that APP_ROOT carries a .dev-copy
marker and has no .git. Both are true on the developer's machine and false in
CI, where the checkout IS a git repo and carries no marker, so the suite went
red the moment it left that machine. Environment facts are not code properties.
What survives here are the invariants that hold anywhere: the gate exists, it is
checked BEFORE the snapshot, and the ignore list covers what actually bloated
the accidental commit. Marker cleanup is enforced by an autouse fixture in
tests/conftest.py, which runs in every environment rather than asserting one.
"""
import inspect

from systema.updater import local_git
from systema.updater.service import UpdaterService


def test_apply_checks_the_dev_marker_before_snapshotting():
    src = inspect.getsource(UpdaterService.apply)
    assert "in_dev_environment" in src, "the dev gate is gone"
    assert src.index("in_dev_environment") < src.index("local_git.snapshot"), (
        "the dev check must come BEFORE the snapshot, not after it")


def test_apply_gates_the_marker_on_the_same_check():
    """Snapshot and marker are one decision -- gating only one of them leaves
    the other writing into a developer's tree."""
    src = inspect.getsource(UpdaterService.apply)
    assert src.index("in_dev_environment") < src.index("mark_apply_started")


def test_the_gitignore_excludes_what_actually_bloated_it():
    """Each of these was measured in the accidental 184 MB commit."""
    ignore = local_git.GITIGNORE
    for pattern in ("**/.git/", "*.jar", "data/temp/", "**/server/world/",
                    "**/site-packages/", "data/settings.json"):
        assert pattern in ignore, f"{pattern} must stay ignored"


def test_the_gitignore_still_covers_secrets_and_runtime():
    ignore = local_git.GITIGNORE
    for pattern in ("data/logs/", "data/sessions/", "data/memory/",
                    "data/updates/", "__pycache__/"):
        assert pattern in ignore


def test_init_repo_is_a_no_op_without_git(monkeypatch, tmp_path):
    """No git installed means no safety net, never a crash."""
    monkeypatch.setattr(local_git, "_git", lambda: None)
    assert local_git.available() is False
    assert local_git.init_repo(tmp_path) is False
    assert local_git.snapshot("x", tmp_path) is None
    assert local_git.revert_to("abc", tmp_path) is False
