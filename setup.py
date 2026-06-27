#!/usr/bin/env python3
"""
setup.py — Systema Auxilium unified cross-platform setup
================================================================================
One script to replace setup.bat / setup.sh / setup_macOS.sh. Auto-detects your
OS and performs the matching setup:

  1. Create a .venv virtual environment (prefers Python 3.10, falls back).
  2. Generate the platform's helper scripts (run / open_env / add_autostart /
     remove_autostart) — .bat on Windows, .sh on Linux/macOS.
  3. Install dependencies from requirements.txt into the venv.
  4. Verify the venv's Python version.

Run it with any system Python:
    python setup.py        (Windows)
    python3 setup.py       (Linux / macOS)
================================================================================
"""

import os
import sys
import shutil
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
SYSTEM = platform.system()          # 'Windows' | 'Linux' | 'Darwin'
IS_WIN = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"
ARCHIVE_DIR = ROOT / "setup-scripts"   # created on demand only if the user hides setup.py


def banner(text):
    print("=" * 40)
    print(f" {text}")
    print("=" * 40)
    print()


# ──────────────────────────────────────────────────────────────────────────────
# [1/5] Virtual environment
# ──────────────────────────────────────────────────────────────────────────────

def find_python310():
    """Return an argv prefix that invokes Python 3.10, or None if unavailable."""
    candidates = []
    if IS_WIN:
        candidates.append(["py", "-3.10"])
    candidates.append(["python3.10"])
    for argv in candidates:
        try:
            out = subprocess.run(argv + ["--version"], capture_output=True, text=True)
            if out.returncode == 0 and "3.10" in (out.stdout + out.stderr):
                return argv
        except (FileNotFoundError, OSError):
            continue
    # macOS Homebrew python@3.10
    if IS_MAC:
        try:
            prefix = subprocess.run(["brew", "--prefix", "python@3.10"],
                                    capture_output=True, text=True)
            if prefix.returncode == 0:
                py = Path(prefix.stdout.strip()) / "bin" / "python3.10"
                if py.exists():
                    return [str(py)]
        except (FileNotFoundError, OSError):
            pass
    return None


def create_venv():
    print("[1/5] Creating virtual environment (.venv)...")
    if VENV.exists():
        print("       .venv already exists — reusing it.")
        return
    argv310 = find_python310()
    if argv310:
        print(f"       Python 3.10 detected ({' '.join(argv310)}). Creating venv...")
        base = argv310
    else:
        print("       Python 3.10 not found. Using current Python "
              f"({platform.python_version()})...")
        base = [sys.executable]
    subprocess.run(base + ["-m", "venv", str(VENV)], check=True)
    print()


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


# ──────────────────────────────────────────────────────────────────────────────
# [2/5] Helper scripts (platform-specific) — identical to the legacy scripts
# ──────────────────────────────────────────────────────────────────────────────

_WIN_HELPERS = {
    "open_env.bat": r'''@echo off
start "" cmd /k "%~dp0.venv\Scripts\activate.bat"
''',
    "run.bat": r'''@echo off
cd /d "%~dp0"
call "%~dp0.venv\Scripts\activate.bat"
python "%~dp0main.py"
pause
''',
    "add_autostart.bat": r'''@echo off
REM Adds Systema Auxilium to start at user login WITH ADMIN PRIVILEGES
REM Works for ANY user on ANY computer

set "SCRIPT_DIR=%~dp0"
set "RUN_BAT=%SCRIPT_DIR%run.bat"

schtasks /create /tn "SystemaAuxilium_AutoStart" /tr "\"%RUN_BAT%\"" /sc onlogon /ru "%USERDOMAIN%\%USERNAME%" /rl HIGHEST /f

if %ERRORLEVEL% EQU 0 (
    echo Scheduled task 'SystemaAuxilium_AutoStart' added successfully with admin rights.
    echo Task points to: %RUN_BAT%
    echo Running as user: %USERDOMAIN%\%USERNAME%
) else (
    echo Failed to create task. Please run this script as Administrator.
)
pause
''',
    "remove_autostart.bat": r'''@echo off
REM Removes Systema Auxilium auto-start scheduled task
REM Works for ANY user on ANY computer

echo Removing scheduled task 'SystemaAuxilium_AutoStart'...
schtasks /delete /tn "SystemaAuxilium_AutoStart" /f

if %ERRORLEVEL% EQU 0 (
    echo Scheduled task 'SystemaAuxilium_AutoStart' removed successfully.
    echo Task was running as: %USERDOMAIN%\%USERNAME%
) else (
    echo Failed to remove task. It may not exist or you need admin privileges.
)
pause
''',
}

_NIX_HELPERS = {
    "open_env.sh": r'''#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash --rcfile <(echo "source \"$SCRIPT_DIR/.venv/bin/activate\"; echo 'Systema Auxilium venv activated.'")
''',
    "run.sh": r'''#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
python "$SCRIPT_DIR/main.py"
''',
    "remove_autostart.sh": None,   # filled per-platform below
    "add_autostart.sh": None,      # filled per-platform below
}

_LINUX_ADD_AUTOSTART = r'''#!/bin/bash
# Adds Systema Auxilium to autostart via XDG autostart (.desktop file)
# Works on GNOME, KDE, XFCE, and most desktop environments

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SH="$SCRIPT_DIR/run.sh"
AUTOSTART_DIR="$HOME/.config/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/systema-auxilium.desktop"

mkdir -p "$AUTOSTART_DIR"

cat > "$DESKTOP_FILE" << DESKTOP
[Desktop Entry]
Type=Application
Name=Systema Auxilium
Exec=bash "$RUN_SH"
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Comment=Systema Auxilium AI Desktop Assistant
DESKTOP

echo "Autostart entry created at: $DESKTOP_FILE"
echo "Systema Auxilium will launch on next login."
'''

_LINUX_REMOVE_AUTOSTART = r'''#!/bin/bash
# Removes Systema Auxilium autostart entry

DESKTOP_FILE="$HOME/.config/autostart/systema-auxilium.desktop"

if [ -f "$DESKTOP_FILE" ]; then
    rm "$DESKTOP_FILE"
    echo "Autostart entry removed: $DESKTOP_FILE"
else
    echo "No autostart entry found at: $DESKTOP_FILE"
fi
'''

_MAC_ADD_AUTOSTART = r'''#!/bin/bash
# Adds Systema Auxilium to autostart via launchd (native macOS)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SH="$SCRIPT_DIR/run.sh"
APP_LABEL="com.nic2007.systema-auxilium"
PLIST_PATH="$HOME/Library/LaunchAgents/$APP_LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$APP_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$RUN_SH</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/systema-auxilium.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/systema-auxilium-error.log</string>
</dict>
</plist>
PLIST

launchctl load "$PLIST_PATH"
echo "Autostart registered with launchd: $PLIST_PATH"
echo "Systema Auxilium will launch on next login."
echo "Logs will be written to ~/Library/Logs/systema-auxilium.log"
'''

_MAC_REMOVE_AUTOSTART = r'''#!/bin/bash
# Removes Systema Auxilium from launchd autostart

APP_LABEL="com.nic2007.systema-auxilium"
PLIST_PATH="$HOME/Library/LaunchAgents/$APP_LABEL.plist"

if [ -f "$PLIST_PATH" ]; then
    launchctl unload "$PLIST_PATH"
    rm "$PLIST_PATH"
    echo "Autostart entry removed: $PLIST_PATH"
else
    echo "No autostart entry found at: $PLIST_PATH"
fi
'''


def write_helpers():
    print("[2/5] Creating helper scripts...")
    if IS_WIN:
        helpers = _WIN_HELPERS
    else:
        helpers = dict(_NIX_HELPERS)
        if IS_MAC:
            helpers["add_autostart.sh"] = _MAC_ADD_AUTOSTART
            helpers["remove_autostart.sh"] = _MAC_REMOVE_AUTOSTART
        else:
            helpers["add_autostart.sh"] = _LINUX_ADD_AUTOSTART
            helpers["remove_autostart.sh"] = _LINUX_REMOVE_AUTOSTART

    for name, content in helpers.items():
        path = ROOT / name
        path.write_text(content, encoding="utf-8")
        if not IS_WIN:
            os.chmod(path, 0o755)
        print(f"   - {name}")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# [3/5]+[4/5] Install dependencies into the venv
# ──────────────────────────────────────────────────────────────────────────────

def install_deps():
    print("[3/5] Upgrading pip in the venv...")
    py = venv_python()
    if not py.exists():
        print(f"WARNING: venv python not found at {py} — skipping dependency install.")
        return
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"],
                   check=False)
    print()
    print("[4/5] Installing dependencies from requirements.txt...")
    req = ROOT / "requirements.txt"
    if req.exists():
        result = subprocess.run([str(py), "-m", "pip", "install", "-r", str(req)],
                                check=False)
        if result.returncode == 0:
            print("Dependencies installed successfully!")
        else:
            print("WARNING: Some dependencies failed to install.")
    else:
        print("WARNING: No requirements.txt found. Skipping dependency installation.")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# [5/5] Verify
# ──────────────────────────────────────────────────────────────────────────────

def verify():
    print("[5/5] Verifying Python version in venv...")
    py = venv_python()
    if py.exists():
        subprocess.run([str(py), "--version"], check=False)
    else:
        print("WARNING: venv python missing — setup may be incomplete.")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# Optionally hide this setup.py after a successful run
# ──────────────────────────────────────────────────────────────────────────────

def _ask_yes_no(question, default_yes=True):
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        answer = input(f"{question} {suffix} ").strip().lower()
    except EOFError:
        return default_yes  # non-interactive → take the default
    if not answer:
        return default_yes
    return answer in ("y", "yes")


def maybe_hide_self():
    if _ask_yes_no("Hide setup.py too (move it into setup-scripts/)? "
                   "You can re-run it from there later.", default_yes=True):
        ARCHIVE_DIR.mkdir(exist_ok=True)
        target = ARCHIVE_DIR / "setup.py"
        try:
            shutil.move(str(Path(__file__).resolve()), str(target))
            print(f"setup.py moved to {target.relative_to(ROOT)}.")
            print("Re-run later with:  python setup-scripts/setup.py")
        except Exception as e:
            print(f"Could not move setup.py: {e}")
    else:
        print("setup.py kept in the project root for easy re-runs.")


# ──────────────────────────────────────────────────────────────────────────────

def main():
    banner(f"Systema Auxilium Setup  ·  {SYSTEM}")
    try:
        create_venv()
        write_helpers()
        install_deps()
        verify()
    except subprocess.CalledProcessError as e:
        print(f"\nERROR during setup: {e}")
        sys.exit(1)

    banner("Setup Complete!")
    if IS_WIN:
        print("Your environment is ready! You can now:")
        print("  - Run the app:        run.bat")
        print("  - Open venv terminal: open_env.bat")
        print("  - Enable autostart:   add_autostart.bat (as Admin)")
        print("  - Disable autostart:  remove_autostart.bat (as Admin)")
    else:
        print("Your environment is ready! You can now:")
        print("  - Run the app:        bash run.sh")
        print("  - Open venv terminal: bash open_env.sh")
        print("  - Enable autostart:   bash add_autostart.sh")
        print("  - Disable autostart:  bash remove_autostart.sh")
        if IS_MAC:
            print()
            print("Note: macOS may ask for permission to control your computer.")
            print("Grant it in System Settings → Privacy & Security → Accessibility.")
    print()

    maybe_hide_self()


if __name__ == "__main__":
    main()
