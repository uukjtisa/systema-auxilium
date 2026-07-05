"""
systema/common/autostart.py
Cross-platform "start at login" manager for Systema Auxilium.

Stdlib-only, and a sibling of systema/common/shortcuts.py — it reuses that
module's launch-target + privilege helpers so autostart always points at exactly
the same thing the desktop shortcut does (run script if present, else venv python
+ main.py) and supports the SAME admin / normal privilege toggle.

  • Windows — a .lnk in the user's Startup folder
               (%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup).
  • Linux    — an XDG autostart .desktop in ~/.config/autostart/.
  • macOS    — a LaunchAgent plist in ~/Library/LaunchAgents/ (RunAtLoad).

Privilege toggle (`as_admin`) mirrors shortcuts.py: Windows flips the .lnk
run-as-admin bit in place; Linux launches through `pkexec`; macOS wraps the
launch in an admin `osascript`.

⚠  Admin autostart is inherently awkward — NORMAL is recommended and is the
   default. Admin is opt-in:
     • Windows — an elevated Startup .lnk pops a UAC prompt at every login, and
       some Windows builds skip elevated Startup items entirely.
     • Linux   — pkexec prompts for a password at every login.
     • macOS   — a per-user LaunchAgent cannot truly elevate; the admin wrapper
       prompts for a password at each login.

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
# Windows — a .lnk in the Startup folder, with an optional run-as-admin bit
# ══════════════════════════════════════════════════════════════════════════════
def _win_enable(as_admin: bool = False):
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
    ok, msg = _win_set_admin_bit(as_admin)
    if not ok:
        return False, msg
    return True, ("Systema Auxilium will now start automatically at login"
                  + (" as administrator (a UAC prompt appears each login)." if as_admin else "."))


def _win_set_admin_bit(as_admin: bool):
    """Flip ONLY the run-as-admin bit in the Startup .lnk, preserving other flags.
    Reuses the same offset/bit constants shortcuts.py uses for the desktop .lnk."""
    lnk = autostart_path()
    if not lnk.exists():
        return False, "Autostart entry does not exist yet."
    try:
        data = bytearray(lnk.read_bytes())
        off = _sc._LNK_ADMIN_OFFSET
        if len(data) <= off:
            return False, "Autostart shortcut is malformed (too short)."
        if as_admin:
            data[off] |= _sc._LNK_ADMIN_BIT
        else:
            data[off] &= ~_sc._LNK_ADMIN_BIT
        lnk.write_bytes(data)
        return True, "ok"
    except Exception as e:
        return False, f"Could not set autostart privileges: {e}"


def _win_is_admin():
    lnk = autostart_path()
    if not lnk.exists():
        return None
    try:
        data = lnk.read_bytes()
        off = _sc._LNK_ADMIN_OFFSET
        if len(data) <= off:
            return None
        return bool(data[off] & _sc._LNK_ADMIN_BIT)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Linux — XDG autostart .desktop (pkexec when admin)
# ══════════════════════════════════════════════════════════════════════════════
def _linux_enable(as_admin: bool = False):
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    icon = str(_sc.ICON_ICO) if _sc.ICON_ICO.exists() else APP_NAME.lower().replace(" ", "-")
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Systema Auxilium AI Desktop Assistant\n"
        f"Exec={_sc._linux_exec_line(as_admin)}\n"
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
    return True, ("Systema Auxilium will now start automatically at login"
                  + (" as administrator (pkexec prompts each login)." if as_admin else "."))


def _linux_is_admin():
    path = autostart_path()
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("Exec="):
                return "pkexec" in line
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# macOS — LaunchAgent (RunAtLoad); admin wraps the launch in an osascript prompt
# ══════════════════════════════════════════════════════════════════════════════
def _mac_enable(as_admin: bool = False):
    exe, main = _sc._launch_parts()
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if as_admin:
        # AppleScript prompts for the admin password at login, then runs elevated.
        # `&` must be XML-escaped inside the plist <string>.
        inner = f'cd \\"{ROOT}\\" &amp;&amp; \\"{exe}\\" \\"{main}\\"'
        prog = ("  <array>\n"
                "    <string>/bin/bash</string>\n"
                "    <string>-c</string>\n"
                f"    <string>osascript -e 'do shell script \"{inner}\" "
                "with administrator privileges'</string>\n"
                "  </array>\n")
    else:
        prog = f'  <array><string>{exe}</string><string>{main}</string></array>\n'
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
    return True, ("Systema Auxilium will now start automatically at login"
                  + (" as administrator (a password prompt appears each login)." if as_admin else "."))


def _mac_is_admin():
    path = autostart_path()
    if not path.exists():
        return None
    try:
        return "with administrator privileges" in path.read_text(encoding="utf-8")
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════
def enable_autostart(as_admin: bool = False):
    """Enable start-at-login (normal by default). Returns (ok, message)."""
    try:
        if IS_WIN:
            return _win_enable(as_admin)
        if IS_MAC:
            return _mac_enable(as_admin)
        return _linux_enable(as_admin)
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


def set_admin(as_admin: bool):
    """Set the privilege level of the autostart entry, enabling it if missing.
    Returns (ok, message). Mirrors shortcuts.set_admin()."""
    try:
        if not is_enabled():
            return enable_autostart(as_admin)
        if IS_WIN:
            ok, msg = _win_set_admin_bit(as_admin)     # in-place bit flip, no rebuild
            if not ok:
                return False, msg
            return True, f"Autostart set to {'run as administrator' if as_admin else 'normal'}."
        # Linux / macOS: rewrite the entry with the new privilege form.
        return enable_autostart(as_admin)
    except Exception as e:
        return False, f"Unexpected error: {e}"


def is_enabled() -> bool:
    """True if the autostart entry currently exists."""
    return autostart_path().exists()


def is_admin():
    """True/False for the current entry's privilege level, or None if disabled."""
    if not is_enabled():
        return None
    if IS_WIN:
        return _win_is_admin()
    if IS_MAC:
        return _mac_is_admin()
    return _linux_is_admin()


def status() -> dict:
    """{'enabled': bool, 'path': str, 'admin': bool|None} — for UI state."""
    p = autostart_path()
    enabled = p.exists()
    return {"enabled": enabled, "path": str(p),
            "admin": is_admin() if enabled else None}
