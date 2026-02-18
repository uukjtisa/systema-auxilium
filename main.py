"""
Systema Auxilium - Operating System Helper Agent
An AI assistant that can control the system via Python interpreter

Original Architecture & Implementation:
    - Niccc2007
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
        f"h{now.strftime('%I')}_"
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

# ── Core imports come AFTER the Tee is in place ──────────────────────────────
from PyQt6.QtWidgets import QApplication
from core.controller import AssistantController
# ─────────────────────────────────────────────────────────────────────────────


def hide_console_window():
    """Hide the console window on Windows if launched from CMD"""
    if sys.platform == "win32":
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
                return True
        except Exception:
            pass
    return False


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Systema Auxilium - AI System Agent")
    app.setOrganizationName("NicProjects")

    print(
        "======================CAUTION======================\n\n"
        "This AI can execute system-level actions if ran with "
        "sufficient permissions.\nUse caution when issuing prompts.\n"
        "You are responsible for the actions taken.\n\n"
        "======================CAUTION======================"
    )

    controller = AssistantController()
    controller.show()

    console_hidden = hide_console_window()
    if console_hidden:
        print("[Startup] Console window hidden (toggle in Debug Window)")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()