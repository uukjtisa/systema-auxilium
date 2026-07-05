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
_SYSTEMD_UNIT = "systema-auxilium.service"


# ── target path per OS ────────────────────────────────────────────────────────
def _win_startup_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _xdg_config_home() -> Path:
    """Honor $XDG_CONFIG_HOME (falling back to ~/.config). Getting this wrong is a
    real 'autostart doesn't fire' cause: the entry lands where the DE never looks."""
    x = os.environ.get("XDG_CONFIG_HOME")
    if x and x.strip():
        return Path(x.strip())
    return Path.home() / ".config"


def autostart_path() -> Path:
    """Where the XDG / native autostart entry lives for this OS."""
    if IS_WIN:
        return _win_startup_dir() / f"{APP_NAME}.lnk"
    if IS_MAC:
        return Path.home() / "Library" / "LaunchAgents" / f"{_MAC_LABEL}.plist"
    return _xdg_config_home() / "autostart" / _LINUX_DESKTOP


def _systemd_unit_path() -> Path:
    """Path of the optional systemd --user unit (the alternative Linux method)."""
    return _xdg_config_home() / "systemd" / "user" / _SYSTEMD_UNIT


def autostart_log_path() -> Path:
    """Login-time breadcrumb log written by the Linux launcher wrapper."""
    return ROOT / "data" / "logs" / "autostart.log"


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
def _linux_launcher_script():
    """Write (and return) the wrapper the Linux autostart entry runs at login.

    Both the XDG entry and the systemd unit run this. It (1) logs a breadcrumb to
    data/logs/autostart.log so a login-time autostart is diagnosable, (2) hardens
    PATH and cd's into the app root, (3) WAITS for the graphical session (polls for
    $DISPLAY / $WAYLAND_DISPLAY, up to ~30s) so the GUI has a display to attach to —
    beating the login race far better than a blind sleep — then (4) launches via the
    SAME run script normal launch uses, and (5) shows a notify-send toast if the
    launch fails. Returns the wrapper Path, or None if it couldn't be written."""
    script = _sc._run_script()
    if script is not None:
        try:
            os.chmod(script, 0o755)
        except Exception:
            pass
        launch = f'bash "{script}"'
        target = str(script)
    else:
        exe, main = _sc._launch_parts()
        launch = f'"{exe}" "{main}"'
        target = f"{exe} {main}"
    log_dir = ROOT / "data" / "logs"
    log_file = log_dir / "autostart.log"
    wrapper = ROOT / "data" / "autostart_launch.sh"
    content = (
        "#!/bin/bash\n"
        "# Auto-generated by Systema Auxilium autostart (systema/common/autostart.py).\n"
        "# Waits for the desktop session, then launches via the run script. Logs to\n"
        "# data/logs/autostart.log and toasts if the launch fails. Safe to delete —\n"
        "# regenerated whenever autostart is re-enabled.\n"
        f'ROOT="{ROOT}"\n'
        f'LOG_DIR="{log_dir}"\n'
        f'LOG="{log_file}"\n'
        'mkdir -p "$LOG_DIR"\n'
        'log(){ echo "[$(date \'+%Y-%m-%d %H:%M:%S\')] $*" >> "$LOG"; }\n'
        'log "autostart fired (pid $$)"\n'
        'export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOME/.local/bin:$PATH"\n'
        'cd "$ROOT" 2>/dev/null || true\n'
        '# Wait up to ~30s for a graphical session (X or Wayland) before launching.\n'
        'for i in $(seq 1 60); do\n'
        '  if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then\n'
        '    log "display ready (DISPLAY=\'$DISPLAY\' WAYLAND_DISPLAY=\'$WAYLAND_DISPLAY\')"; break\n'
        '  fi\n'
        '  sleep 0.5\n'
        'done\n'
        'if [ -z "$DISPLAY" ] && [ -z "$WAYLAND_DISPLAY" ]; then\n'
        '  log "WARNING: no display after wait — launching anyway"\n'
        'fi\n'
        f'log "launching -> {target}"\n'
        f'{launch} >> "$LOG" 2>&1\n'
        'rc=$?\n'
        'log "run script exited rc=$rc"\n'
        'if [ "$rc" -ne 0 ]; then\n'
        '  command -v notify-send >/dev/null 2>&1 && \\\n'
        '    notify-send "Systema Auxilium" "Autostart failed (exit $rc). See data/logs/autostart.log." || true\n'
        'fi\n'
    )
    try:
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(content, encoding="utf-8")
        os.chmod(wrapper, 0o755)
    except Exception:
        return None
    return wrapper


def _linux_exec_line() -> str:
    """Exec line for the autostart .desktop: runs a generated wrapper (breadcrumb
    + settle delay) that in turn launches the run script. Falls back to launching
    the run script (or venv python + main.py) directly if the wrapper can't be
    written — always through bash so a missing +x bit / noexec mount can't stop
    it from launching at login."""
    wrapper = _linux_launcher_script()
    if wrapper is not None:
        return f'bash "{wrapper}"'
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
    # A themable icon NAME (not the Windows .ico path — it won't resolve on Linux
    # and a broken icon looks off). Missing from the theme just shows a generic
    # icon, which is harmless and never disables the entry.
    icon = APP_NAME.lower().replace(" ", "-")
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
# Linux — optional systemd --user service (DE-independent fallback method)
# ══════════════════════════════════════════════════════════════════════════════
def _systemctl_user_available() -> bool:
    """True if a reachable systemd --user manager exists (so the fallback is usable)."""
    if not IS_LINUX:
        return False
    try:
        import shutil as _sh
        if _sh.which("systemctl") is None:
            return False
        r = subprocess.run(["systemctl", "--user", "is-system-running"],
                           capture_output=True, text=True, timeout=6)
        out = (r.stdout or "") + (r.stderr or "")
        # Any state (running / degraded / starting) means the manager answered;
        # only a connection failure means there's no user bus to enable into.
        return "Failed to connect" not in out and "not been booted" not in out
    except Exception:
        return False


def _systemd_is_enabled() -> bool:
    return _systemd_unit_path().exists()


def _systemd_enable():
    wrapper = _linux_launcher_script()
    if wrapper is None:
        return False, "Could not create the autostart launcher script."
    unit = _systemd_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[Unit]\n"
        f"Description={APP_NAME} (start at login)\n"
        "After=graphical-session.target\n"
        "PartOf=graphical-session.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f'ExecStart=/bin/bash "{wrapper}"\n'
        f"WorkingDirectory={ROOT}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    try:
        unit.write_text(content, encoding="utf-8")
    except Exception as e:
        return False, f"Could not write the systemd unit: {e}"
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       capture_output=True, text=True, timeout=10)
        r = subprocess.run(["systemctl", "--user", "enable", _SYSTEMD_UNIT],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            _log(f"systemd enable rc={r.returncode}: {(r.stderr or '').strip()}")
            return False, ("Wrote the unit but 'systemctl --user enable' failed: "
                           + ((r.stderr or "").strip() or "unknown error"))
    except Exception as e:
        return False, f"systemctl --user unavailable: {e}"
    _log(f"systemd user unit -> {unit}")
    return True, "Systema Auxilium will now start at login (systemd user service)."


def _systemd_disable():
    unit = _systemd_unit_path()
    try:
        if unit.exists():
            subprocess.run(["systemctl", "--user", "disable", _SYSTEMD_UNIT],
                           capture_output=True, text=True, timeout=10)
            unit.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"],
                           capture_output=True, text=True, timeout=10)
            return True, "Autostart (systemd) disabled."
        return True, "Autostart (systemd) was not enabled."
    except Exception as e:
        return False, f"Could not disable the systemd unit: {e}"


def _disable_linux_xdg():
    p = autostart_path()
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass


def _linux_method() -> str:
    """The Linux autostart method currently active (inferred from which entry
    exists): 'systemd', 'xdg', or 'none'."""
    if _systemd_is_enabled():
        return "systemd"
    if autostart_path().exists():
        return "xdg"
    return "none"


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
def enable_autostart(method: str = "xdg"):
    """Enable start-at-login (per-user, normal privilege). Returns (ok, message).

    On Linux ``method`` selects the mechanism: 'xdg' (default — the desktop
    autostart entry, the professional standard for GUI apps) or 'systemd' (a
    --user service; a DE-independent fallback). The two are MUTUALLY EXCLUSIVE —
    enabling one disables the other — so a login never double-launches (which would
    otherwise pop the 'already running' notice)."""
    try:
        if IS_WIN:
            return _win_enable()
        if IS_MAC:
            return _mac_enable()
        if str(method).lower() == "systemd":
            _disable_linux_xdg()
            return _systemd_enable()
        _systemd_disable()
        return _linux_enable()
    except Exception as e:
        return False, f"Unexpected error: {e}"


def disable_autostart():
    """Disable start-at-login (removes every mechanism). Returns (ok, message)."""
    try:
        if IS_LINUX:
            had = _systemd_is_enabled() or autostart_path().exists()
            _systemd_disable()
            _disable_linux_xdg()
            return True, ("Autostart disabled." if had else "Autostart was not enabled.")
        path = autostart_path()
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
    """True if any autostart entry currently exists."""
    if IS_LINUX:
        return _linux_method() != "none"
    return autostart_path().exists()


def status() -> dict:
    """{'enabled': bool, 'path': str, 'method': str} — for UI state."""
    if IS_LINUX:
        m = _linux_method()
        p = _systemd_unit_path() if m == "systemd" else autostart_path()
        return {"enabled": m != "none", "path": str(p), "method": m}
    p = autostart_path()
    return {"enabled": p.exists(), "path": str(p),
            "method": ("native" if p.exists() else "none")}


def diagnose() -> str:
    """Human-readable autostart 'doctor' report for Settings ▸ Diagnose. Surfaces
    exactly the facts that decide whether a login-time autostart fires, plus the
    tail of the launcher log — so a no-show can be diagnosed on the user's machine
    instead of guessed at remotely."""
    lines = []
    osname = "Windows" if IS_WIN else "macOS" if IS_MAC else "Linux"
    st = status()
    lines.append(f"OS: {osname}")
    lines.append(f"Enabled: {st['enabled']}   (method: {st.get('method')})")
    lines.append(f"Entry: {st['path']}")
    try:
        lines.append(f"  exists: {Path(st['path']).exists()}")
    except Exception:
        pass
    if IS_LINUX:
        lines.append(f"XDG_CONFIG_HOME: {os.environ.get('XDG_CONFIG_HOME') or '(unset → ~/.config)'}")
        lines.append(f"Session: DISPLAY={os.environ.get('DISPLAY') or '(none)'}  "
                     f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY') or '(none)'}")
        lines.append(f"systemctl --user available: {_systemctl_user_available()}")
        lines.append(f"XDG .desktop: {autostart_path()}  (exists: {autostart_path().exists()})")
        lines.append(f"systemd unit: {_systemd_unit_path()}  (exists: {_systemd_is_enabled()})")
        wrapper = ROOT / "data" / "autostart_launch.sh"
        w_exec = os.access(wrapper, os.X_OK) if wrapper.exists() else False
        lines.append(f"Launcher: {wrapper}  (exists: {wrapper.exists()}, exec: {w_exec})")
        run = _sc._run_script()
        lines.append(f"Run script: {run}  (exists: {run.exists() if run else False})")
    logf = autostart_log_path()
    lines.append("")
    lines.append(f"--- {logf} (last 15 lines) ---")
    if logf.exists():
        try:
            tail = logf.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
            lines.extend(tail or ["(empty)"])
        except Exception as e:
            lines.append(f"(could not read: {e})")
    else:
        lines.append("(no log yet — the login autostart hasn't fired since it was enabled)")
    return "\n".join(lines)
