"""
systema/common/autostart.py
Cross-platform "start at login" manager for Systema Auxilium.

Stdlib-only, and a sibling of systema/common/shortcuts.py — it reuses that
module's launch-target helpers so autostart always launches exactly the same
thing the desktop shortcut / normal launch does: the generated RUN SCRIPT
(run.bat / run.sh / run.command), falling back to the venv python + main.py only
when no run script exists yet.

Autostart is **per-user and normal-privilege only** on every OS. Elevated
autostart is deliberately NOT supported: Windows silently ignores elevated
Startup-folder items at login, and Linux pkexec/​macOS admin prompts don't fire
in the autostart context — so it never worked and only caused confusion.

  • Windows — a .lnk in the user's Startup folder
               (%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup).
  • Linux    — an XDG autostart .desktop in ~/.config/autostart/ that runs
               `bash "run.sh"` (via bash so a missing +x bit / noexec mount can't
               silently stop it from launching at login).
  • macOS    — a LaunchAgent plist in ~/Library/LaunchAgents/ (RunAtLoad).

Every public function returns (ok: bool, message: str) or a status dict; nothing
raises, so UI callers can show the message directly.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from systema.common import shortcuts as _sc

APP_NAME = _sc.APP_NAME
ROOT = _sc.ROOT
IS_WIN, IS_MAC, IS_LINUX = _sc.IS_WIN, _sc.IS_MAC, _sc.IS_LINUX
_NO_WINDOW = _sc._NO_WINDOW

_MAC_LABEL = "com.systema-auxilium.autostart"
_LINUX_DESKTOP = "systema-auxilium.desktop"


# ── target path per OS ────────────────────────────────────────────────────────
def _win_startup_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def autostart_path() -> Path:
    """Where the autostart entry lives for this OS."""
    if IS_WIN:
        return _win_startup_dir() / f"{APP_NAME}.lnk"
    if IS_MAC:
        return Path.home() / "Library" / "LaunchAgents" / f"{_MAC_LABEL}.plist"
    return Path.home() / ".config" / "autostart" / _LINUX_DESKTOP


def _log(msg: str) -> None:
    """Breadcrumb to stdout (captured by the app's session-log _Tee) so a
    login-time autostart failure is diagnosable."""
    try:
        print(f"[autostart] {msg}")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Windows — a .lnk in the Startup folder pointing at run.bat (normal privilege)
# ══════════════════════════════════════════════════════════════════════════════
def _win_enable():
    script = _sc._run_script()
    if script is not None:
        target, arguments = str(script), ""
    else:
        exe, main = _sc._launch_parts()
        target, arguments = exe, '"' + main + '"'
    lnk = autostart_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    icon = str(_sc.ICON_ICO) if _sc.ICON_ICO.exists() else target
    q = _sc._ps_quote
    ps = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut({q(str(lnk))})
$s.TargetPath = {q(target)}
$s.Arguments = {q(arguments)}
$s.WorkingDirectory = {q(str(ROOT))}
$s.IconLocation = {q(icon)}
$s.WindowStyle = 7
$s.Description = {q(APP_NAME + ' — start at login')}
$s.Save()
"""
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    if r.returncode != 0 or not lnk.exists():
        return False, f"Could not enable autostart: {r.stderr.strip() or 'PowerShell failed'}"
    _log(f"windows startup .lnk -> {target} {arguments}".strip())
    return True, "Systema Auxilium will now start automatically at login."


# ══════════════════════════════════════════════════════════════════════════════
# Linux — XDG autostart .desktop running `bash "run.sh"`
# ══════════════════════════════════════════════════════════════════════════════
def _linux_exec_line() -> str:
    """Launch the run script through bash (robust to a missing +x bit / noexec),
    falling back to the venv python + main.py when no run script exists yet."""
    script = _sc._run_script()
    if script is not None:
        try:
            os.chmod(script, 0o755)
        except Exception:
            pass
        return f'bash "{script}"'
    exe, main = _sc._launch_parts()
    return f'"{exe}" "{main}"'


def _linux_enable():
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    icon = str(_sc.ICON_ICO) if _sc.ICON_ICO.exists() else APP_NAME.lower().replace(" ", "-")
    exec_line = _linux_exec_line()
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Systema Auxilium AI Desktop Assistant\n"
        f"Exec={exec_line}\n"
        f"Path={ROOT}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Hidden=false\n"
        "NoDisplay=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    try:
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o755)
    except Exception as e:
        return False, f"Could not enable autostart: {e}"
    _log(f"linux autostart .desktop -> Exec={exec_line}")
    return True, "Systema Auxilium will now start automatically at login."


# ══════════════════════════════════════════════════════════════════════════════
# macOS — LaunchAgent (RunAtLoad) running run.command
# ══════════════════════════════════════════════════════════════════════════════
def _mac_enable():
    script = _sc._run_script()
    if script is not None:
        try:
            os.chmod(script, 0o755)
        except Exception:
            pass
        prog = f'  <array><string>/bin/bash</string><string>{script}</string></array>\n'
        launched = f"bash {script}"
    else:
        exe, main = _sc._launch_parts()
        prog = f'  <array><string>{exe}</string><string>{main}</string></array>\n'
        launched = f"{exe} {main}"
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f'  <key>Label</key><string>{_MAC_LABEL}</string>\n'
        '  <key>ProgramArguments</key>\n'
        f'{prog}'
        f'  <key>WorkingDirectory</key><string>{ROOT}</string>\n'
        '  <key>RunAtLoad</key><true/>\n'
        '</dict>\n</plist>\n'
    )
    try:
        path.write_text(plist, encoding="utf-8")
    except Exception as e:
        return False, f"Could not enable autostart: {e}"
    try:
        subprocess.run(["launchctl", "load", str(path)], capture_output=True, text=True)
    except Exception:
        pass
    _log(f"macos launchagent -> {launched}")
    return True, "Systema Auxilium will now start automatically at login."


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════
def enable_autostart():
    """Enable start-at-login (per-user, normal privilege). Returns (ok, message)."""
    try:
        if IS_WIN:
            return _win_enable()
        if IS_MAC:
            return _mac_enable()
        return _linux_enable()
    except Exception as e:
        return False, f"Unexpected error: {e}"


def disable_autostart():
    """Disable start-at-login. Returns (ok, message)."""
    path = autostart_path()
    try:
        if IS_MAC and path.exists():
            try:
                subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
            except Exception:
                pass
        if path.exists():
            path.unlink()
            return True, "Autostart disabled."
        return True, "Autostart was not enabled."
    except Exception as e:
        return False, f"Could not disable autostart: {e}"


def run_now():
    """Launch the app immediately via the SAME run script autostart uses — for a
    'test autostart now' button. If the app is already running its single-instance
    guard will just show the 'already running' notice, which still proves the run
    script executes. Returns (ok, message)."""
    script = _sc._run_script()
    try:
        if IS_WIN:
            if script is not None:
                subprocess.Popen(["cmd", "/c", "call", str(script)], cwd=str(ROOT),
                                 creationflags=0x00000008 | 0x08000000)  # DETACHED|NO_WINDOW
            else:
                exe, main = _sc._launch_parts()
                subprocess.Popen([exe, main], cwd=str(ROOT),
                                 creationflags=0x00000008 | 0x08000000)
        else:
            if script is not None:
                subprocess.Popen(["bash", str(script)], cwd=str(ROOT), start_new_session=True)
            else:
                exe, main = _sc._launch_parts()
                subprocess.Popen([exe, main], cwd=str(ROOT), start_new_session=True)
        target = str(script) if script is not None else "python main.py"
        _log(f"run_now -> {target}")
        return True, f"Launched via the run script ({Path(target).name})."
    except Exception as e:
        return False, f"Could not launch: {e}"


def is_enabled() -> bool:
    """True if the autostart entry currently exists."""
    return autostart_path().exists()


def status() -> dict:
    """{'enabled': bool, 'path': str} — for UI state."""
    p = autostart_path()
    return {"enabled": p.exists(), "path": str(p)}
