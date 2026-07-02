"""
systema/app/updater_service.py
UpdaterService — self-update for Systema Auxilium via the gitplucker library.

Wraps the `gitplucker` updater (github.com/uukjtisa/updater-gitplucker) and runs
check/apply on QThread workers so the GUI never blocks. The controller owns one
instance (created in `_deferred_bg_init`); the Settings ▸ System tab opens an
`UpdateWindow` that drives it via the signals below.

Design notes
------------
- **install_root = APP_ROOT** — the running app updates itself in place.
- **Channel = python-source** — 3-way merge preserves the user's local edits to
  tracked files (conflicts are marked), plus new Python deps are auto-installed.
- **User data is excluded** (`data/**`, `assistant_settings.json`, venv, caches)
  so logs/sessions/memory/settings are never overwritten or deleted.
- Update state + pre-apply backups live under `data/updates/` (itself excluded),
  so a bad update can be rolled back by restoring from there.
"""

from __future__ import annotations

import os
import traceback

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from systema import APP_ROOT
from systema.common.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("UpdaterService") if _verbose else _NoOpLogger()

# The repo this app updates itself from (allowlisted — nothing else is fetched).
REPO = "uukjtisa/systema-auxilium"
DEFAULT_BRANCH = "main"

# Update state / backups live here (kept out of the update payload via excludes).
_STATE_DIR = APP_ROOT / "data" / "updates"

# Paths never touched by an update (user data + runtime + build cruft).
# data/** already covers everything the user configures under data/ — tasks,
# task-sessions, sessions, memory, logs, the updater's own state — so those are
# fully protected and never even appear in an update plan.
_EXCLUDES = [
    "**/.git/**", "**/__pycache__/**", "**/*.pyc", "**/.gitplucker/**",
    ".venv/**", "**/.venv/**", "venv/**", "**/node_modules/**",
    "data/**",                       # logs, sessions, memory, tasks, updates, lock file
    "assistant_settings.json",       # the user's own configuration
    "*.lock", "**/*.lock",
]

# Paths that hold user-configured secrets / accounts (e.g. API keys inside
# provider scripts). Unlike _EXCLUDES these STAY VISIBLE in the plan so the user
# can review a genuine upstream change — but they are auto-unselected, flagged
# "PROTECTED", and applying one takes an explicit opt-in + a strong warning, so
# an update can never silently overwrite or delete a configured account.
_SENSITIVE_GLOBS = [
    "providers/**",
]


def is_sensitive_path(relpath: str) -> bool:
    """True if ``relpath`` holds user-configured secrets/accounts (providers)."""
    rp = str(relpath).replace("\\", "/")
    try:
        from gitplucker.fsutil import glob_match
        return glob_match(rp, _SENSITIVE_GLOBS)
    except Exception:
        return rp.startswith("providers/")


def make_updater(branch: str = DEFAULT_BRANCH, token: str | None = None,
                 auto_install_deps: bool = True, on_event=None):
    """Build a configured gitplucker Updater for Systema Auxilium.

    Standalone (no controller / GUI needed) so scripts and the self-knowledge
    skill can reuse the exact same config, allowlist and excludes as the app.
    """
    from gitplucker import (Channel, RepoSubscription, Updater, UpdaterConfig)

    cfg = UpdaterConfig(
        install_root=APP_ROOT,
        allowed_repos=[REPO],
        subscriptions=[
            RepoSubscription(repo=REPO, branches=[branch],
                             channel=Channel.PYTHON_SOURCE, exclude_globs=_EXCLUDES),
        ],
        token=token or os.environ.get("GITHUB_TOKEN") or None,
        apply_strategy="whole_app",
        conflict_policy="mark",
        auto_install_deps=auto_install_deps,
        requirements_file="requirements.txt",
        backup=True,
        state_dir=_STATE_DIR,
    )
    updater = Updater(cfg)
    if on_event is not None:
        updater.events.on(on_event)
    return updater


def in_dev_environment() -> bool:
    """True in the developer working dir (.dev-copy present, gitignored in ships)."""
    return (APP_ROOT / ".dev-copy").exists()


class _FnWorker(QThread):
    """Runs a blocking callable off the GUI thread, emitting result or error."""

    ok = pyqtSignal(object)
    err = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.ok.emit(self._fn())
        except Exception as e:  # surfaced to the UI; also logged with traceback
            traceback.print_exc()
            self.err.emit(f"{type(e).__name__}: {e}")


class UpdaterService(QObject):
    """Controller-owned facade over gitplucker. All heavy work is threaded."""

    check_started = pyqtSignal()
    check_finished = pyqtSignal(object)     # gitplucker UpdatePlan
    check_failed = pyqtSignal(str)
    apply_started = pyqtSignal()
    apply_finished = pyqtSignal(object)     # gitplucker ApplyResult
    apply_failed = pyqtSignal(str)
    progress = pyqtSignal(str, dict)        # (event_name, payload) from gitplucker
    update_available = pyqtSignal(bool, str, list)  # (has_update, branch, commits) — startup probe
    commits_ready = pyqtSignal(list)         # stacked commit messages for the window
    rollback_finished = pyqtSignal(object)  # gitplucker ApplyResult
    rollback_failed = pyqtSignal(str)
    baseline_seeded = pyqtSignal(object)    # status dict from seed_baseline
    baseline_failed = pyqtSignal(str)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._updater = None
        self._plan = None
        self._worker = None                 # keep a ref so QThread isn't GC'd
        self._seed_worker = None
        log.info("[UpdaterService.__init__] ready | repo=%s | state=%s" % (REPO, _STATE_DIR))

    # ── availability ───────────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        try:
            import gitplucker  # noqa: F401
            return True
        except Exception as e:
            log.warning(f"[UpdaterService.available] gitplucker not importable: {e}")
            return False

    def installed_version(self, branch: str = DEFAULT_BRANCH) -> str | None:
        """Last applied version for this branch (None if never updated)."""
        try:
            from gitplucker.state import StateStore
            return StateStore(_STATE_DIR).get_version(REPO, branch)
        except Exception:
            return None

    @property
    def auto_dev_detected(self) -> bool:
        """Auto-detected developer working dir (.dev-copy present, gitignored in ships)."""
        return in_dev_environment()

    @property
    def dev_mode(self) -> bool:
        """User-set developer-mode flag (persisted in settings)."""
        s = getattr(self.controller, "settings", {}) or {}
        return bool(s.get("updater_dev_mode", False))

    def set_dev_mode(self, on: bool) -> None:
        try:
            self.controller.settings["updater_dev_mode"] = bool(on)
            self.controller.save_settings()
        except Exception:
            pass

    @property
    def is_dev_environment(self) -> bool:
        """Developer environment: auto-detected working dir OR the manual flag.
        When true we never auto-notify, so a copy that is AHEAD of / diverged from
        the repo (your local edits are newer) doesn't nag or get overwritten."""
        return self.auto_dev_detected or self.dev_mode

    @property
    def saved_branch(self) -> str:
        """The branch the user last subscribed to (persisted in settings)."""
        s = getattr(self.controller, "settings", {}) or {}
        b = s.get("update_branch")
        return b if b in ("main", "unstable") else DEFAULT_BRANCH

    def set_saved_branch(self, branch: str) -> None:
        try:
            self.controller.settings["update_branch"] = branch
            self.controller.save_settings()
        except Exception:
            pass

    # ── auto-check / notification preferences ───────────────────────────────
    def _get_flag(self, key: str, default: bool) -> bool:
        s = getattr(self.controller, "settings", {}) or {}
        v = s.get(key)
        return default if v is None else bool(v)

    def _set_flag(self, key: str, on: bool) -> None:
        try:
            self.controller.settings[key] = bool(on)
            self.controller.save_settings()
        except Exception:
            pass

    @property
    def auto_check_enabled(self) -> bool:
        """Probe GitHub for updates shortly after launch (off = never auto-check)."""
        return self._get_flag("update_auto_check", True)

    def set_auto_check_enabled(self, on: bool) -> None:
        self._set_flag("update_auto_check", on)

    @property
    def notify_enabled(self) -> bool:
        """Surface a notification when a startup check finds an update."""
        return self._get_flag("update_notify", True)

    def set_notify_enabled(self, on: bool) -> None:
        self._set_flag("update_notify", on)

    @property
    def auto_install_deps(self) -> bool:
        """Auto-install detected Python dependencies when applying an update."""
        return self._get_flag("update_auto_deps", True)

    def set_auto_install_deps(self, on: bool) -> None:
        self._set_flag("update_auto_deps", on)

    # ── build ──────────────────────────────────────────────────────────────
    def _build_updater(self, branch: str):
        settings = getattr(self.controller, "settings", {}) or {}
        token = settings.get("github_token") or os.environ.get("GITHUB_TOKEN") or None
        return make_updater(
            branch,
            token=token,
            auto_install_deps=bool(settings.get("update_auto_deps", True)),
            on_event=lambda name, payload: self.progress.emit(name, payload),
        )

    # ── check ──────────────────────────────────────────────────────────────
    def check(self, branch: str = DEFAULT_BRANCH):
        if not self.available:
            self.check_failed.emit(
                "The 'gitplucker' updater library is not installed in this "
                "environment (pip install updater-gitplucker)."
            )
            return
        log.info(f"[UpdaterService.check] checking {REPO}@{branch}")
        self.check_started.emit()
        try:
            self._updater = self._build_updater(branch)
        except Exception as e:
            traceback.print_exc()
            self.check_failed.emit(f"could not initialise updater: {e}")
            return
        self._branch = branch
        w = _FnWorker(lambda: self._updater.check_repo(REPO, branch))
        w.ok.connect(self._on_check_ok)
        w.err.connect(self.check_failed)
        self._worker = w
        w.start()

    def _on_check_ok(self, plan):
        self._plan = plan
        log.info(f"[UpdaterService._on_check_ok] {plan.summary()}")
        self.check_finished.emit(plan)

    # ── review ─────────────────────────────────────────────────────────────
    def preview(self, relpath: str) -> str:
        """Unified diff of what applying one file would do (fast, synchronous)."""
        if self._updater is None or self._plan is None:
            return "(no plan — run a check first)"
        try:
            return self._updater.preview_change(self._plan, relpath)
        except Exception as e:
            return f"(could not build preview: {e})"

    def review(self, relpath: str) -> list[tuple[str, str]]:
        """Origin-tagged 3-way review of one file for colored rendering.

        Returns ``[(tag, line), ...]`` (tags: same, update_add, update_del,
        local_add, local_del, conflict_marker/local/base/remote, hunk, header,
        info). Falls back to a single info entry when no plan exists.
        """
        if self._updater is None or self._plan is None:
            return [("info", "(no plan — run a check first)")]
        try:
            return self._updater.review_change(self._plan, relpath)
        except Exception as e:
            return [("info", f"(could not build review: {e})")]

    def write_managed_file(self, relpath: str, text: str) -> str:
        """Write a hand-/AI-resolved version of one file, backing up the old one.

        Used by the line-management dialog so the user can apply a custom merge of
        a PROTECTED file without gitplucker overwriting their accounts. Returns the
        backup path. Refuses to escape the app root.
        """
        import datetime, shutil
        rp = str(relpath).replace("\\", "/")
        target = (APP_ROOT / rp).resolve()
        if APP_ROOT.resolve() not in target.parents and target != APP_ROOT.resolve():
            raise ValueError(f"refusing to write outside the app: {relpath}")
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = _STATE_DIR / "managed" / ts
        backup_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            dest = backup_dir / target.name
            shutil.copy2(target, dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        log.info(f"[UpdaterService.write_managed_file] wrote {rp} (backup in {backup_dir})")
        return str(backup_dir)

    # ── apply ──────────────────────────────────────────────────────────────
    def apply(self, only=None):
        """Apply the checked plan. ``only`` restricts to a subset of paths."""
        if self._updater is None or self._plan is None:
            self.apply_failed.emit("nothing to apply — run a check first")
            return
        log.info(f"[UpdaterService.apply] applying update | only={None if only is None else len(only)} file(s)")
        self.apply_started.emit()
        plan = self._plan
        w = _FnWorker(lambda: self._updater.apply(plan, only=only))
        w.ok.connect(self._on_apply_ok)
        w.err.connect(self.apply_failed)
        self._worker = w
        w.start()

    def _on_apply_ok(self, result):
        self._plan = None
        log.info(f"[UpdaterService._on_apply_ok] success={result.success} | {result.message}")
        self.apply_finished.emit(result)

    # ── startup auto-notification ──────────────────────────────────────────
    def check_startup_notify(self):
        """Cheap background probe on launch; emits update_available(has, branch).

        No-ops in the dev working dir and when there's no established baseline
        yet (a fresh install), so it never produces false 'update available'
        noise — which also covers running from the working directory.
        """
        if not self.available or self.is_dev_environment:
            log.info("[UpdaterService.check_startup_notify] skipped "
                     f"(available={self.available}, dev={self.is_dev_environment})")
            return
        if not self.auto_check_enabled:
            log.info("[UpdaterService.check_startup_notify] skipped (auto-check disabled)")
            return
        branch = self.saved_branch
        if not self.installed_version(branch):
            log.info("[UpdaterService.check_startup_notify] skipped (no baseline version yet)")
            return

        def _probe():
            u = self._build_updater(branch)
            avail = u.has_update_available(REPO, branch)
            commits = u.pending_commits(REPO, branch) if avail else []
            return avail, commits

        w = _FnWorker(_probe)
        w.ok.connect(lambda res: self.update_available.emit(bool(res[0]), branch, res[1]))
        w.err.connect(lambda msg: log.warning(f"[UpdaterService] startup probe failed: {msg}"))
        self._probe_worker = w
        w.start()

    def fetch_pending_commits(self, branch: str | None = None, limit: int = 50):
        """Threaded fetch of the stacked commit messages; emits commits_ready(list)."""
        if not self.available:
            self.commits_ready.emit([])
            return
        branch = branch or self.saved_branch
        try:
            u = self._updater or self._build_updater(branch)
        except Exception as e:
            log.warning(f"[UpdaterService.fetch_pending_commits] {e}")
            self.commits_ready.emit([])
            return
        w = _FnWorker(lambda: u.pending_commits(REPO, branch, limit))
        w.ok.connect(lambda commits: self.commits_ready.emit(commits or []))
        w.err.connect(lambda msg: (log.warning(f"[UpdaterService] commits fetch failed: {msg}"),
                                   self.commits_ready.emit([])))
        self._commits_worker = w
        w.start()

    # ── baseline (common ancestor for 3-way merge) ─────────────────────────
    def has_baseline(self, branch: str | None = None) -> bool:
        """True once a merge baseline exists for the branch (enables 3-way review)."""
        branch = branch or self.saved_branch
        return self.installed_version(branch) is not None

    def seed_baseline(self, branch: str | None = None, force: bool = False):
        """Establish the merge baseline from upstream WITHOUT changing files.

        Threaded; emits baseline_seeded(status) or baseline_failed(msg). After
        this, three-way review works — local edits are attributed to the user
        instead of shown as update changes. Safe to call on first run.
        """
        if not self.available:
            self.baseline_failed.emit("updater unavailable")
            return
        branch = branch or self.saved_branch
        try:
            u = self._build_updater(branch)
        except Exception as e:
            self.baseline_failed.emit(f"could not initialise updater: {e}")
            return
        log.info(f"[UpdaterService.seed_baseline] seeding {REPO}@{branch} (force={force})")
        w = _FnWorker(lambda: u.seed_baseline(REPO, branch, force=force))
        w.ok.connect(self.baseline_seeded)
        w.err.connect(self.baseline_failed)
        self._seed_worker = w
        w.start()

    def maybe_seed_baseline_on_startup(self):
        """Auto-establish a baseline once, in the background, for normal installs.

        Skips the developer working copy (their files are intentionally ahead) and
        respects the auto-check preference. No-op if a baseline already exists.
        """
        if not self.available or self.is_dev_environment:
            return
        if not self.auto_check_enabled:
            return
        branch = self.saved_branch
        if self.has_baseline(branch):
            return
        log.info("[UpdaterService.maybe_seed_baseline_on_startup] no baseline — seeding")
        self.seed_baseline(branch)

    # ── rollback / snapshots ───────────────────────────────────────────────
    def list_snapshots(self, branch: str | None = None) -> list:
        if not self.available:
            return []
        branch = branch or self.saved_branch
        try:
            u = self._updater or self._build_updater(branch)
            return u.list_snapshots(REPO, branch)
        except Exception as e:
            log.warning(f"[UpdaterService.list_snapshots] {e}")
            return []

    def rollback(self, branch: str | None = None):
        if not self.available:
            self.rollback_failed.emit("updater unavailable")
            return
        branch = branch or self.saved_branch
        log.info(f"[UpdaterService.rollback] reverting {REPO}@{branch}")
        try:
            u = self._build_updater(branch)
        except Exception as e:
            self.rollback_failed.emit(f"could not initialise updater: {e}")
            return
        w = _FnWorker(lambda: u.rollback(REPO, branch))
        w.ok.connect(self.rollback_finished)
        w.err.connect(self.rollback_failed)
        self._worker = w
        w.start()

    # ── housekeeping ───────────────────────────────────────────────────────
    def discard(self):
        """Drop a checked-but-unapplied plan and clean its temp download."""
        try:
            if self._updater is not None and self._plan is not None:
                self._updater.discard(self._plan)
        except Exception:
            pass
        self._plan = None

    @property
    def has_pending_plan(self) -> bool:
        return self._plan is not None
