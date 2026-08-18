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
from functools import lru_cache
from pathlib import Path

SYSTEM = platform.system()          # 'Windows' | 'Linux' | 'Darwin'
IS_WIN = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"
IS_LINUX = not IS_WIN and not IS_MAC

APP_NAME = "Systema Auxilium"
# Project root = two levels up from this file (systema/common/shortcuts.py).
ROOT = Path(__file__).resolve().parents[2]
ICON_ICO = ROOT / "resources" / "assets" / "systema_auxilium.ico"

_NO_WINDOW = 0x08000000 if IS_WIN else 0   # CREATE_NO_WINDOW for quiet subprocess


# ── Paths & launch target ─────────────────────────────────────────────────────

def _win_desktop_from_registry():
    """The Desktop path straight out of the user's shell-folder registry key.

    This is where Explorer itself stores the (possibly OneDrive-redirected)
    location, so it answers the same question the PowerShell call below does —
    in microseconds instead of spawning a process. Returns None if anything is
    off, so the caller can fall back.
    """
    try:
        import winreg
        key = (r"Software\Microsoft\Windows\CurrentVersion"
               r"\Explorer\User Shell Folders")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            raw, _ = winreg.QueryValueEx(handle, "Desktop")
        path = Path(os.path.expandvars(raw))
        return path if path.is_dir() else None
    except Exception:
        return None


@lru_cache(maxsize=1)
def desktop_dir() -> Path:
    """The real Desktop folder to place the shortcut in. When the app runs elevated
    (sudo/pkexec), this is the INVOKING login user's Desktop, NOT root's — otherwise
    the shortcut lands on /root/Desktop and the user's session never shows it (and
    removal can't find it). Honours OneDrive / XDG redirects for the normal case.

    CACHED, and on Windows the registry is tried before PowerShell. This used to
    spawn `powershell -NoProfile -Command [Environment]::GetFolderPath('Desktop')`
    on every call, from the GUI thread — PowerShell start-up is a few hundred ms
    cold, and `settings_window.__init__` reaches this during a click. It was the
    single worst stall on record: a 3226 ms freeze opening Settings
    (perf report 2026-08-18_20-15-36). The answer cannot change while the app
    runs, so computing it more than once was never useful either.
    """
    try:
        if IS_WIN:
            from_registry = _win_desktop_from_registry()
            if from_registry is not None:
                return from_registry
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Environment]::GetFolderPath('Desktop')"],
                capture_output=True, text=True, creationflags=_NO_WINDOW)
            p = out.stdout.strip()
            if p and Path(p).is_dir():
                return Path(p)
            return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        # Elevated: the login user's Desktop directly (xdg-user-dir as root would
        # return root's redirect, not theirs).
        tu = _target_user()
        if tu is not None:
            return tu[1] / "Desktop"
        if IS_LINUX:
            home = Path.home()
            out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True)
            p = out.stdout.strip()
            # xdg-user-dir returns $HOME itself when no Desktop is configured — in
            # that case DON'T drop the launcher loose in $HOME; prefer ~/Desktop so
            # it stays findable and removable.
            if p and Path(p).is_dir() and Path(p) != home:
                return Path(p)
    except Exception:
        pass
    return _home() / "Desktop"


def _shortcut_name() -> str:
    ext = ".lnk" if IS_WIN else (".command" if IS_MAC else ".desktop")
    return f"{APP_NAME}{ext}"


def shortcut_path() -> Path:
    """Primary location of the Systema Auxilium desktop shortcut for this OS."""
    return desktop_dir() / _shortcut_name()


def _shortcut_candidates() -> list:
    """All plausible locations the shortcut could live, so status/removal never
    strand a launcher when desktop-dir resolution drifts (xdg vs ~/Desktop vs $HOME).
    Primary (shortcut_path) first; de-duplicated, order preserved."""
    name = _shortcut_name()
    cands = [shortcut_path()]
    if not IS_WIN:
        home = _home()      # login user's home when elevated (not root's)
        for d in (home / "Desktop", home):
            cands.append(d / name)
    seen, ordered = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


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


def _run_script() -> Path | None:
    """The dedicated launcher generated by setup.py for THIS OS, if it exists.

    Windows -> run.bat, macOS -> run.command, Linux -> run.sh. Preferred as the
    shortcut target so the shortcut behaves exactly like the app's normal launch
    (venv activation, working dir, any custom steps) instead of calling main.py
    directly. Falls back to the interpreter + main.py when no run script exists.
    """
    name = "run.bat" if IS_WIN else ("run.command" if IS_MAC else "run.sh")
    p = ROOT / name
    return p if p.exists() else None


# ── invoking login user (when the app runs elevated) ──────────────────────────
def _target_user():
    """The real LOGIN user to install user-visible entries (desktop shortcut,
    autostart) for. When the app runs ELEVATED (euid 0) via sudo/pkexec from a
    normal user's desktop session, Path.home() is /root — so a shortcut would be
    created on /root/Desktop and an autostart entry in /root/.config, neither of
    which the user's own session shows or reads (this is why creating/removing the
    shortcut appeared broken). This resolves the invoking user (SUDO_USER /
    PKEXEC_UID). Returns (name, home, uid, gid), or None when we should just use the
    current user (Windows, not elevated, or a genuine root session)."""
    if IS_WIN:
        return None      # UAC elevation keeps the same user profile/home
    try:
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            return None
        import pwd as _pwd
        su = os.environ.get("SUDO_USER")
        if su and not su.isdigit():
            pw = _pwd.getpwnam(su)
        else:
            uid = os.environ.get("PKEXEC_UID") or (su if (su and su.isdigit()) else None)
            if not uid:
                return None
            pw = _pwd.getpwuid(int(uid))
        if pw.pw_uid == 0:
            return None
        return (pw.pw_name, Path(pw.pw_dir), pw.pw_uid, pw.pw_gid)
    except Exception:
        return None


def _home() -> Path:
    """Home of the login user we're installing for (see _target_user)."""
    tu = _target_user()
    return tu[1] if tu is not None else Path.home()


def _chown_to_target(path) -> None:
    """chown a generated entry to the login user when elevated, so their session
    owns it instead of a root-owned file sitting in their home/Desktop."""
    tu = _target_user()
    if tu is None:
        return
    try:
        os.chown(path, tu[2], tu[3])
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Windows (.lnk)
# ══════════════════════════════════════════════════════════════════════════════

_LNK_ADMIN_OFFSET = 0x15
_LNK_ADMIN_BIT = 0x20


def _win_create(as_admin: bool):
    script = _run_script()
    if script is not None:
        # Target run.bat directly; start it minimized so its console isn't intrusive.
        target = str(script)
        arguments = ""
        window_style = 7            # 7 = minimized
    else:
        exe, main = _launch_parts()
        target = exe
        arguments = '"' + main + '"'
        window_style = 1            # 1 = normal (pythonw shows no window anyway)
    lnk = shortcut_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    icon = str(ICON_ICO) if ICON_ICO.exists() else target
    # Build the .lnk with the WScript.Shell COM object via PowerShell (no pywin32).
    ps = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut({_ps_quote(str(lnk))})
$s.TargetPath = {_ps_quote(target)}
$s.Arguments = {_ps_quote(arguments)}
$s.WorkingDirectory = {_ps_quote(str(ROOT))}
$s.IconLocation = {_ps_quote(icon)}
$s.WindowStyle = {window_style}
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


def _win_is_admin(lnk: "Path | None" = None):
    lnk = lnk or shortcut_path()
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
    script = _run_script()
    if script is not None:
        try:
            os.chmod(script, 0o755)          # make sure run.sh is executable
        except Exception:
            pass
        base = f'"{script}"'
    else:
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
        _chown_to_target(path)      # own it as the login user when elevated
    except Exception as e:
        return False, f"Could not write shortcut: {e}"
    # Best-effort: mark trusted so GNOME lets it launch on double-click.
    try:
        subprocess.run(["gio", "set", str(path), "metadata::trusted", "true"],
                       capture_output=True, text=True)
    except Exception:
        pass
    return True, f"Shortcut created on the Desktop ({'admin' if as_admin else 'normal'})."


def _linux_is_admin(path: "Path | None" = None):
    path = path or shortcut_path()
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
    script = _run_script()
    if script is not None:
        try:
            os.chmod(script, 0o755)          # make sure run.command is executable
        except Exception:
            pass
        launch = f'\\"{script}\\"'
        plain = f'"{script}"'
    else:
        exe, main = _launch_parts()
        launch = f'\\"{exe}\\" \\"{main}\\"'
        plain = f'"{exe}" "{main}"'
    path = shortcut_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if as_admin:
        # AppleScript prompts for the admin password natively, then runs elevated.
        inner = f'cd \\"{ROOT}\\" && {launch}'
        body = (
            "#!/bin/bash\n"
            f"osascript -e 'do shell script \"{inner}\" with administrator privileges'\n"
        )
    else:
        body = (
            "#!/bin/bash\n"
            f'cd "{ROOT}"\n'
            f'{plain}\n'
        )
    try:
        path.write_text(body, encoding="utf-8")
        os.chmod(path, 0o755)
        _chown_to_target(path)      # own it as the login user when elevated
    except Exception as e:
        return False, f"Could not write shortcut: {e}"
    return True, f"Shortcut created on the Desktop ({'admin' if as_admin else 'normal'})."


def _mac_is_admin(path: "Path | None" = None):
    path = path or shortcut_path()
    if not path.exists():
        return None
    try:
        return "with administrator privileges" in path.read_text(encoding="utf-8")
    except Exception:
        return None


def _admin_at(path: "Path") -> "bool | None":
    """Privilege level of a shortcut at a specific path (for candidate scanning)."""
    if IS_WIN:
        return _win_is_admin(path)
    if IS_MAC:
        return _mac_is_admin(path)
    return _linux_is_admin(path)


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
    """Delete the desktop shortcut. Scans every candidate location so a path drift
    (xdg-user-dir vs ~/Desktop vs $HOME) can't strand the launcher. Returns (ok, msg)."""
    removed = []
    try:
        for p in _shortcut_candidates():
            if p.exists():
                p.unlink()
                removed.append(str(p))
        if removed:
            return True, "Shortcut removed."
        return True, "No shortcut to remove."
    except Exception as e:
        return False, f"Could not remove shortcut: {e}"


def is_admin_shortcut():
    """True/False privilege of the existing shortcut (any candidate location), or
    None if none exists."""
    for p in _shortcut_candidates():
        if p.exists():
            return _admin_at(p)
    return None


def status() -> dict:
    """{'exists': bool, 'path': str, 'admin': bool|None} — for UI state. Reports the
    first shortcut found across candidate locations."""
    for p in _shortcut_candidates():
        if p.exists():
            return {"exists": True, "path": str(p), "admin": _admin_at(p)}
    return {"exists": False, "path": str(shortcut_path()), "admin": None}
