"""
systema/common/relauncher.py

The SINGLE restart path, factored out of app/controller.py (2026-07-21) so that
lightweight processes — the tkinter notice (ui/startup_notif.py) and the crash
watcher — can kill and relaunch the app WITHOUT importing the controller (which
pulls in PyQt, the engine and every provider).

stdlib only. Anything added here must stay import-cheap.
"""

import os
import subprocess
import sys
from pathlib import Path


def _warn(msg: str) -> None:
    """Best-effort stderr note (the app's Tee routes it into the session log)."""
    try:
        sys.stderr.write(f"[relauncher] {msg}\n")
    except Exception:
        pass


def spawn_relauncher(pid: int, root) -> bool:
    """Spawn a DETACHED SHELL that INHERITS this process's privileges — so an
    elevated app restarts elevated and a normal one restarts normal, no user
    switching — which:
      1. polls until the OLD pid is gone (the moment the single-instance lock is
         released; bounded so it can never hang, NOT a fixed-time guess), then
      2. cd's to APP_ROOT and execs the canonical venv launch script (run.sh /
         run.bat), falling back to this same interpreter + main.py if missing.

    POSIX uses an inline `sh -c` (kill -0 to watch the pid); Windows uses
    PowerShell `Wait-Process` (a direct wait on the pid — no console-tool output
    parsing, which is unreliable in a windowless process).
    """
    root = Path(root)
    try:
        if sys.platform == "win32":
            script = root / "run.bat"
            # Windows paths never contain a single quote, so single-quoting is safe.
            launch = (f"& cmd /c '{script}'" if script.exists()
                      else f"& '{sys.executable}' '{root / 'main.py'}'")
            ps = (f"Wait-Process -Id {pid} -Timeout 30 -ErrorAction SilentlyContinue; "
                  f"Start-Sleep -Milliseconds 500; "
                  f"Set-Location -LiteralPath '{root}'; {launch}")
            # A REAL console, born hidden — NOT CREATE_NO_WINDOW. CREATE_NO_WINDOW
            # gave the whole relaunched chain (powershell -> cmd -> python) a
            # windowless conhost, so after a restart GetConsoleWindow() returned
            # NULL and the Debug window's console toggle silently did nothing.
            # A new console started SW_HIDE keeps the relaunch just as invisible
            # while giving the restarted app a toggleable console window.
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                cwd=str(root), close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                startupinfo=si)
        else:
            script = root / "run.sh"
            launch = (f'exec sh "{script}"' if script.exists()
                      else f'exec "{sys.executable}" "{root / "main.py"}"')
            # Wait (bounded ~30s) for our pid to vanish, tiny grace, then relaunch.
            sh = (f'i=0; while kill -0 {pid} 2>/dev/null && [ "$i" -lt 300 ]; do '
                  f'sleep 0.1; i=$((i+1)); done; sleep 0.5; cd "{root}" || exit 1; {launch}')
            subprocess.Popen(["sh", "-c", sh], cwd=str(root),
                             close_fds=True, start_new_session=True)
        return True
    except Exception as e:
        _warn(f"failed to spawn relauncher: {e}")
        return False


def process_alive(pid: int) -> bool:
    """True if `pid` still exists. Never raises."""
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            SYNCHRONIZE = 0x00100000
            h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
            if not h:
                return False
            # WAIT_TIMEOUT (0x102) => still running; WAIT_OBJECT_0 (0) => exited.
            rc = ctypes.windll.kernel32.WaitForSingleObject(h, 0)
            ctypes.windll.kernel32.CloseHandle(h)
            return rc != 0
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True            # exists, owned by someone else
    except (ProcessLookupError, OSError, ValueError):
        return False


def kill_process(pid: int) -> bool:
    """Force-kill `pid` (and its children on Windows). Used to clear a process
    whose UI died but whose interpreter is still holding the instance lock."""
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           capture_output=True, timeout=15)
        else:
            import signal as _signal
            os.kill(int(pid), _signal.SIGKILL)
        return True
    except Exception as e:
        _warn(f"failed to kill pid {pid}: {e}")
        return False


def kill_and_relaunch(pid: int, root) -> bool:
    """Kill a stuck instance, then relaunch the app once its pid is gone."""
    kill_process(pid)
    return spawn_relauncher(pid, root)
