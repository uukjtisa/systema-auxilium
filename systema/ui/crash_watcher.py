"""
systema/ui/crash_watcher.py

UI-freeze watchdog + forensic crash dumps (idea #12, 2026-07-21).

The failure this exists for: during a live demo the floating window + chat
silently vanished while python.exe kept running — no traceback, no log entry,
nothing to autopsy, and the zombie held the single-instance lock so a fresh
launch was blocked until a Task Manager kill.

Three pieces:
  * beat()             — called by a tiny QTimer on the UI thread (floating
                         window); stamps data/logs/crash_dumps/heartbeat.txt.
  * CrashWatcher       — daemon THREAD (the process survived the observed
                         crash, so an in-process watchdog suffices). On a stale
                         heartbeat it writes a forensic dump bundle, spawns the
                         tkinter crash notice, spawns the relauncher, and exits
                         the zombie process so the instance lock frees.
  * write_crash_dump() — the shared dump writer; also used by the backend
                         exception hooks (sys.excepthook / threading.excepthook)
                         for the backend/ bucket.

Dump bundle: data/logs/crash_dumps/{ui,backend}/<timestamp>/ containing
crash_report.json, thread_stacks.txt (all-thread Python stacks — the key
artifact for a hung event loop), log_tail.txt, process_snapshot.txt, and
screenshot.png (ui bucket only). Retention: newest 10 bundles per bucket.
"""

import faulthandler
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from systema import APP_ROOT
from systema.common.data_paths import migrate_legacy
from systema.common.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("CrashWatcher") if _verbose else _NoOpLogger()

# Dumps live UNDER data/logs (they're diagnostics, same family as session
# logs). NOTE: the heartbeat must stay inside crash_dumps/, never loose in
# data/logs/ — _write_log_tail picks the newest *.txt there and a 5s-stamped
# heartbeat would always win. glob('*.txt') is non-recursive, so it's safe.
DUMP_ROOT = APP_ROOT / "data" / "logs" / "crash_dumps"
migrate_legacy(APP_ROOT / "data" / "crash_dumps", DUMP_ROOT)
HEARTBEAT_FILE = DUMP_ROOT / "heartbeat.txt"

HEARTBEAT_INTERVAL_MS = 5000   # UI-thread stamp cadence (cheap: one tiny write)
STALE_AFTER_S = 30.0           # generous — a busy-but-alive UI must never trip it
KEEP_PER_BUCKET = 10           # newest N dump bundles kept per bucket


# ── Heartbeat (UI thread) ────────────────────────────────────────────────────

def beat():
    """Stamp the heartbeat file. Runs on the UI thread via QTimer — if the Qt
    event loop hangs or the windows die, the stamps stop and the watcher acts."""
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(
            f"{time.time():.3f} pid={os.getpid()} "
            f"{datetime.now().isoformat(timespec='seconds')}",
            encoding="utf-8")
    except Exception:
        pass


# ── Dump writer (shared by watcher + backend hooks) ──────────────────────────

def write_crash_dump(bucket: str, reason: str, exc_text=None,
                     screenshot: bool = False):
    """Write a forensic bundle to data/logs/crash_dumps/<bucket>/<timestamp>/.
    Never raises; returns the bundle path (str) or None."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        d = DUMP_ROOT / bucket / ts
        n = 1
        while d.exists():                       # same-second collision
            n += 1
            d = DUMP_ROOT / bucket / f"{ts}_{n}"
        d.mkdir(parents=True, exist_ok=True)

        report = {
            "reason": reason,
            "bucket": bucket,
            "time": datetime.now().isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "platform": sys.platform,
            "python": sys.version,
            "exception": exc_text,
        }
        try:
            report["last_heartbeat"] = HEARTBEAT_FILE.read_text(encoding="utf-8")
        except Exception:
            report["last_heartbeat"] = None
        (d / "crash_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8")

        # All-thread Python stacks — shows exactly where a hung UI thread sits.
        try:
            with open(d / "thread_stacks.txt", "w", encoding="utf-8") as f:
                faulthandler.dump_traceback(file=f)
        except Exception:
            pass

        _write_log_tail(d)
        _write_process_snapshot(d)
        if screenshot:
            _write_screenshot(d)
        _prune(bucket)
        log.warning(f"[write_crash_dump] {bucket} dump written: {d}")
        return str(d)
    except Exception:
        return None


def _write_log_tail(d: Path, tail_bytes: int = 64 * 1024):
    """Tail of THIS run's session log — the last thing the app said.

    Asks `run_context` for the file rather than guessing the newest `*.txt` by
    mtime. The guess was wrong exactly when it mattered most: a second instance,
    or a relauncher child that had already opened its own log, made the newest
    file belong to a different process than the one that just died.
    """
    try:
        from systema.common import run_context
        src = run_context.log_path()
        if src is None or not src.exists():
            logs = sorted((APP_ROOT / "data" / "logs").glob("*.txt"),
                          key=lambda p: p.stat().st_mtime)
            if not logs:
                return
            src = logs[-1]
        with open(src, "rb") as f:
            f.seek(max(0, src.stat().st_size - tail_bytes))
            data = f.read()
        (d / "log_tail.txt").write_text(
            f"=== tail of {src.name} ===\n"
            + data.decode("utf-8", errors="replace"),
            encoding="utf-8")
    except Exception:
        pass


def _write_process_snapshot(d: Path):
    """psutil view of this process: memory, cpu, threads, children, handles."""
    try:
        import psutil
        p = psutil.Process()
        lines = [f"pid           : {p.pid}",
                 f"cmdline       : {' '.join(p.cmdline())}",
                 f"create_time   : {datetime.fromtimestamp(p.create_time()).isoformat(timespec='seconds')}",
                 f"cpu_percent   : {p.cpu_percent(interval=0.1)}",
                 f"memory_rss_mb : {p.memory_info().rss / (1024 * 1024):.1f}",
                 f"num_threads   : {p.num_threads()}"]
        try:
            lines.append(f"open_files    : {len(p.open_files())}")
        except Exception:
            pass
        lines.append("threads (id, user_s, sys_s):")
        for t in p.threads():
            lines.append(f"  {t.id}  {t.user_time:.1f}  {t.system_time:.1f}")
        kids = p.children(recursive=True)
        lines.append(f"children      : {len(kids)}")
        for c in kids:
            try:
                lines.append(f"  {c.pid}  {c.name()}")
            except Exception:
                pass
        (d / "process_snapshot.txt").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")
    except Exception:
        pass


def _write_screenshot(d: Path):
    """What the screen actually showed at dump time (ui bucket only)."""
    try:
        from PIL import ImageGrab
        ImageGrab.grab().save(str(d / "screenshot.png"))
    except Exception:
        pass


def _prune(bucket: str):
    """Keep only the newest KEEP_PER_BUCKET bundles (timestamp names sort
    chronologically)."""
    try:
        import shutil
        dirs = sorted(p for p in (DUMP_ROOT / bucket).iterdir() if p.is_dir())
        for old in dirs[:-KEEP_PER_BUCKET]:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass


# ── Backend exception hooks (backend/ bucket) ────────────────────────────────

_hooks_installed = False


def install_backend_hooks():
    """Chain onto sys.excepthook + threading.excepthook so any UNHANDLED
    exception (main thread or worker) leaves a backend dump before the existing
    handlers run. Idempotent."""
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True

    prev_sys = sys.excepthook

    def _sys_hook(exc_type, exc_value, exc_tb):
        write_crash_dump(
            "backend",
            f"Unhandled exception on main thread: {exc_type.__name__}: {exc_value}",
            exc_text="".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        prev_sys(exc_type, exc_value, exc_tb)

    sys.excepthook = _sys_hook

    prev_thr = threading.excepthook

    def _thr_hook(args):
        if args.exc_type is not SystemExit:
            name = getattr(args.thread, "name", "?")
            write_crash_dump(
                "backend",
                f"Unhandled exception in thread {name}: "
                f"{args.exc_type.__name__}: {args.exc_value}",
                exc_text="".join(traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback)))
        prev_thr(args)

    threading.excepthook = _thr_hook


# ── The watchdog thread ──────────────────────────────────────────────────────

class CrashWatcher(threading.Thread):
    """Daemon thread: polls the heartbeat stamp; on a confirmed-stale UI it
    dumps, notifies, relaunches, and exits the zombie process."""

    def __init__(self):
        super().__init__(name="CrashWatcher", daemon=True)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        # Grace: let the UI boot and lay down its first stamps.
        if self._stop.wait(HEARTBEAT_INTERVAL_MS / 1000 * 3):
            return
        last_tick = time.monotonic()
        while not self._stop.wait(HEARTBEAT_INTERVAL_MS / 1000):
            now = time.monotonic()
            own_gap, last_tick = now - last_tick, now
            if own_gap > STALE_AFTER_S:
                # OUR loop was also frozen → the whole machine slept (suspend/
                # resume), not a UI hang. Skip a cycle; the beat recovers.
                continue
            if not threading.main_thread().is_alive():
                return                      # normal interpreter shutdown
            try:
                stale = time.time() - float(
                    HEARTBEAT_FILE.read_text(encoding="utf-8").split()[0])
            except Exception:
                continue                    # no/garbled stamp yet
            if stale < STALE_AFTER_S:
                continue
            self._handle_freeze(stale)
            return

    def _handle_freeze(self, stale: float):
        # Tee routes stderr into the session log — leave a trace THERE too.
        try:
            sys.stderr.write(
                f"\n[CrashWatcher] UI heartbeat stale for {stale:.0f}s — "
                f"event loop hung or windows gone. Dumping + force-restarting.\n")
        except Exception:
            pass
        dump = write_crash_dump(
            "ui",
            f"UI heartbeat stale for {stale:.0f}s (event loop hung or windows gone)",
            screenshot=True)
        _notify_crash(dump)
        try:
            from systema.common.relauncher import spawn_relauncher
            spawn_relauncher(os.getpid(), APP_ROOT)
        except Exception:
            pass
        # os._exit: the UI thread is hung — a polite Qt shutdown would never
        # return. Children (the notice, the relauncher shell) survive this.
        os._exit(70)


def _notify_crash(dump_dir):
    """Spawn the tkinter crash notice (separate process — it must outlive us)."""
    try:
        subprocess.Popen(
            [sys.executable,
             str(APP_ROOT / "systema" / "ui" / "startup_notif.py"),
             "--crashed", str(dump_dir or "")],
            creationflags=(subprocess.CREATE_NO_WINDOW
                           if sys.platform == "win32" else 0))
    except Exception:
        pass


# ── Public entry point ───────────────────────────────────────────────────────

_watcher = None


def start_watcher():
    """Start the watchdog (idempotent) + install the backend exception hooks.
    Called once from the floating window right after its heartbeat timer."""
    global _watcher
    if _watcher is not None and _watcher.is_alive():
        return _watcher
    install_backend_hooks()
    beat()          # never let the watcher see a missing/ancient stamp at boot
    _watcher = CrashWatcher()
    _watcher.start()
    log.info(f"[start_watcher] armed (stale threshold {STALE_AFTER_S:.0f}s, "
             f"beat {HEARTBEAT_INTERVAL_MS}ms)")
    return _watcher
