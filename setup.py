#!/usr/bin/env python3
"""
setup.py — Systema Auxilium unified cross-platform installer
================================================================================
One script for Windows / Linux / macOS. Auto-detects your OS and:

  1. Creates a .venv virtual environment (prefers Python 3.10, falls back).
  2. Lets you pick optional feature modules in an interactive TUI
     (essential modules are locked and always installed).
  3. Lets you pick pre-configuration steps (desktop shortcut, autostart).
  4. Installs the chosen dependencies into the venv.
  5. Generates the platform helper scripts (run / open_env / autostart).
  6. Applies your pre-configuration choices and verifies the venv.

Run it with any system Python:
    python setup.py        (Windows)
    python3 setup.py       (Linux / macOS)

Everything here is stdlib-only, so it runs before any dependency exists.
Non-interactive shells (no TTY) automatically fall back to sensible defaults.
================================================================================
"""

import os
import sys
import shutil
import platform
import subprocess
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
SYSTEM = platform.system()          # 'Windows' | 'Linux' | 'Darwin'
IS_WIN = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"
IS_LINUX = not IS_WIN and not IS_MAC
ARCHIVE_DIR = ROOT / "setup-scripts"

# ── ANSI palette (auto-disabled if the terminal can't do colour) ──────────────
_C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "cyan": "\033[36m", "green": "\033[32m", "yellow": "\033[33m",
    "red": "\033[31m", "blue": "\033[34m", "mag": "\033[35m",
    "grey": "\033[90m", "inv": "\033[7m",
}


def _enable_ansi() -> bool:
    """Turn on ANSI/VT processing. Returns True if colour output is usable."""
    if not sys.stdout.isatty():
        return False
    if IS_WIN:
        try:
            import ctypes
            k = ctypes.windll.kernel32
            for handle in (-11, -12):   # STDOUT, STDERR
                h = k.GetStdHandle(handle)
                mode = ctypes.c_uint32()
                if k.GetConsoleMode(h, ctypes.byref(mode)):
                    k.SetConsoleMode(h, mode.value | 0x0004)  # VT processing
        except Exception:
            return False
    return True


ANSI = _enable_ansi()


def c(text, *styles):
    if not ANSI:
        return text
    return "".join(_C[s] for s in styles) + text + _C["reset"]


def clear():
    if ANSI:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


# ══════════════════════════════════════════════════════════════════════════════
# ASCII banner
# ══════════════════════════════════════════════════════════════════════════════

_BANNER = r"""
   ____            _                          _             _ _ _
  / ___| _   _ ___| |_ ___ _ __ ___   __ _   / \  _   ___  _(_) (_)_   _ _ __ ___
  \___ \| | | / __| __/ _ \ '_ ` _ \ / _` | / _ \| | | \ \/ / | | | | | | '_ ` _ \
   ___) | |_| \__ \ ||  __/ | | | | | (_| |/ ___ \ |_| |>  <| | | | |_| | | | | | |
  |____/ \__, |___/\__\___|_| |_| |_|\__,_/_/   \_\__,_/_/\_\_|_|_|\__,_|_| |_| |_|
         |___/
"""


def show_banner():
    clear()
    print(c(_BANNER, "cyan", "bold"))
    tag = "  AI Desktop Assistant  ·  Cross-Platform Installer"
    print(c(tag, "grey"))
    print(c(f"  {SYSTEM} · Python {platform.python_version()}", "grey"))
    print(c("  " + "─" * 74, "grey"))
    print()


def step(text):
    print(c(f"▸ {text}", "cyan", "bold"))


def ok(text):
    print(c(f"  ✓ {text}", "green"))


def warn(text):
    print(c(f"  ! {text}", "yellow"))


def fail(text):
    print(c(f"  ✗ {text}", "red"))


# ══════════════════════════════════════════════════════════════════════════════
# Module & pre-configuration catalogue
# ══════════════════════════════════════════════════════════════════════════════

# Essentials are ALWAYS installed and cannot be unchecked.
ESSENTIAL = [
    "PyQt6", "Pillow", "pystray", "psutil", "send2trash", "pyautogui",
    "requests", "markdown2", "matplotlib", "pyparsing",
    "anthropic", "google-genai", "openai",
    "fastembed", "numpy", "onnxruntime==1.19.2",
    "sounddevice", "webrtcvad-wheels", "SpeechRecognition", "edge-tts", "pygame",
]

# Optional feature groups the user can toggle in the picker.
OPTIONAL_GROUPS = [
    {"key": "web",   "label": "Web-browser skill",
     "desc": "Live web search + page reading (beautifulsoup4, duckduckgo_search, trafilatura)",
     "default": True,  "pkgs": ["beautifulsoup4", "duckduckgo_search", "trafilatura"]},
    {"key": "data",  "label": "Data-viz skill",
     "desc": "DataFrame handling for the data/plot skill (pandas)",
     "default": True,  "pkgs": ["pandas"]},
    {"key": "tts",   "label": "Offline text-to-speech",
     "desc": "Local speech synthesis when edge-tts is unavailable (pyttsx3)",
     "default": False, "pkgs": ["pyttsx3"]},
    {"key": "vosk",  "label": "Offline speech-to-text (Vosk)",
     "desc": "Fully offline voice recognition (vosk — downloads a model separately)",
     "default": False, "pkgs": ["vosk"]},
    {"key": "llama", "label": "Local LLaMA models",
     "desc": "Run GGUF models on-device (llama-cpp-python — capable hardware only)",
     "default": False, "pkgs": ["llama-cpp-python"]},
]

# Pre-configuration steps applied after install.
PRECONFIG = [
    {"key": "shortcut", "label": "Create a Desktop shortcut",
     "desc": "Adds a Systema Auxilium launcher to your Desktop", "default": True},
    {"key": "admin",    "label": "  └ Launch that shortcut as administrator",
     "desc": "Shortcut requests elevated privileges (UAC / polkit / sudo)", "default": False},
    {"key": "autostart", "label": "Start automatically at login",
     "desc": "Registers Systema Auxilium to launch when you log in", "default": False},
]


# ══════════════════════════════════════════════════════════════════════════════
# Minimal cross-platform single-key reader + checklist TUI
# ══════════════════════════════════════════════════════════════════════════════

def _read_key():
    """Return one of: 'up','down','space','enter','toggle_all','skip','quit',
    or the lowercase character pressed. Blocks for a single keypress."""
    if IS_WIN:
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):           # arrow / function prefix
            ch2 = msvcrt.getwch()
            return {"H": "up", "P": "down"}.get(ch2, "")
        return _map_key(ch)
    # POSIX raw read
    import termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":                     # escape sequence
            seq = sys.stdin.read(2)
            return {"[A": "up", "[B": "down"}.get(seq, "quit")
        return _map_key(ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _map_key(ch):
    if ch in ("\r", "\n"):
        return "enter"
    if ch == " ":
        return "space"
    if ch in ("\x03", "\x1b", "q", "Q"):
        return "quit"
    if ch in ("a", "A"):
        return "toggle_all"
    if ch in ("s", "S"):
        return "skip"
    return ch.lower()


def checklist(title, items, subtitle=""):
    """Interactive checkbox list.

    items: list of dicts with keys: label, desc, checked(bool), locked(bool).
    Returns the (possibly mutated) items list; on non-TTY, returns defaults.
    Controls: ↑/↓ move · Space toggle · A all/none · S skip(defaults) · Enter confirm.
    """
    if not sys.stdin.isatty() or not ANSI:
        # Headless: accept defaults silently.
        return items

    idx = 0
    n = len(items)
    while True:
        clear()
        print(c(_BANNER, "cyan", "bold"))
        print(c(f"  {title}", "bold"))
        if subtitle:
            print(c(f"  {subtitle}", "grey"))
        print(c("  " + "─" * 74, "grey"))
        print()
        for i, it in enumerate(items):
            cursor = c("❯", "cyan", "bold") + " " if i == idx else "  "
            if it.get("locked"):
                box = c("[■]", "grey")
                lab = c(it["label"], "grey")
                tag = c(" locked", "grey", "dim")
            else:
                box = c("[✓]", "green") if it["checked"] else "[ ]"
                lab = c(it["label"], "bold") if i == idx else it["label"]
                tag = ""
            print(f"   {cursor}{box} {lab}{tag}")
            if i == idx and it.get("desc"):
                print(c(f"        {it['desc']}", "grey"))
        print()
        print(c("   ↑/↓ move   Space toggle   A all/none   S skip   Enter confirm",
                "grey"))
        sys.stdout.flush()

        key = _read_key()
        if key == "up":
            idx = (idx - 1) % n
        elif key == "down":
            idx = (idx + 1) % n
        elif key == "space":
            if not items[idx].get("locked"):
                items[idx]["checked"] = not items[idx]["checked"]
        elif key == "toggle_all":
            newval = not all(it["checked"] for it in items if not it.get("locked"))
            for it in items:
                if not it.get("locked"):
                    it["checked"] = newval
        elif key in ("enter", "skip"):
            clear()
            return items
        elif key == "quit":
            clear()
            print(c("Setup cancelled.", "yellow"))
            sys.exit(0)


def _ask_yes_no(question, default_yes=True):
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        answer = input(f"{question} {suffix} ").strip().lower()
    except EOFError:
        return default_yes
    if not answer:
        return default_yes
    return answer in ("y", "yes")


# ══════════════════════════════════════════════════════════════════════════════
# [1] Virtual environment
# ══════════════════════════════════════════════════════════════════════════════

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
    step("[1/6] Virtual environment (.venv)")
    if VENV.exists():
        ok(".venv already exists — reusing it.")
        print()
        return
    argv310 = find_python310()
    if argv310:
        ok(f"Python 3.10 detected ({' '.join(argv310)}).")
        base = argv310
    else:
        warn(f"Python 3.10 not found. Using current Python {platform.python_version()}.")
        base = [sys.executable]
    subprocess.run(base + ["-m", "venv", str(VENV)], check=True)
    ok("Virtual environment created.")
    print()


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


# ══════════════════════════════════════════════════════════════════════════════
# [4] Install dependencies
# ══════════════════════════════════════════════════════════════════════════════

def install_deps(packages):
    step("[4/6] Installing dependencies")
    py = venv_python()
    if not py.exists():
        warn(f"venv python not found at {py} — skipping install.")
        print()
        return
    print(c("  Upgrading pip…", "grey"))
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"],
                   check=False, stdout=subprocess.DEVNULL)
    print(c(f"  Installing {len(packages)} packages "
            "(this can take a few minutes)…", "grey"))
    result = subprocess.run([str(py), "-m", "pip", "install", *packages], check=False)
    if result.returncode == 0:
        ok("All dependencies installed.")
    else:
        warn("Some dependencies failed — check the pip output above.")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# [5] Helper scripts (platform-specific)
# ══════════════════════════════════════════════════════════════════════════════

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
set "SCRIPT_DIR=%~dp0"
set "RUN_BAT=%SCRIPT_DIR%run.bat"
schtasks /create /tn "SystemaAuxilium_AutoStart" /tr "\"%RUN_BAT%\"" /sc onlogon /ru "%USERDOMAIN%\%USERNAME%" /rl HIGHEST /f
if %ERRORLEVEL% EQU 0 (
    echo Scheduled task 'SystemaAuxilium_AutoStart' added with admin rights.
    echo Task points to: %RUN_BAT%
) else (
    echo Failed to create task. Please run this script as Administrator.
)
pause
''',
    "remove_autostart.bat": r'''@echo off
echo Removing scheduled task 'SystemaAuxilium_AutoStart'...
schtasks /delete /tn "SystemaAuxilium_AutoStart" /f
if %ERRORLEVEL% EQU 0 (
    echo Scheduled task removed successfully.
) else (
    echo Failed to remove task. It may not exist or you need admin privileges.
)
pause
''',
}

_NIX_HELPERS_BASE = {
    "open_env.sh": r'''#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash --rcfile <(echo "source \"$SCRIPT_DIR/.venv/bin/activate\"; echo 'Systema Auxilium venv activated.'")
''',
    "run.sh": r'''#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
python "$SCRIPT_DIR/main.py"
''',
}

_LINUX_ADD_AUTOSTART = r'''#!/bin/bash
# Adds Systema Auxilium to autostart via XDG autostart (.desktop file)
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
Terminal=false
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Comment=Systema Auxilium AI Desktop Assistant
DESKTOP
echo "Autostart entry created at: $DESKTOP_FILE"
echo "Systema Auxilium will launch on next login."
'''

_LINUX_REMOVE_AUTOSTART = r'''#!/bin/bash
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
UID_NUM="$(id -u)"
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
# Modern API first (macOS 10.10+), fall back to legacy load for old systems.
launchctl bootout "gui/$UID_NUM/$APP_LABEL" 2>/dev/null
if ! launchctl bootstrap "gui/$UID_NUM" "$PLIST_PATH" 2>/dev/null; then
    launchctl load "$PLIST_PATH"
fi
echo "Autostart registered with launchd: $PLIST_PATH"
echo "Systema Auxilium will launch on next login."
echo "Logs: ~/Library/Logs/systema-auxilium.log"
'''

_MAC_REMOVE_AUTOSTART = r'''#!/bin/bash
APP_LABEL="com.nic2007.systema-auxilium"
PLIST_PATH="$HOME/Library/LaunchAgents/$APP_LABEL.plist"
UID_NUM="$(id -u)"
if [ -f "$PLIST_PATH" ]; then
    if ! launchctl bootout "gui/$UID_NUM/$APP_LABEL" 2>/dev/null; then
        launchctl unload "$PLIST_PATH" 2>/dev/null
    fi
    rm "$PLIST_PATH"
    echo "Autostart entry removed: $PLIST_PATH"
else
    echo "No autostart entry found at: $PLIST_PATH"
fi
'''


def write_helpers():
    step("[5/6] Generating helper scripts")
    if IS_WIN:
        helpers = dict(_WIN_HELPERS)
    else:
        helpers = dict(_NIX_HELPERS_BASE)
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
        ok(name)
    print()


# ══════════════════════════════════════════════════════════════════════════════
# Pre-configuration application (shortcut + autostart)
# ══════════════════════════════════════════════════════════════════════════════

def _load_shortcuts_module():
    """Import the stdlib-only shortcut manager directly from its file, avoiding
    a full `import systema` (which would pull in PyQt6 & friends)."""
    path = ROOT / "systema" / "common" / "shortcuts.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("sa_shortcuts", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def apply_preconfig(choices):
    """choices: dict of key->bool for the PRECONFIG items."""
    if not any(choices.get(k) for k in ("shortcut", "autostart")):
        return
    step("[6/6] Applying pre-configuration")

    if choices.get("shortcut"):
        sc = _load_shortcuts_module()
        if sc is None:
            warn("Shortcut module unavailable — skipping desktop shortcut.")
        else:
            ok_flag, msg = sc.create_shortcut(as_admin=bool(choices.get("admin")))
            (ok if ok_flag else warn)(msg)

    if choices.get("autostart"):
        script = ROOT / ("add_autostart.bat" if IS_WIN else "add_autostart.sh")
        if not script.exists():
            warn("Autostart helper missing — skipping.")
        else:
            try:
                if IS_WIN:
                    subprocess.run(["cmd", "/c", str(script)], check=False)
                else:
                    subprocess.run(["bash", str(script)], check=False)
                ok("Autostart registered.")
            except Exception as e:
                warn(f"Could not enable autostart automatically: {e}")
                warn(f"Run it manually later: {script.name}")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# Verify + optional self-hide
# ══════════════════════════════════════════════════════════════════════════════

def verify():
    py = venv_python()
    if py.exists():
        r = subprocess.run([str(py), "--version"], capture_output=True, text=True)
        ok(f"venv ready: {(r.stdout or r.stderr).strip()}")
    else:
        warn("venv python missing — setup may be incomplete.")
    print()


def maybe_hide_self():
    if _ask_yes_no("Hide setup.py (move it into setup-scripts/)?", default_yes=False):
        ARCHIVE_DIR.mkdir(exist_ok=True)
        target = ARCHIVE_DIR / "setup.py"
        try:
            shutil.move(str(Path(__file__).resolve()), str(target))
            ok(f"setup.py moved to {target.relative_to(ROOT)}.")
            print(c("     Re-run later with:  python setup-scripts/setup.py", "grey"))
        except Exception as e:
            warn(f"Could not move setup.py: {e}")
    else:
        ok("setup.py kept in the project root for easy re-runs.")


# ══════════════════════════════════════════════════════════════════════════════
# Orchestration
# ══════════════════════════════════════════════════════════════════════════════

def gather_choices():
    """Run the two TUI pickers; return (packages, preconfig_choices)."""
    # ── Module picker ──
    mod_items = [{"label": f"Core essentials ({len(ESSENTIAL)} packages)",
                  "desc": "PyQt6 GUI, voice, memory, AI provider SDKs — required",
                  "checked": True, "locked": True}]
    for g in OPTIONAL_GROUPS:
        mod_items.append({"label": g["label"], "desc": g["desc"],
                          "checked": g["default"], "locked": False, "_key": g["key"]})
    mod_items = checklist(
        "Select optional feature modules",
        mod_items,
        subtitle="Essentials are locked. Toggle the extras you want, then press Enter.")

    packages = list(ESSENTIAL)
    selected = {it.get("_key") for it in mod_items if it.get("_key") and it["checked"]}
    for g in OPTIONAL_GROUPS:
        if g["key"] in selected:
            packages.extend(g["pkgs"])

    # ── Pre-configuration picker ──
    pre_items = [{"label": p["label"], "desc": p["desc"],
                  "checked": p["default"], "locked": False, "_key": p["key"]}
                 for p in PRECONFIG]
    pre_items = checklist(
        "Pre-configuration",
        pre_items,
        subtitle="Optional finishing steps. Toggle, then press Enter.")
    choices = {it["_key"]: it["checked"] for it in pre_items}
    if not choices.get("shortcut"):
        choices["admin"] = False   # admin sub-option only meaningful with a shortcut
    return packages, choices


def final_summary():
    print(c("  " + "─" * 74, "grey"))
    print(c("  ✓ Setup complete!", "green", "bold"))
    print()
    print("  You can now:")
    if IS_WIN:
        print(c("    • Run the app:        ", "grey") + "run.bat")
        print(c("    • Open venv terminal: ", "grey") + "open_env.bat")
        print(c("    • Enable autostart:   ", "grey") + "add_autostart.bat (as Admin)")
        print(c("    • Disable autostart:  ", "grey") + "remove_autostart.bat (as Admin)")
    else:
        print(c("    • Run the app:        ", "grey") + "bash run.sh")
        print(c("    • Open venv terminal: ", "grey") + "bash open_env.sh")
        print(c("    • Enable autostart:   ", "grey") + "bash add_autostart.sh")
        print(c("    • Disable autostart:  ", "grey") + "bash remove_autostart.sh")
        if IS_MAC:
            print()
            print(c("    macOS may ask permission to control your computer — grant it in", "grey"))
            print(c("    System Settings → Privacy & Security → Accessibility.", "grey"))
    print()


def main():
    show_banner()
    packages, choices = gather_choices()

    show_banner()
    try:
        create_venv()
        install_deps(packages)
        write_helpers()
        apply_preconfig(choices)
        verify()
    except subprocess.CalledProcessError as e:
        fail(f"Error during setup: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        warn("Setup interrupted.")
        sys.exit(1)

    final_summary()
    maybe_hide_self()


if __name__ == "__main__":
    main()
