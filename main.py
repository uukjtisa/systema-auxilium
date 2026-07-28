"""
main.py
Systema Auxilium - Operating System Helper Agent
An AI assistant that can control the system via Python interpreter

Original Architecture & Implementation:
    - Niccc2007 (https://github.com/uukjtisa)
"""

import faulthandler
import threading
import sys
import os
import re
import signal
import atexit
import ctypes
from datetime import datetime
from pathlib import Path


# ─── Session Logger ─── must run BEFORE any core imports ─────────────────────

_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[mGKHF]')

def _make_log_path():
    now = datetime.now()
    ms  = now.strftime("%f")[:2]
    name = (
        f"log_{now.year}_"
        f"{now.strftime('%b').lower()}_"
        f"{now.strftime('%d')}_"
        f"{now.strftime('%A').lower()}_"
        f"h{now.strftime('%I') }_"
        f"m{now.strftime('%M')}_"
        f"s{now.strftime('%S')}_"
        f"ms{ms}_"
        f"{now.strftime('%p').lower()}"
        ".txt"
    )
    logs_dir = Path("data/logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / name


class _LogSink:
    """Shared session log file — ONE file per run, both Tees write through it.

    Deliberately NOT rotated. Size-based rotation used to split a run at ~2MB
    into `_p2`, `_p3`… parts, which made debugging worse than the problem it was
    guarding: a single session's story was scattered across files, greps missed
    half the evidence, and the tail of a crash often lived in a different part
    than its cause. faulthandler stays aimed at this one file for the whole run,
    so a C-level crash always lands in the same place as the Python log.
    """

    def __init__(self):
        self.path = _make_log_path()
        self.file = open(self.path, "w", encoding="utf-8", buffering=1)
        self._written = 0
        self._lock = threading.Lock()

    def write(self, data):
        with self._lock:
            self.file.write(data)
            self._written += len(data)

    def flush(self):
        try:
            self.file.flush()
        except Exception:
            pass

    def close(self):
        try:
            self.file.flush()
            self.file.close()
        except Exception:
            pass


class _Tee:
    """
    Thread-aware tee. Mirrors writes to both the real stream and the log sink.
    Threads that call set_capture() get their output routed to their own buffer
    instead — so background threads never bleed into code execution output.
    Strips ANSI color codes so the log file is clean plain text.
    """
    def __init__(self, real, log_file):
        self._real  = real
        self._log   = log_file
        self._local = threading.local()  # per-thread capture slot

    def set_capture(self, buf):
        """Called by the execution thread to start capturing its own output."""
        self._local.capture = buf

    def clear_capture(self):
        """Called by the execution thread when done — restores normal routing."""
        self._local.capture = None

    def set_bypass(self, enabled: bool = True) -> None:
        """Ref-counted bypass flag.  While bypass_depth > 0 on this thread,
        write() and flush() skip the capture buffer and go directly to the
        real stream + log file — so logging infrastructure output never
        pollutes user-code capture buffers."""
        depth = getattr(self._local, '_bypass_depth', 0)
        self._local._bypass_depth = max(0, depth + (1 if enabled else -1))

    def write(self, data):
        bypass = getattr(self._local, '_bypass_depth', 0) > 0
        buf = getattr(self._local, 'capture', None)
        if buf is not None and not bypass:
            buf.write(data)   # this thread is capturing — don't touch the log
            return
        # Normal path — mirror to real stream + log file
        self._real.write(data)
        self._real.flush()
        try:
            clean = _ANSI_ESCAPE.sub('', data)
            self._log.write(clean)
            self._log.flush()
        except Exception:
            pass

    def flush(self):
        bypass = getattr(self._local, '_bypass_depth', 0) > 0
        buf = getattr(self._local, 'capture', None)
        if buf is not None and not bypass:
            try: buf.flush()
            except Exception: pass
            return
        self._real.flush()
        try: self._log.flush()
        except Exception: pass

    def __getattr__(self, attr):
        return getattr(self._real, attr)


def _setup_session_logger():
    sink = _LogSink()
    log_path = sink.path

    # Publish the log file so the rest of the app can NAME it: session history
    # stamps each entry with it (see systema/common/run_context.py) and the crash
    # watcher tails it instead of guessing the newest file in data/logs.
    # run_context is stdlib-only and imports nothing from the package, so this is
    # safe here — before the heavy imports, alongside the logger it describes.
    try:
        from systema.common import run_context
        run_context.set_log_path(log_path)
    except Exception:
        pass        # logging must never be what stops the app from starting

    sink.write(
        f"=== Systema Auxilium — Session Log ===\n"
        f"Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Log file: {log_path}\n"
        f"{'=' * 40}\n\n"
    )
    sink.flush()

    # Replace stdout and stderr with Tees BEFORE any core module is imported.
    # logging.StreamHandler() stores a reference to sys.stderr at creation time,
    # so as long as we replace it here first, every logger created during import
    # will automatically write through our Tee — no FileHandler injection needed.
    # Both Tees share the ONE sink (one file per run — never split).
    sys.stdout = _Tee(sys.stdout, sink)
    sys.stderr = _Tee(sys.stderr, sink)

    # C-level crashes / SIGSEGV → also goes to the log file (one file, so this
    # aim holds for the whole run)
    faulthandler.enable(file=sink.file)

    # ── Exit hooks ────────────────────────────────────────────────────────────
    def _footer(reason: str):
        try:
            sys.stderr.write(
                f"\n{'=' * 40}\n"
                f"=== {reason} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n"
            )
        except Exception:
            pass
        sink.close()

    atexit.register(lambda: _footer("Process exited normally"))

    _SIG_NAMES = {
        signal.SIGTERM: "SIGTERM (killed / Task Manager)",
        signal.SIGINT:  "SIGINT  (Ctrl+C)",
    }
    if hasattr(signal, "SIGBREAK"):
        _SIG_NAMES[signal.SIGBREAK] = "SIGBREAK (Ctrl+Break)"

    def _sig_handler(sig, frame):
        _footer(f"Received signal: {_SIG_NAMES.get(sig, sig)}")
        signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)

    for sig in _SIG_NAMES:
        try:
            signal.signal(sig, _sig_handler)
        except (OSError, ValueError):
            pass

    _orig_excepthook = sys.excepthook
    def _excepthook(exc_type, exc_value, exc_tb):
        _orig_excepthook(exc_type, exc_value, exc_tb)
        _footer(f"Unhandled exception: {exc_type.__name__}: {exc_value}")
    sys.excepthook = _excepthook

    print(f"[Logger] Session log → {log_path}")
    return log_path


# ── Single-instance check — before logger so blocked launches leave no log ───
import subprocess as _subprocess

_LOCK_FILE  = Path(__file__).parent / "data" / "systema_auxilium.lock"
_MUTEX_HOLD = None  # held for the entire process lifetime on Windows

def _read_lock_pid():
    try:
        return int(_LOCK_FILE.read_text().strip())
    except Exception:
        return None

def _acquire_instance_lock():
    global _MUTEX_HOLD
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        ERROR_ALREADY_EXISTS = 183
        handle = ctypes.windll.kernel32.CreateMutexW(None, True,
                                                     "SystemaAuxiliumSingleInstance")
        err = ctypes.windll.kernel32.GetLastError()

        if err == ERROR_ALREADY_EXISTS:
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
            return False, _read_lock_pid()

        _MUTEX_HOLD = handle
        try:
            _LOCK_FILE.write_text(str(os.getpid()))
        except Exception:
            pass
        atexit.register(lambda: _LOCK_FILE.unlink(missing_ok=True))
        return True, None

    else:
        # POSIX (Linux/macOS): atomic O_EXCL lockfile holding the owner PID, with
        # liveness-checked stale reclaim so a crashed run never blocks the next one.
        while True:
            try:
                with open(_LOCK_FILE, 'x') as f:
                    f.write(str(os.getpid()))
                atexit.register(lambda: _LOCK_FILE.unlink(missing_ok=True))
                return True, None
            except FileExistsError:
                pid = _read_lock_pid()
                if pid:
                    try:
                        os.kill(pid, 0)
                        return False, pid          # holder alive → blocked
                    except PermissionError:
                        # Exists but owned by another user (EPERM) — still ALIVE,
                        # so treat as running, never reclaim (would double-launch).
                        return False, pid
                    except ProcessLookupError:
                        pass                        # ESRCH: dead → reclaim below
                # Stale (dead PID) or corrupt/empty lockfile → remove and retry.
                try:
                    _LOCK_FILE.unlink()
                except FileNotFoundError:
                    pass

_is_primary, _existing_pid = _acquire_instance_lock()
if not _is_primary:
    # Pass the launching terminal's window handle so the notice's Dismiss button
    # can close it too (no stray console left behind by the blocked launch).
    _console_hwnd = 0
    if sys.platform == "win32":
        try:
            _console_hwnd = ctypes.windll.kernel32.GetConsoleWindow() or 0
        except Exception:
            _console_hwnd = 0
    _subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "systema" / "ui" / "startup_notif.py"),
         "--already-running", str(_existing_pid or 0), "--console", str(_console_hwnd)],
        creationflags=_subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    )
    sys.exit(0)
# ─────────────────────────────────────────────────────────────────────────────

# Run setup FIRST — before importing anything from core
_setup_session_logger()

# ── Hide console immediately after logger grabs the handles ──────────────────
if sys.platform == "win32":
    try:
        _hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if _hwnd:
            _SWP_NOSIZE     = 0x0001
            _SWP_NOMOVE     = 0x0002
            _SWP_NOZORDER   = 0x0004
            _SWP_HIDEWINDOW = 0x0080
            ctypes.windll.user32.SetWindowPos(
                _hwnd, None, 0, 0, 0, 0,
                _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOZORDER | _SWP_HIDEWINDOW
            )
    except Exception:
        pass
# ─────────────────────────────────────────────────────────────────────────────

# ── Startup notification (fire and forget) ───────────────────────────────────
_subprocess.Popen(
    [sys.executable, str(Path(__file__).parent / "systema" / "ui" / "startup_notif.py")],
    creationflags=_subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)
# ─────────────────────────────────────────────────────────────────────────────

# ── Core imports come AFTER the Tee is in place ──────────────────────────────
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import QObject, QEvent
from systema.app.controller import AssistantController
# ─────────────────────────────────────────────────────────────────────────────


class _ClickFocusActivator(QObject):
    """App-wide click-to-focus fix.

    Every Systema Auxilium window is frameless + always-on-top (see chat_window /
    floating_window). On Linux/X11 the window manager's focus-stealing-prevention
    won't hand keyboard focus to such a window on a bare click, so the input stays
    unfocusable until a stray keypress activates it. This QApplication-level filter
    re-activates whichever of our top-level windows you press, so a click ALWAYS
    grabs focus. It is a harmless no-op where the platform already focuses on click
    (Windows/macOS) and never consumes the event. Window flags are untouched —
    frameless + always-on-top are preserved. (Wayland may still ignore programmatic
    activation; there it degrades gracefully.)
    """
    def eventFilter(self, obj, event):
        try:
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(obj, QWidget):
                win = obj.window()
                if win is not None and not win.isActiveWindow():
                    win.activateWindow()
        except Exception:
            pass
        return False  # observe only — never block the click


def hide_console_window():
    """Hide the console window on Windows (used for debug toggle)"""
    if sys.platform == "win32":
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                SWP_NOSIZE     = 0x0001
                SWP_NOMOVE     = 0x0002
                SWP_NOZORDER   = 0x0004
                SWP_HIDEWINDOW = 0x0080
                ctypes.windll.user32.SetWindowPos(
                    hwnd, None, 0, 0, 0, 0,
                    SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_HIDEWINDOW
                )
                return True
        except Exception:
            pass
    return False


def main():
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QFont, QIcon
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Systema Auxilium - AI System Helper Agent")
    app.setOrganizationName("NicProjects")
    # ── App icon ──────────────────────────────────────────────────────────────
    icon_path = Path(__file__).parent / "resources" / "assets" / "systema_auxilium.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    # ─────────────────────────────────────────────────────────────────────────
    font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)

    # App-wide click-to-focus fix for frameless/always-on-top windows (X11).
    # Windows/macOS already focus on click, and on Windows this used to disturb the
    # native always-on-top behavior — so keep it a non-Windows-only fix.
    if sys.platform != "win32":
        app._focus_activator = _ClickFocusActivator()   # keep a ref so it isn't GC'd
        app.installEventFilter(app._focus_activator)

    controller = AssistantController()
    controller.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()