"""
main.py
Systema Auxilium - Operating System Helper Agent
An AI assistant that can control the system via Python interpreter

Original Architecture & Implementation:
    - Niccc2007 (https://github.com/uukjtisa)
"""

import faulthandler
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


class _Tee:
    """
    Mirrors every write to both the real stream and the log file.
    Strips ANSI color codes so the log file is clean plain text.
    """
    def __init__(self, real, log_file):
        self._real = real
        self._log  = log_file

    def write(self, data):
        self._real.write(data)
        self._real.flush()
        try:
            clean = _ANSI_ESCAPE.sub('', data)
            self._log.write(clean)
            self._log.flush()
        except Exception:
            pass

    def flush(self):
        self._real.flush()
        try: self._log.flush()
        except Exception: pass

    def __getattr__(self, attr):
        return getattr(self._real, attr)


def _setup_session_logger():
    log_path = _make_log_path()
    log_file  = open(log_path, "w", encoding="utf-8", buffering=1)

    log_file.write(
        f"=== Systema Auxilium — Session Log ===\n"
        f"Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Log file: {log_path}\n"
        f"{'=' * 40}\n\n"
    )
    log_file.flush()

    # Replace stdout and stderr with Tees BEFORE any core module is imported.
    # logging.StreamHandler() stores a reference to sys.stderr at creation time,
    # so as long as we replace it here first, every logger created during import
    # will automatically write through our Tee — no FileHandler injection needed.
    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)

    # C-level crashes / SIGSEGV → also goes to log file
    faulthandler.enable(file=log_file)

    # ── Exit hooks ────────────────────────────────────────────────────────────
    def _footer(reason: str):
        try:
            sys.stderr.write(
                f"\n{'=' * 40}\n"
                f"=== {reason} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n"
            )
        except Exception:
            pass
        try:
            log_file.flush()
            log_file.close()
        except Exception:
            pass

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
import subprocess
subprocess.Popen(
    [sys.executable, str(Path(__file__).parent / "ui" / "startup_notif.py")],
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)
# ─────────────────────────────────────────────────────────────────────────────

# ── Core imports come AFTER the Tee is in place ──────────────────────────────
from PyQt6.QtWidgets import QApplication
from core.controller import AssistantController
# ─────────────────────────────────────────────────────────────────────────────


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
    icon_path = Path(__file__).parent / "assets" / "systema_auxilium.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    # ─────────────────────────────────────────────────────────────────────────
    font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)

    controller = AssistantController()
    controller.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()