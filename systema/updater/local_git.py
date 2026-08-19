"""
systema/updater/local_git.py

A real git repo in APP_ROOT, used as the updater's primary rollback mechanism.

WHY, when gitplucker already has snapshots
------------------------------------------
gitplucker's snapshots live under `data/updates/`, and so do the backups, the
settled store and the merge baseline. `data/**` is excluded from updates, but
nothing protects it from the USER -- it is the directory you delete to "reset
the app". Losing it loses every recovery path at once. Worse, the only way to
reach `rollback()` is Settings > System, which requires the app to launch and
the chat window to open: the one scenario rollback exists for is the scenario
where you cannot get to it.

A git repo in the working copy answers both. It survives a `data/` wipe, and
`systema/startup/recovery.py` can drive it BEFORE the controller is built.

NOT the same thing as file_journal's shadow repo
------------------------------------------------
`systema/execution/file_journal.py` keeps a SHADOW repo under `data/` that
mirrors individual files the agent edited, for per-edit undo. This one versions
the app's own source, for update rollback. Different lifetimes, different
contents, different failure modes -- do not merge them.

EVERYTHING HERE IS OPTIONAL. Git may not be installed, and the app must work
identically without it, just without the safety net. Every function returns a
falsy value rather than raising, and callers are expected to carry on.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from systema import APP_ROOT
from systema.common.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("LocalGit") if _verbose else _NoOpLogger()

# Quiet subprocesses -- a console flashing on every snapshot was a reported bug
# class in this app, so nothing here may spawn a visible window.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TIMEOUT = 120

# Identity for the snapshot commits. Local-only; never pushed anywhere.
_USER_NAME = "Systema Auxilium"
_USER_EMAIL = "systema@local"

# What the safety net covers, decided by what an UPDATE can damage plus what the
# user authors by hand. The heavy, regenerable, or machine-specific trees are
# excluded: they would make every pre-apply snapshot slow for no recovery value.
#
#   tracked   systema/ tests/ scripts/ resources/ docs/ main.py requirements.txt
#             skills/ (minus vendored venvs), data/templates, data/*.json configs,
#             and the side projects that live in the working copy
#   ignored   data/ runtime (logs, sessions, memory, updates, crash dumps),
#             settings.json (machine-specific + secrets), caches, venvs,
#             node_modules, and the 887-entry vendored venv in skills/mc_server
GITIGNORE = """\
# Written by systema/updater/local_git.py -- the update-rollback safety net.
# Tracked: app source, skills, hand-authored data. Ignored: runtime state,
# secrets, caches and anything regenerable.

# Caches and build artefacts
__pycache__/
*.pyc
*.pyo
*.egg-info/
build/
dist/
.pytest_cache/

# Environments (including the ones vendored inside skills)
.venv/
venv/
env/
**/.venv/
**/site-packages/
node_modules/

# Updater state -- gitplucker owns this, and it is what git is a backup FOR
.gitplucker/
data/updates/

# NESTED REPOSITORIES. Committing another repo's .git internals is never right,
# and DesktopSbS/.git alone was 17 MB of it.
**/.git/

# Downloaded RUNTIME that a skill fetches for itself. A skill's code is worth
# versioning; the Minecraft server it downloads is not -- server.jar alone is
# 46 MB, and skills/mc_server totalled ~90 MB of jars, libraries and world
# region files. Restoring the app never needs any of it.
*.jar
**/server/libraries/
**/server/versions/
**/server/world/
**/*.mca

# Scratch downloads (mp3s, yt audio) -- 25 MB of pure churn
data/temp/

# Runtime state: regenerated, huge, and churns every second
data/logs/
data/sessions/
data/task_sessions/
data/memory/
data/crash_dumps/
data/perf_reports/
data/file_history/
data/cache/
data/*.log

# Machine-specific configuration and secrets -- never version these
data/settings.json
settings.json
assistant_settings.json
chat_config.json
floating_window_config.json
*.lock
.dev-copy

# Scratch output
output/
screenshots/
"""


def _git() -> str | None:
    """Absolute path to git, or None when it is not installed."""
    return shutil.which("git")


def available() -> bool:
    """True when git can be used at all. Callers must tolerate False."""
    return _git() is not None


def _run(args, root=None, check=False, timeout=_TIMEOUT):
    """Run one git command quietly. Returns CompletedProcess, or None on failure."""
    exe = _git()
    if exe is None:
        return None
    try:
        return subprocess.run(
            [exe, *args], cwd=str(root or APP_ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=check, timeout=timeout, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(f"[local_git._run] git {' '.join(args[:2])}: "
                    f"{type(e).__name__}: {e}")
        return None


def is_repo(root=None) -> bool:
    return (Path(root or APP_ROOT) / ".git").exists()


def write_gitignore(root=None, overwrite: bool = False) -> bool:
    """Install the .gitignore. Never clobbers an existing one unless asked --
    the user may have added their own rules."""
    path = Path(root or APP_ROOT) / ".gitignore"
    if path.exists() and not overwrite:
        return False
    try:
        path.write_text(GITIGNORE, encoding="utf-8")
        return True
    except OSError as e:
        log.warning(f"[local_git.write_gitignore] {e}")
        return False


def init_repo(root=None) -> bool:
    """One-time init. Idempotent, and a no-op without git.

    Deliberately does NOT create the first commit here: the initial `git add`
    over the whole working copy is slow, and doing it inside startup would put
    it in front of the window appearing. `snapshot()` makes the first commit on
    the first apply, which is the moment it actually matters.
    """
    root = Path(root or APP_ROOT)
    if not available():
        log.info("[local_git.init_repo] git not installed -- no safety net")
        return False
    if is_repo(root):
        write_gitignore(root)          # migration: repo exists, ignore file lost
        return True
    log.info(f"[local_git.init_repo] initialising {root}")
    write_gitignore(root)
    if _run(["init", "-q"], root) is None:
        return False
    _run(["config", "user.name", _USER_NAME], root)
    _run(["config", "user.email", _USER_EMAIL], root)
    # Never sign these: a machine without a configured key would fail every
    # snapshot, i.e. lose the safety net exactly when it is needed.
    _run(["config", "commit.gpgsign", "false"], root)
    return is_repo(root)


def is_clean(root=None) -> bool:
    """True when nothing tracked has been modified. False also when unknown."""
    proc = _run(["status", "--porcelain"], root)
    return bool(proc and proc.returncode == 0 and not proc.stdout.strip())


def snapshot(message: str, root=None) -> str | None:
    """Commit the current working copy. Returns the short sha, or None.

    Called BEFORE every update apply, so a broken apply always has a known-good
    commit to come back to. Returns the EXISTING head when there is nothing to
    commit -- an unchanged tree is still a valid restore point, and reporting
    None there would read as failure.
    """
    if not available():
        return None
    if not is_repo(root) and not init_repo(root):
        return None
    if _run(["add", "-A"], root) is None:
        return None
    proc = _run(["commit", "-q", "-m", message, "--no-verify"], root)
    if proc is None:
        return None
    if proc.returncode != 0 and "nothing to commit" not in (
            proc.stdout + proc.stderr).lower():
        log.warning(f"[local_git.snapshot] commit failed: "
                    f"{(proc.stderr or proc.stdout).strip()[:200]}")
        return None
    sha = head(root)
    log.info(f"[local_git.snapshot] {sha} -- {message}")
    return sha


def head(root=None) -> str | None:
    proc = _run(["rev-parse", "--short", "HEAD"], root)
    if proc and proc.returncode == 0:
        return proc.stdout.strip() or None
    return None


def recent_commits(limit: int = 20, root=None) -> list:
    """[(sha, iso_date, subject), ...], newest first. [] when unavailable."""
    proc = _run(["log", f"-{int(limit)}", "--pretty=%h%x1f%cI%x1f%s"], root)
    if not proc or proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            out.append((parts[0], parts[1], parts[2]))
    return out


def revert_to(sha: str, root=None) -> bool:
    """Restore the working copy to ``sha``.

    Uses `checkout -- .` after a hard reset so files the bad apply ADDED are
    also cleaned, but deliberately NOT `clean -xdf`: that would delete ignored
    paths, which here means the user's sessions, memory and settings. Recovering
    from a bad update must never cost the user their data.
    """
    if not available() or not is_repo(root):
        return False
    sha = str(sha or "").strip()
    if not sha:
        return False
    log.info(f"[local_git.revert_to] resetting working copy to {sha}")
    proc = _run(["reset", "--hard", sha], root)
    if proc is None or proc.returncode != 0:
        log.error(f"[local_git.revert_to] reset failed: "
                  f"{(proc.stderr if proc else 'no git')}")
        return False
    # Remove UNTRACKED files the bad apply introduced, but only those git would
    # otherwise leave behind -- -x is omitted on purpose so ignored user data
    # (sessions, memory, settings) is untouched.
    _run(["clean", "-df"], root)
    return True


def verify(root=None) -> bool:
    """Cheap integrity probe on the repo itself (not the working copy)."""
    proc = _run(["rev-parse", "--is-inside-work-tree"], root)
    return bool(proc and proc.returncode == 0
                and proc.stdout.strip() == "true")


def status_summary(root=None) -> dict:
    """Everything the recovery dialog needs, in one call."""
    return {
        "available": available(),
        "is_repo": is_repo(root),
        "clean": is_clean(root),
        "head": head(root),
        "commits": recent_commits(10, root),
    }
