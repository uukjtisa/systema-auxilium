"""
systema/updater/service.py
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
- **User data is excluded** (`data/**`, `settings.json`, venv, caches)
  so logs/sessions/memory/settings are never overwritten or deleted.
- Update state + pre-apply backups live under `data/updates/` (itself excluded),
  so a bad update can be rolled back by restoring from there.
"""

from __future__ import annotations

import json
import os
import time
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
    "settings.json",                 # the user's own configuration (consolidated)
    # Legacy config names — pre-consolidation installs may still carry them and
    # an update must never propose touching a stale copy.
    "assistant_settings.json", "chat_config.json", "floating_window_config.json",
    "*.lock", "**/*.lock",
]

# User-owned "data folders": paths the user edits and populates themselves —
# provider scripts (API keys / accounts) and skill files. Updates legitimately
# touch these sometimes, so unlike _EXCLUDES they STAY VISIBLE in the plan; but a
# content change to one (MOD / DEL) is auto-UNSELECTED, flagged "PROTECTED", and
# takes an explicit opt-in + a strong warning, so an update can never silently
# overwrite or delete the user's work. A brand-new file (NEW) in one of these is
# only additive and is treated normally — it can't overwrite or delete anything.
#
# (data/** itself is the strongest tier: it's in _EXCLUDES above, so the user's
# runtime — sessions, memory, tasks, logs — never even appears in a plan and can
# never be overwritten or proposed for deletion.)
#
# `providers/` moved under `resources/` on 2026-07-28. The pre-move glob stays
# listed so an install updating ACROSS that move still treats its old-layout
# provider scripts as protected — dropping it would let the first update after
# the move propose overwriting them as ordinary source.
_SENSITIVE_GLOBS = [
    "resources/providers/**",
    "providers/**",              # pre-2026-07-28 layout
    "skills/**",
]

_SENSITIVE_PREFIXES = ("resources/providers/", "providers/", "skills/")


def is_sensitive_path(relpath: str) -> bool:
    """True if ``relpath`` is inside a user-owned data folder (providers/skills)
    whose content changes must be reviewed and applied by hand."""
    rp = str(relpath).replace("\\", "/")
    try:
        from gitplucker.fsutil import glob_match
        return glob_match(rp, _SENSITIVE_GLOBS)
    except Exception:
        return rp.startswith(_SENSITIVE_PREFIXES)


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


# Marker file that identifies a developer working copy. Deliberately neutral and
# purpose-named: it says what it is FOR, not which editor happens to create it,
# and it is gitignored so it never reaches a shipped checkout.
DEV_MARKER = ".dev-copy"


def in_dev_environment() -> bool:
    """True in the developer working dir (marker file present, never shipped)."""
    return (APP_ROOT / DEV_MARKER).exists()


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
        self._settle_after_apply = None     # settle plan captured before an apply
        log.info("[UpdaterService.__init__] ready | repo=%s | state=%s" % (REPO, _STATE_DIR))

    # ── availability ───────────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        try:
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
        """Auto-detected developer working dir (marker file present, never shipped)."""
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

    # ── "settled" store ─────────────────────────────────────────────────────
    # A partial apply (only=…) deliberately does NOT advance gitplucker's version
    # marker (unselected files weren't updated), so has_update_available() keeps
    # returning True and the startup probe re-notifies forever. The "settled" store
    # records the files the user DECIDED not to apply (PROTECTED / unwanted) at a
    # baseline (their upstream content hash) + the origin commit, plus the commit
    # the user last acknowledged. The startup nag is suppressed while HEAD == that
    # acknowledged commit; a settled file re-appears only when a NEW commit changes
    # its upstream content (its remote_hash moves). Kept in data/updates/ (excluded
    # from the update payload). Schema:
    #   { "<branch>": {"acknowledged": "<target_version>",
    #                  "files": {"<relpath>": {"remote_hash","commit","settled_at"}}}}
    def _settled_path(self):
        return _STATE_DIR / "settled.json"

    def _settled_load(self) -> dict:
        try:
            p = self._settled_path()
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log.warning(f"[UpdaterService._settled_load] {e}")
        return {}

    def _settled_save(self, data: dict) -> None:
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._settled_path().write_text(
                json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        except Exception as e:
            log.warning(f"[UpdaterService._settled_save] {e}")

    def settled_files(self, branch: str) -> dict:
        """{relpath: {remote_hash, commit, settled_at}} for this branch."""
        return (self._settled_load().get(branch, {}) or {}).get("files", {}) or {}

    def settle_files(self, branch: str, items, commit) -> int:
        """Record files as settled. ``items`` = iterable of (relpath, remote_hash).
        Returns the number stored."""
        items = [(str(p), h or "") for p, h in items if p]
        if not items:
            return 0
        data = self._settled_load()
        files = data.setdefault(branch, {}).setdefault("files", {})
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        for path, rhash in items:
            files[path] = {"remote_hash": rhash, "commit": commit or "", "settled_at": now}
        self._settled_save(data)
        log.info(f"[UpdaterService.settle_files] settled {len(items)} file(s) on "
                 f"'{branch}' @ {commit}")
        return len(items)

    def unsettle(self, branch: str, path: str) -> None:
        data = self._settled_load()
        files = data.get(branch, {}).get("files", {})
        if str(path) in files:
            del files[str(path)]
            self._settled_save(data)

    def clear_settled_branch(self, branch: str) -> None:
        """Drop all settled files + acknowledged marker for a branch (used after a
        FULL apply, which advances the version marker past everything)."""
        data = self._settled_load()
        if branch in data:
            data[branch]["files"] = {}
            data[branch]["acknowledged"] = None
            self._settled_save(data)

    def acknowledged_version(self, branch: str) -> str | None:
        return self._settled_load().get(branch, {}).get("acknowledged")

    def set_acknowledged(self, branch: str, version) -> None:
        data = self._settled_load()
        data.setdefault(branch, {})["acknowledged"] = version or ""
        self._settled_save(data)

    def prune_settled_against_plan(self, branch: str, plan) -> dict:
        """Reconcile the settled store with a fresh plan and return the survivors
        ({relpath: entry}). Drops an entry when its file is no longer in the plan
        (upstream now matches local) or when its upstream content changed since it
        was settled (a new commit touched it → re-offer it as a normal change)."""
        try:
            files_now = {fc.path: getattr(fc, "remote_hash", None)
                         for fc in plan.file_changes if fc.change.value != "unchanged"}
        except Exception:
            files_now = {}
        data = self._settled_load()
        files = data.get(branch, {}).get("files", {})
        changed = False
        for path in list(files.keys()):
            entry = files[path]
            if path not in files_now:
                del files[path]; changed = True; continue
            rnow = files_now.get(path)
            if rnow and entry.get("remote_hash") and rnow != entry["remote_hash"]:
                del files[path]; changed = True     # upstream moved → un-settle
        if changed:
            self._settled_save(data)
        return files

    @staticmethod
    def _sha_match(a: str | None, b: str | None) -> bool:
        """True if two version stamps / shas refer to the same commit (compares the
        short-sha tail of ``YYYY-MM-DD+shortsha`` or a bare sha, min 7 chars)."""
        if not a or not b:
            return False
        a = str(a).split("+")[-1]
        b = str(b).split("+")[-1]
        n = min(len(a), len(b))
        return n >= 7 and a[:n] == b[:n]

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
        # newline="" preserves the exact line endings the merge produced instead
        # of letting Windows translate every "\n" into "\r\n".
        target.write_text(text, encoding="utf-8", newline="")
        log.info(f"[UpdaterService.write_managed_file] wrote {rp} (backup in {backup_dir})")
        # A hand-resolved file is a decision: settle it at the current plan's commit
        # so it stops nagging until upstream changes it again.
        try:
            if self._plan is not None:
                fc = next((f for f in self._plan.file_changes if f.path == rp), None)
                branch = getattr(self, "_branch", None) or self.saved_branch
                self.settle_files(branch, [(rp, getattr(fc, "remote_hash", None) if fc else None)],
                                  self._plan.target_version)
                self.set_acknowledged(branch, self._plan.target_version)
        except Exception:
            pass
        return str(backup_dir)

    # ── apply ──────────────────────────────────────────────────────────────
    def apply(self, only=None, resolved=None):
        """Apply the checked plan. ``only`` restricts to a subset of paths.

        ``resolved`` (``{relpath: text}``) supplies user-resolved content for
        conflicted files: the plan's write-op for each such path is rewritten
        to that exact text BEFORE applying, so a resolved conflict goes through
        the ONE normal apply path (backup, snapshot, rollback, deps) and — when
        every change is applied — advances the version marker. This is what
        makes a resolved conflict actually stick instead of being written as
        markers and re-flagged on the next check.
        """
        if self._updater is None or self._plan is None:
            self.apply_failed.emit("nothing to apply — run a check first")
            return
        log.info(f"[UpdaterService.apply] applying update | only={None if only is None else len(only)} file(s)"
                 f" | resolved={0 if not resolved else len(resolved)}")
        self.apply_started.emit()
        plan = self._plan
        if resolved:
            for op in getattr(plan, "_ops", []):
                rp = getattr(op, "relpath", None)
                if rp in resolved:
                    op.kind = "write"
                    op.text = resolved[rp]
                    op.src = None
        branch = getattr(self, "_branch", None) or self.saved_branch
        # Capture the settle plan BEFORE threading (the plan is cleared on success):
        # a partial apply settles the changed files the user chose NOT to apply (so
        # they stop nagging); a full apply just clears the branch's settled state
        # because the version marker advances past everything.
        if only is not None:
            selected = set(only)
            leftovers = [(fc.path, getattr(fc, "remote_hash", None))
                         for fc in plan.file_changes
                         if fc.change.value != "unchanged" and fc.path not in selected]
            self._settle_after_apply = {"branch": branch, "items": leftovers,
                                        "commit": plan.target_version, "partial": True}
        else:
            self._settle_after_apply = {"branch": branch, "partial": False}
        w = _FnWorker(lambda: self._updater.apply(plan, only=only))
        w.ok.connect(self._on_apply_ok)
        w.err.connect(self.apply_failed)
        self._worker = w
        w.start()

    def _on_apply_ok(self, result):
        self._plan = None
        pend = self._settle_after_apply
        self._settle_after_apply = None
        if result.success and pend:
            branch = pend.get("branch") or self.saved_branch
            if pend.get("partial"):
                if pend.get("items"):
                    self.settle_files(branch, pend["items"], pend.get("commit"))
                # Remember the commit the user has now dealt with, so the startup
                # probe stops nagging until a genuinely new commit lands.
                self.set_acknowledged(branch, pend.get("commit"))
            else:
                self.clear_settled_branch(branch)   # full apply advanced the version
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
            head_sha = (commits[0].get("sha") if commits else "") or ""
            return avail, commits, head_sha

        def _on_probe(res):
            avail, commits, head_sha = res
            if avail:
                ack = self.acknowledged_version(branch)
                if ack and head_sha and self._sha_match(ack, head_sha):
                    # The user already dealt with this exact commit (applied what they
                    # wanted, settled the rest) — only settled files remain, so don't
                    # nag. A genuinely new commit moves HEAD and re-enables the notice.
                    log.info("[UpdaterService.check_startup_notify] suppressed — HEAD "
                             f"already acknowledged ({ack})")
                    avail = False
            self.update_available.emit(bool(avail), branch, commits if avail else [])

        w = _FnWorker(_probe)
        w.ok.connect(_on_probe)
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
