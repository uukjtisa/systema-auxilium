"""systema/common/terminal.py

Cross-OS terminal / console control for the Debug window's ">_" toggle.

Windows
    The app launches with a real console that ``_Tee`` (main.py) redirects the
    session log through. The toggle simply shows / hides that console window —
    identical behavior to the old inline logic, just centralized here.

Linux / macOS
    The app is launched *detached* (run.sh uses ``setsid``/``nohup``), so there
    is no clinging terminal to toggle. Instead the toggle opens a dedicated
    *log-viewer* terminal that live-tails the newest session log under
    ``data/logs/`` — and closes it again. Logging itself is unaffected: ``_Tee``
    captures everything regardless of whether this viewer is open.
"""
from __future__ import annotations

import os
import sys
import signal
import shlex
import shutil
import subprocess
from pathlib import Path


def _app_root() -> Path:
    try:
        from systema import APP_ROOT
        return Path(APP_ROOT)
    except Exception:
        # common/terminal.py → common → systema → <root>
        return Path(__file__).resolve().parents[2]


# ── Windows console (real console show/hide) ─────────────────────────────────

def console_supported() -> bool:
    """True when a real OS console window exists to toggle (Windows only)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


def console_visible() -> bool:
    """True when the Windows console window exists AND is currently visible.
    The Debug toggle syncs from this instead of trusting a remembered flag —
    after a restart the console exists but starts hidden."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        return bool(hwnd) and bool(ctypes.windll.user32.IsWindowVisible(hwnd))
    except Exception:
        return False


def _win_show_console(show: bool) -> bool:
    import ctypes
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if not hwnd:
        return False
    ctypes.windll.user32.ShowWindow(hwnd, 5 if show else 0)  # SW_SHOW / SW_HIDE
    return True


def show_console() -> bool:
    """Show the Windows console. Returns True on success."""
    if sys.platform != "win32":
        return False
    try:
        return _win_show_console(True)
    except Exception:
        return False


def hide_console() -> bool:
    """Hide the Windows console. Returns True on success."""
    if sys.platform != "win32":
        return False
    try:
        return _win_show_console(False)
    except Exception:
        return False


# ── POSIX log-viewer terminal (tail -f newest log) ───────────────────────────

_log_terminal: "subprocess.Popen | None" = None


def latest_log() -> "Path | None":
    """THIS run's log file, falling back to the newest ``log_*.txt`` by mtime.

    Prefers `run_context` over the mtime scan: with a relauncher child or a
    second instance running, the newest file on disk belongs to a different
    process, and the log terminal would then tail somebody else's output.
    """
    try:
        from systema.common import run_context
        active = run_context.log_path()
        if active is not None and active.exists():
            return active
    except Exception:
        pass
    logs = _app_root() / "data" / "logs"
    try:
        files = [p for p in logs.glob("log_*.txt") if p.is_file()]
    except Exception:
        return None
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def log_terminal_open() -> bool:
    """True while the spawned log-viewer terminal is still running."""
    return _log_terminal is not None and _log_terminal.poll() is None


# Emulators tried in order. Those that stay ATTACHED to the launched process
# (so killing our Popen closes the window) come first; gnome-terminal is last
# because it hands off to a server and detaches — there close is best-effort.
_LINUX_TERMINALS = (
    "x-terminal-emulator",   # Debian/Kali alternatives symlink (usually qterminal)
    "qterminal",
    "konsole",
    "xterm",
    "alacritty",
    "kitty",
    "xfce4-terminal",
    "mate-terminal",
    "tilix",
    "gnome-terminal",
)


def _linux_terminal_argv(exe: str, tail_cmd: str) -> list:
    """Build the correct argv for `exe` to run `sh -c tail_cmd` in a new window,
    accounting for each emulator's command-flag conventions."""
    if exe in ("gnome-terminal", "mate-terminal", "tilix"):
        return [exe, "--", "sh", "-c", tail_cmd]
    if exe == "xfce4-terminal":
        return [exe, "-x", "sh", "-c", tail_cmd]
    if exe == "kitty":
        return [exe, "sh", "-c", tail_cmd]
    # x-terminal-emulator, qterminal, konsole, xterm, alacritty
    return [exe, "-e", "sh", "-c", tail_cmd]


def open_log_terminal() -> "tuple[bool, str]":
    """Open a terminal that live-tails the newest session log.
    Returns ``(ok, message)``."""
    global _log_terminal
    if log_terminal_open():
        return True, "Log terminal already open."

    log = latest_log()
    if log is None:
        return False, "No session log found under data/logs/."

    if sys.platform == "darwin":
        osa = ('tell application "Terminal"\n'
               f'  do script "clear; tail -n +1 -f {str(log).replace(chr(34), chr(92)+chr(34))}"\n'
               '  activate\n'
               'end tell')
        try:
            _log_terminal = subprocess.Popen(["osascript", "-e", osa],
                                             start_new_session=True)
            return True, f"Opened Terminal tailing {log.name}."
        except Exception as e:
            return False, f"Could not open Terminal.app: {e}"

    # Linux / other X11 desktops
    tail_cmd = (f'clear; echo "=== live log: {log.name} "'
                f'"(Ctrl+C or close this window to stop) ==="; '
                f'exec tail -n +1 -f {shlex.quote(str(log))}')
    tried = []
    for exe in _LINUX_TERMINALS:
        if shutil.which(exe) is None:
            continue
        tried.append(exe)
        try:
            _log_terminal = subprocess.Popen(_linux_terminal_argv(exe, tail_cmd),
                                             start_new_session=True)
            return True, f"Opened {exe} tailing {log.name}."
        except Exception:
            continue
    if tried:
        return False, f"Failed to launch a terminal (tried: {', '.join(tried)})."
    return False, ("No supported terminal emulator found "
                   "(install one of: x-terminal-emulator, xterm, konsole, gnome-terminal).")


def close_log_terminal() -> "tuple[bool, str]":
    """Close the spawned log-viewer terminal. Returns ``(ok, message)``."""
    global _log_terminal
    if not log_terminal_open():
        _log_terminal = None
        return True, "Log terminal already closed."

    proc = _log_terminal
    try:
        # We started it with start_new_session=True, so it leads its own process
        # group — signal the whole group to take down both the shell and tail.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    finally:
        _log_terminal = None
    return True, "Closed log terminal."


def toggle_log_terminal() -> "tuple[bool, bool, str]":
    """Toggle the POSIX log-viewer terminal.
    Returns ``(ok, now_open, message)``."""
    if log_terminal_open():
        ok, msg = close_log_terminal()
        return ok, False, msg
    ok, msg = open_log_terminal()
    return ok, ok, msg
