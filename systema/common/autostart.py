"""
systema/common/autostart.py
Cross-platform "start at login" manager for Systema Auxilium.

Stdlib-only, and a sibling of systema/common/shortcuts.py — it reuses that
module's launch-target helpers so autostart always points at exactly the same
thing the desktop shortcut does (run script if present, else venv python +
main.py). Driven from Settings → General → Start at login. No admin required.

  • Windows — a .lnk in the user's Startup folder
               (%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup).
  • Linux    — an XDG autostart .desktop in ~/.config/autostart/.
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


# ══════════════════════════════════════════════════════════════════════════════
# Windows — a .lnk in the Startup folder (launches on login, no admin)
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
    return True, "Systema Auxilium will now start automatically at login."


# ══════════════════════════════════════════════════════════════════════════════
# Linux — XDG autostart .desktop
# ══════════════════════════════════════════════════════════════════════════════
def _linux_enable():
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    icon = str(_sc.ICON_ICO) if _sc.ICON_ICO.exists() else APP_NAME.lower().replace(" ", "-")
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Systema Auxilium AI Desktop Assistant\n"
        f"Exec={_sc._linux_exec_line(as_admin=False)}\n"
        f"Path={ROOT}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    try:
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o755)
    except Exception as e:
        return False, f"Could not enable autostart: {e}"
    return True, "Systema Auxilium will now start automatically at login."


# ══════════════════════════════════════════════════════════════════════════════
# macOS — LaunchAgent (RunAtLoad)
# ══════════════════════════════════════════════════════════════════════════════
def _mac_enable():
    exe, main = _sc._launch_parts()
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f'  <key>Label</key><string>{_MAC_LABEL}</string>\n'
        '  <key>ProgramArguments</key>\n'
        f'  <array><string>{exe}</string><string>{main}</string></array>\n'
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
    return True, "Systema Auxilium will now start automatically at login."


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════
def enable_autostart():
    """Enable start-at-login. Returns (ok, message)."""
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


def is_enabled() -> bool:
    """True if the autostart entry currently exists."""
    return autostart_path().exists()


def status() -> dict:
    """{'enabled': bool, 'path': str} — for UI state."""
    p = autostart_path()
    return {"enabled": p.exists(), "path": str(p)}
