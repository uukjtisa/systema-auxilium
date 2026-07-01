"""
systema/common/shortcuts.py
Cross-platform desktop-shortcut manager for Systema Auxilium.

Stdlib-only (no PyQt, no pywin32) so this module can be imported by BOTH the
running app (Settings → System → Shortcut) and the bare-Python setup.py before
any dependencies exist.

Supports create + a *hot* admin/normal privilege toggle on every OS:

  • Windows  — a .lnk shortcut. "Run as administrator" is bit 0x20 of the byte
               at offset 0x15 of the .lnk. Toggling flips ONLY that bit in place
               (other LinkFlags preserved), so switching admin↔normal never
               rebuilds the shortcut. Windows draws the UAC shield automatically.
  • Linux    — a .desktop launcher (marked executable + gio-trusted). Admin runs
               the app through `pkexec env DISPLAY=… XAUTHORITY=…` (polkit prompt).
  • macOS    — a .command file. Admin wraps the launch in
               `osascript … do shell script … with administrator privileges`.

Every public function returns (ok: bool, message: str) or a status dict; nothing
raises, so callers (UI buttons) can show the message directly.
"""
from __future__ import annotations

import os
import sys
import platform
import subprocess
from pathlib import Path

SYSTEM = platform.system()          # 'Windows' | 'Linux' | 'Darwin'
IS_WIN = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"
IS_LINUX = not IS_WIN and not IS_MAC

APP_NAME = "Systema Auxilium"
# Project root = two levels up from this file (systema/common/shortcuts.py).
ROOT = Path(__file__).resolve().parents[2]
ICON_ICO = ROOT / "assets" / "systema_auxilium.ico"

_NO_WINDOW = 0x08000000 if IS_WIN else 0   # CREATE_NO_WINDOW for quiet subprocess


# ── Paths & launch target ─────────────────────────────────────────────────────

def desktop_dir() -> Path:
    """The current user's real Desktop folder (honours OneDrive / XDG redirects)."""
    try:
        if IS_WIN:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Environment]::GetFolderPath('Desktop')"],
                capture_output=True, text=True, creationflags=_NO_WINDOW)
            p = out.stdout.strip()
            if p and Path(p).is_dir():
                return Path(p)
            return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        if IS_LINUX:
            out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True)
            p = out.stdout.strip()
            if p and Path(p).is_dir():
                return Path(p)
    except Exception:
        pass
    return Path.home() / "Desktop"


def shortcut_path() -> Path:
    """Where the Systema Auxilium desktop shortcut lives for this OS."""
    ext = ".lnk" if IS_WIN else (".command" if IS_MAC else ".desktop")
    return desktop_dir() / f"{APP_NAME}{ext}"


def _venv_python(windowless: bool = True) -> Path | None:
    """The venv interpreter, if a .venv exists. Prefers pythonw.exe on Windows so
    launching the GUI shows no console window."""
    if IS_WIN:
        for name in (("pythonw.exe", "python.exe") if windowless else ("python.exe",)):
            p = ROOT / ".venv" / "Scripts" / name
            if p.exists():
                return p
    else:
        p = ROOT / ".venv" / "bin" / "python"
        if p.exists():
            return p
    return None


def _launch_parts():
    """Return (executable, main_script) used to start the app. Falls back to the
    current interpreter when no venv is present yet."""
    py = _venv_python()
    exe = str(py) if py else sys.executable
    return exe, str(ROOT / "main.py")


# ══════════════════════════════════════════════════════════════════════════════
# Windows (.lnk)
# ══════════════════════════════════════════════════════════════════════════════

_LNK_ADMIN_OFFSET = 0x15
_LNK_ADMIN_BIT = 0x20


def _win_create(as_admin: bool):
    exe, main = _launch_parts()
    lnk = shortcut_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    icon = str(ICON_ICO) if ICON_ICO.exists() else exe
    # Build the .lnk with the WScript.Shell COM object via PowerShell (no pywin32).
    ps = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut({_ps_quote(str(lnk))})
$s.TargetPath = {_ps_quote(exe)}
$s.Arguments = {_ps_quote('"' + main + '"')}
$s.WorkingDirectory = {_ps_quote(str(ROOT))}
$s.IconLocation = {_ps_quote(icon)}
$s.Description = {_ps_quote(APP_NAME + ' AI Desktop Assistant')}
$s.Save()
"""
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    if r.returncode != 0 or not lnk.exists():
        return False, f"Could not create shortcut: {r.stderr.strip() or 'PowerShell failed'}"
    ok, msg = _win_set_admin_bit(as_admin)
    if not ok:
        return False, msg
    return True, f"Shortcut created on the Desktop ({'admin' if as_admin else 'normal'})."


def _win_set_admin_bit(as_admin: bool):
    """Flip ONLY the run-as-admin bit in the existing .lnk, preserving other flags."""
    lnk = shortcut_path()
    if not lnk.exists():
        return False, "Shortcut does not exist yet."
    try:
        data = bytearray(lnk.read_bytes())
        if len(data) <= _LNK_ADMIN_OFFSET:
            return False, "Shortcut file is malformed (too short)."
        if as_admin:
            data[_LNK_ADMIN_OFFSET] |= _LNK_ADMIN_BIT
        else:
            data[_LNK_ADMIN_OFFSET] &= ~_LNK_ADMIN_BIT
        lnk.write_bytes(data)
        return True, f"Shortcut set to {'run as administrator' if as_admin else 'normal'}."
    except Exception as e:
        return False, f"Could not edit shortcut privileges: {e}"


def _win_is_admin():
    lnk = shortcut_path()
    if not lnk.exists():
        return None
    try:
        data = lnk.read_bytes()
        if len(data) <= _LNK_ADMIN_OFFSET:
            return None
        return bool(data[_LNK_ADMIN_OFFSET] & _LNK_ADMIN_BIT)
    except Exception:
        return None


def _ps_quote(s: str) -> str:
    """Single-quote a string for PowerShell (double any internal single quotes)."""
    return "'" + s.replace("'", "''") + "'"


# ══════════════════════════════════════════════════════════════════════════════
# Linux (.desktop)
# ══════════════════════════════════════════════════════════════════════════════

def _linux_exec_line(as_admin: bool) -> str:
    exe, main = _launch_parts()
    base = f'"{exe}" "{main}"'
    if as_admin:
        # polkit elevation; sh -c so $DISPLAY/$XAUTHORITY expand for the GUI.
        inner = f'pkexec env DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY {base}'
        return f'sh -c \'{inner}\''
    return base


def _linux_create(as_admin: bool):
    path = shortcut_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    icon = str(ICON_ICO) if ICON_ICO.exists() else APP_NAME.lower().replace(" ", "-")
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Systema Auxilium AI Desktop Assistant\n"
        f"Exec={_linux_exec_line(as_admin)}\n"
        f"Path={ROOT}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
    )
    try:
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o755)
    except Exception as e:
        return False, f"Could not write shortcut: {e}"
    # Best-effort: mark trusted so GNOME lets it launch on double-click.
    try:
        subprocess.run(["gio", "set", str(path), "metadata::trusted", "true"],
                       capture_output=True, text=True)
    except Exception:
        pass
    return True, f"Shortcut created on the Desktop ({'admin' if as_admin else 'normal'})."


def _linux_is_admin():
    path = shortcut_path()
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
# macOS (.command)
# ══════════════════════════════════════════════════════════════════════════════

def _mac_create(as_admin: bool):
    exe, main = _launch_parts()
    path = shortcut_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if as_admin:
        # AppleScript prompts for the admin password natively, then runs elevated.
        inner = f'cd \\"{ROOT}\\" && \\"{exe}\\" \\"{main}\\"'
        body = (
            "#!/bin/bash\n"
            f"osascript -e 'do shell script \"{inner}\" with administrator privileges'\n"
        )
    else:
        body = (
            "#!/bin/bash\n"
            f'cd "{ROOT}"\n'
            f'"{exe}" "{main}"\n'
        )
    try:
        path.write_text(body, encoding="utf-8")
        os.chmod(path, 0o755)
    except Exception as e:
        return False, f"Could not write shortcut: {e}"
    return True, f"Shortcut created on the Desktop ({'admin' if as_admin else 'normal'})."


def _mac_is_admin():
    path = shortcut_path()
    if not path.exists():
        return None
    try:
        return "with administrator privileges" in path.read_text(encoding="utf-8")
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def create_shortcut(as_admin: bool = False):
    """Create (or overwrite) the desktop shortcut. Returns (ok, message)."""
    try:
        if IS_WIN:
            return _win_create(as_admin)
        if IS_MAC:
            return _mac_create(as_admin)
        return _linux_create(as_admin)
    except Exception as e:
        return False, f"Unexpected error: {e}"


def set_admin(as_admin: bool):
    """Hot-toggle an existing shortcut's privilege level (creates it if missing).
    Returns (ok, message)."""
    try:
        if not shortcut_path().exists():
            return create_shortcut(as_admin)
        if IS_WIN:
            return _win_set_admin_bit(as_admin)     # in-place bit flip, no rebuild
        # Linux/macOS: rewrite the launcher with the new privilege form.
        return (_mac_create if IS_MAC else _linux_create)(as_admin)
    except Exception as e:
        return False, f"Unexpected error: {e}"


def remove_shortcut():
    """Delete the desktop shortcut. Returns (ok, message)."""
    path = shortcut_path()
    try:
        if path.exists():
            path.unlink()
            return True, "Shortcut removed."
        return True, "No shortcut to remove."
    except Exception as e:
        return False, f"Could not remove shortcut: {e}"


def is_admin_shortcut():
    """True/False if a shortcut exists (admin state), or None if none exists."""
    if IS_WIN:
        return _win_is_admin()
    if IS_MAC:
        return _mac_is_admin()
    return _linux_is_admin()


def status() -> dict:
    """{'exists': bool, 'path': str, 'admin': bool|None} — for UI state."""
    p = shortcut_path()
    exists = p.exists()
    return {"exists": exists, "path": str(p),
            "admin": is_admin_shortcut() if exists else None}
