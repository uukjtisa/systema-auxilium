#!/usr/bin/env python3
"""
setup.py — Systema Auxilium unified cross-platform installer
================================================================================
One script for Windows / Linux / macOS. It:

  1. Creates a .venv virtual environment (prefers Python 3.10, falls back).
  2. Lets you pick optional feature modules (essentials are always installed).
  3. Installs dependencies robustly (verifies every essential landed; retries any
     that slip through — no more silent "some failed").
  4. Generates the platform helper scripts (run / open_env / autostart wrappers /
     double-click launchers) and a __DO_NOT_DELETE__ reminder.
  5. Optionally creates a desktop shortcut / enables start-at-login — using the
     SAME code the in-app Settings uses (admin or normal privileges).

It runs a small **graphical** window when tkinter + a display are available, and
falls back to a robust **numbered-menu terminal** flow otherwise (Kali/Debian
ship Python without python3-tk, headless/SSH sessions have no display).

Dependencies live in ONE place — the ESSENTIAL / OPTIONAL_GROUPS lists below —
and requirements.txt is GENERATED from them (kept because the in-app self-updater
and `pip install -r requirements.txt` both read it).

Run it with any system Python:
    python setup.py           (Windows)     python setup.py --cli   (force terminal)
    python3 setup.py          (Linux/macOS) python3 setup.py --recover (regen scripts)

Everything here is stdlib-only, so it runs before any dependency exists.
================================================================================
"""

import os
import sys
import queue
import shlex
import shutil
import platform
import threading
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
SYSTEM = platform.system()          # 'Windows' | 'Linux' | 'Darwin'
IS_WIN = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"
IS_LINUX = not IS_WIN and not IS_MAC
ARCHIVE_DIR = ROOT / "setup-scripts"


# ══════════════════════════════════════════════════════════════════════════════
# Dependency catalogue — THE single source of truth (requirements.txt is derived)
# ══════════════════════════════════════════════════════════════════════════════

# Essentials are ALWAYS installed and cannot be unchecked.
ESSENTIAL = [
    "PyQt6", "Pillow", "pystray", "psutil", "send2trash", "pyautogui",
    "requests", "markdown2", "matplotlib", "pyparsing",
    "anthropic", "google-genai", "openai",
    "updater-gitplucker>=0.7.0",   # self-update from GitHub (Settings ▸ Check for Updates)
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
    {"key": "admin",    "label": "Run the desktop shortcut as administrator",
     "desc": "Elevated shortcut launch (UAC / pkexec / admin prompt).",
     "default": False},
    {"key": "autostart", "label": "Start automatically at login",
     "desc": "Registers Systema Auxilium to launch when you log in", "default": False},
    {"key": "autostart_admin", "label": "Start autostart as administrator",
     "desc": "Elevated at login (Windows: a logon Scheduled Task; Linux/macOS: admin prompt).",
     "default": False},
]

# Requirements notes preserved verbatim in the generated requirements.txt footer.
_REQ_NOTES = """\
# ============================================================================
# PLATFORM NOTES
# ============================================================================
# Windows:
#   - Visual C++ Redistributable 2015-2022 (x64):
#     https://aka.ms/vs/17/release/vc_redist.x64.exe
#   - Keep onnxruntime==1.19.2 (1.20+ causes DLL init failures).
#
# Linux (Debian/Ubuntu/Kali):
#   sudo apt-get install python3-pyqt6 python3-tk portaudio19-dev
#   - python3-tk is only needed for the setup.py GUI (the app itself doesn't use it).
#   - "Run as administrator" options use `pkexec` (package: policykit-1).
#
# macOS:
#   brew install portaudio
#   - Grant Accessibility permission in System Settings -> Privacy & Security.
# ============================================================================
"""


def _dist_name(spec: str) -> str:
    """Strip version/extras/markers from a requirement to its distribution name."""
    import re
    return re.split(r"[<>=!~;\[ ]", spec.strip(), 1)[0].strip()


def generate_requirements() -> str:
    """Render the canonical requirements.txt from the catalogue above. Essentials
    and default-on optional groups are uncommented; other optionals are listed
    commented under their group label. This file is GENERATED — the running app's
    self-updater diffs it, so it must stay a real, current file."""
    lines = [
        "# ============================================================================",
        "# SYSTEMA AUXILIUM - REQUIREMENTS   (AUTO-GENERATED by setup.py)",
        "# ----------------------------------------------------------------------------",
        "# DO NOT hand-edit and DO NOT delete: this file is regenerated by setup.py and",
        "# is read by the in-app self-updater (Settings > Check for Updates) to install",
        "# new dependencies after an update. To change deps, edit setup.py's ESSENTIAL /",
        "# OPTIONAL_GROUPS and re-run setup (or `python setup.py --recover`).",
        "# Basic install:   pip install -r requirements.txt",
        "# Recommended:     python setup.py   (builds a .venv + interactive picker)",
        "# ============================================================================",
        "",
        "# -- Essentials (always installed) -------------------------------------------",
    ]
    lines += list(ESSENTIAL)
    lines.append("")
    lines.append("# ============================================================================")
    lines.append("# OPTIONAL feature groups (default-on are uncommented; toggle in setup.py)")
    lines.append("# ============================================================================")
    for g in OPTIONAL_GROUPS:
        lines.append("")
        default = " (recommended)" if g["default"] else ""
        lines.append(f"# --- {g['label']}{default} ---")
        lines.append(f"#     {g['desc']}")
        for pkg in g["pkgs"]:
            lines.append(pkg if g["default"] else f"# {pkg}")
    lines.append("")
    lines.append(_REQ_NOTES)
    return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════════════════
# Reporter — a tiny sink the backend logs through; console or GUI-queue backed
# ══════════════════════════════════════════════════════════════════════════════

_C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "cyan": "\033[36m", "green": "\033[32m", "yellow": "\033[33m",
    "red": "\033[31m", "grey": "\033[90m",
}


def _enable_ansi() -> bool:
    if not sys.stdout or not sys.stdout.isatty():
        return False
    if IS_WIN:
        try:
            import ctypes
            k = ctypes.windll.kernel32
            for handle in (-11, -12):
                h = k.GetStdHandle(handle)
                mode = ctypes.c_uint32()
                if k.GetConsoleMode(h, ctypes.byref(mode)):
                    k.SetConsoleMode(h, mode.value | 0x0004)
        except Exception:
            return False
    return True


ANSI = _enable_ansi()


def c(text, *styles):
    if not ANSI:
        return text
    return "".join(_C[s] for s in styles) + text + _C["reset"]


class Reporter:
    """Base sink. Backend actions call .step/.ok/.warn/.fail/.info — the frontend
    decides how to present them."""
    def step(self, t): pass
    def ok(self, t): pass
    def warn(self, t): pass
    def fail(self, t): pass
    def info(self, t): pass


class ConsoleReporter(Reporter):
    def step(self, t): print(c(f"▸ {t}", "cyan", "bold"))
    def ok(self, t): print(c(f"  ✓ {t}", "green"))
    def warn(self, t): print(c(f"  ! {t}", "yellow"))
    def fail(self, t): print(c(f"  ✗ {t}", "red"))
    def info(self, t): print(c(f"    {t}", "grey"))


class QueueReporter(Reporter):
    """Pushes ('kind', text) tuples onto a queue for the GUI thread to drain."""
    def __init__(self, q): self.q = q
    def step(self, t): self.q.put(("step", t))
    def ok(self, t): self.q.put(("ok", t))
    def warn(self, t): self.q.put(("warn", t))
    def fail(self, t): self.q.put(("fail", t))
    def info(self, t): self.q.put(("info", t))


# ══════════════════════════════════════════════════════════════════════════════
# Shared app modules (shortcut + autostart) — the SAME code Settings uses
# ══════════════════════════════════════════════════════════════════════════════

def load_app_modules():
    """Import systema.common.shortcuts + autostart directly. systema/__init__ is
    import-light and both modules are stdlib-only, so this works under bare system
    Python before the venv exists. Returns (shortcuts, autostart) or (None, None)."""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from systema.common import shortcuts, autostart
        return shortcuts, autostart
    except Exception:
        return None, None


# ══════════════════════════════════════════════════════════════════════════════
# Virtual environment
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


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def create_venv(r: Reporter):
    r.step("Virtual environment (.venv)")
    if VENV.exists():
        r.ok(".venv already exists — reusing it.")
        return
    argv310 = find_python310()
    if argv310:
        r.ok(f"Python 3.10 detected ({' '.join(argv310)}).")
        base = argv310
    else:
        r.warn(f"Python 3.10 not found. Using current Python {platform.python_version()}.")
        r.info("To install Python 3.10 + system deps, use the "
               "\"Install Python 3.10 + prerequisites\" option on the main menu.")
        base = [sys.executable]
    subprocess.run(base + ["-m", "venv", str(VENV)], check=True)
    r.ok("Virtual environment created.")


# ══════════════════════════════════════════════════════════════════════════════
# Fresh-machine bootstrap — install Python 3.10 + system deps for the current OS
# ══════════════════════════════════════════════════════════════════════════════

def _read_os_release() -> dict:
    data = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"')
    except Exception:
        pass
    return data


def detect_platform_installer() -> dict:
    """Map the current OS/distro to a package manager + the commands that install
    Python 3.10 (`py_cmds`) and the app's system dependencies (`sys_cmds`).
    `note` carries any caveat. Unknown targets return empty command lists."""
    if IS_WIN:
        return {"name": "winget", "family": "windows",
                "py_cmds": [["winget", "install", "-e", "--id", "Python.Python.3.10"]],
                "sys_cmds": [],
                "note": "No winget? Get Python 3.10 from "
                        "https://www.python.org/downloads/release/python-31011/"}
    if IS_MAC:
        return {"name": "brew", "family": "macos",
                "py_cmds": [["brew", "install", "python@3.10"]],
                "sys_cmds": [["brew", "install", "portaudio"]],
                "note": "Requires Homebrew (https://brew.sh)."}

    osr = _read_os_release()
    idlike = (osr.get("ID", "") + " " + osr.get("ID_LIKE", "")).lower()

    if any(x in idlike for x in ("ubuntu", "pop", "mint")) and "ubuntu" in idlike:
        return {"name": "apt", "family": "debian",
                "py_cmds": [["sudo", "apt-get", "update"],
                            ["sudo", "apt-get", "install", "-y", "software-properties-common"],
                            ["sudo", "add-apt-repository", "-y", "ppa:deadsnakes/ppa"],
                            ["sudo", "apt-get", "update"],
                            ["sudo", "apt-get", "install", "-y",
                             "python3.10", "python3.10-venv", "python3.10-dev"]],
                "sys_cmds": [["sudo", "apt-get", "install", "-y",
                              "portaudio19-dev", "python3-tk", "python3-pyqt6", "policykit-1"]],
                "note": ""}
    if any(x in idlike for x in ("debian", "kali", "raspbian")):
        return {"name": "apt", "family": "debian",
                "py_cmds": [["sudo", "apt-get", "update"],
                            ["sudo", "apt-get", "install", "-y",
                             "python3", "python3-venv", "python3-dev", "python3-pip"]],
                "sys_cmds": [["sudo", "apt-get", "install", "-y",
                              "portaudio19-dev", "python3-tk", "python3-pyqt6", "policykit-1"]],
                "note": "Debian/Kali repos don't carry python3.10 — installing the system python3 "
                        "instead (the app runs on 3.11+). For 3.10 exactly, use pyenv."}
    if any(x in idlike for x in ("fedora", "rhel", "centos", "rocky", "almalinux")):
        return {"name": "dnf", "family": "fedora",
                "py_cmds": [["sudo", "dnf", "install", "-y", "python3.10"]],
                "sys_cmds": [["sudo", "dnf", "install", "-y",
                              "portaudio-devel", "python3-tkinter", "python3-qt6", "polkit"]],
                "note": ""}
    if any(x in idlike for x in ("arch", "manjaro", "endeavour")):
        return {"name": "pacman", "family": "arch",
                "py_cmds": [["sudo", "pacman", "-Sy", "--noconfirm", "python"]],
                "sys_cmds": [["sudo", "pacman", "-S", "--noconfirm",
                              "portaudio", "tk", "python-pyqt6", "polkit"]],
                "note": "Arch ships the latest python (the app runs on it). For 3.10 exactly use pyenv/AUR."}
    if any(x in idlike for x in ("opensuse", "suse", "sles")):
        return {"name": "zypper", "family": "suse",
                "py_cmds": [["sudo", "zypper", "--non-interactive", "install", "python310"]],
                "sys_cmds": [["sudo", "zypper", "--non-interactive", "install",
                              "portaudio-devel", "python3-tk", "python3-qt6", "polkit"]],
                "note": ""}
    return {"name": "unknown", "family": "linux", "py_cmds": [], "sys_cmds": [],
            "note": "Unknown distro — install manually: python3.10 (+venv/dev), a portaudio dev "
                    "package, tk, PyQt6 system libs, and polkit."}


_LINUX_TERMS = ["x-terminal-emulator", "qterminal", "konsole", "xterm",
                "gnome-terminal", "xfce4-terminal", "mate-terminal", "alacritty", "kitty"]


def _spawn_terminal(script: str) -> bool:
    """Run a bash script in a new terminal window (so `sudo` can prompt for a
    password and the user sees progress). Returns True if a terminal was launched."""
    if IS_MAC:
        try:
            osa = 'tell application "Terminal" to do script "%s"' % script.replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", osa])
            return True
        except Exception:
            return False
    for exe in _LINUX_TERMS:
        if shutil.which(exe) is None:
            continue
        if exe in ("gnome-terminal", "mate-terminal"):
            argv = [exe, "--", "bash", "-c", script]
        elif exe == "xfce4-terminal":
            argv = [exe, "-x", "bash", "-c", script]
        elif exe == "kitty":
            argv = [exe, "bash", "-c", script]
        else:
            argv = [exe, "-e", "bash", "-c", script]
        try:
            subprocess.Popen(argv)
            return True
        except Exception:
            continue
    return False


def bootstrap_prerequisites(r: Reporter, gui: bool = False):
    """Install Python 3.10 (where the distro supports it) + the app's system deps,
    using the detected package manager. Caller confirms first. On Linux GUI runs it
    in a spawned terminal so sudo can prompt; the terminal frontend runs inline."""
    info = detect_platform_installer()
    r.step(f"Install Python 3.10 + prerequisites  ({info['name']})")
    cmds = list(info["py_cmds"]) + list(info["sys_cmds"])
    if not cmds:
        r.warn("No automatic installer mapping for this OS.")
        if info["note"]:
            r.info(info["note"])
        return
    if info["note"]:
        r.info(info["note"])
    for c in cmds:
        r.info("$ " + " ".join(c))

    if IS_WIN or IS_MAC:
        # winget (UAC as needed) / brew — no tty password, run inline.
        for c in cmds:
            try:
                res = subprocess.run(c)
                if res.returncode != 0:
                    r.warn(f"exited {res.returncode}: {' '.join(c)}")
            except FileNotFoundError:
                r.fail(f"{c[0]} not found — see the note above.")
            except Exception as e:
                r.warn(f"{' '.join(c)} -> {e}")
    else:
        # Linux: sudo needs a password. GUI → spawn a terminal; CLI → run inline.
        joined = "; ".join(" ".join(shlex.quote(a) for a in c) for c in cmds)
        if gui:
            script = ("set -e; " + joined +
                      '; echo; echo "[done] prerequisites installed"; '
                      'read -p "Press Enter to close..." _')
            if _spawn_terminal(script):
                r.ok("Opened a terminal to install prerequisites — enter your sudo password there.")
            else:
                r.warn("No terminal emulator found. Run these commands manually:")
                for c in cmds:
                    r.info("  " + " ".join(c))
            return
        for c in cmds:
            try:
                res = subprocess.run(c)
                if res.returncode != 0:
                    r.warn(f"exited {res.returncode}: {' '.join(c)}")
            except Exception as e:
                r.warn(f"{' '.join(c)} -> {e}")

    if find_python310():
        r.ok("Python 3.10 is now available.")
    else:
        r.info("Using the available python3 (the app also runs on 3.11+).")


# ══════════════════════════════════════════════════════════════════════════════
# Robust dependency install
# ══════════════════════════════════════════════════════════════════════════════

def _pip_installed(py: Path, spec: str) -> bool:
    """True if the distribution for `spec` is importable/installed in the venv."""
    try:
        r = subprocess.run([str(py), "-m", "pip", "show", _dist_name(spec)],
                           capture_output=True, text=True)
        return r.returncode == 0
    except Exception:
        return False


def _missing(py: Path, specs) -> list:
    """Subset of `specs` whose distribution is not present in the venv."""
    return [s for s in specs if not _pip_installed(py, s)]


def install_deps(selected_optional_keys, r: Reporter):
    """Install essentials (verified + retried) then each selected optional group
    separately, so one bad package never silently takes the rest down."""
    r.step("Installing dependencies")
    py = venv_python()
    if not py.exists():
        r.warn(f"venv python not found at {py} — skipping install.")
        return

    r.info("Upgrading pip / setuptools / wheel…")
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
                   check=False, stdout=subprocess.DEVNULL)

    # ── Essentials: one batch, then verify + retry the stragglers individually ──
    r.info(f"Installing {len(ESSENTIAL)} essential packages…")
    subprocess.run([str(py), "-m", "pip", "install", *ESSENTIAL], check=False)
    missing = _missing(py, ESSENTIAL)
    for spec in missing:
        r.warn(f"{_dist_name(spec)} missing after batch install — retrying on its own…")
        subprocess.run([str(py), "-m", "pip", "install", spec], check=False)
    still = _missing(py, ESSENTIAL)
    if still:
        for spec in still:
            r.fail(f"Essential package FAILED to install: {spec}")
    else:
        r.ok("All essential packages verified present.")
    # Explicit, named check for the self-updater dependency the user hit before.
    if _pip_installed(py, "updater-gitplucker"):
        r.ok("updater-gitplucker present (self-update ready).")
    else:
        r.fail("updater-gitplucker NOT installed — self-update will be unavailable.")

    # ── Optional groups: isolate each so a failure doesn't block the others ──
    for g in OPTIONAL_GROUPS:
        if g["key"] not in selected_optional_keys:
            continue
        r.info(f"Installing optional: {g['label']}…")
        subprocess.run([str(py), "-m", "pip", "install", *g["pkgs"]], check=False)
        gone = _missing(py, g["pkgs"])
        if gone:
            r.warn(f"{g['label']}: not fully installed ({', '.join(_dist_name(x) for x in gone)}).")
        else:
            r.ok(f"{g['label']} installed.")


# ══════════════════════════════════════════════════════════════════════════════
# Generated helper scripts + launchers + manifest
# ══════════════════════════════════════════════════════════════════════════════

# OS-specific canonical launch-script names (must match shortcuts._run_script()).
RUN_SCRIPT = "run.bat" if IS_WIN else ("run.command" if IS_MAC else "run.sh")
OPENENV_SCRIPT = "open_env.bat" if IS_WIN else ("open_env.command" if IS_MAC else "open_env.sh")
_AS_ADD = "add_autostart.bat" if IS_WIN else "add_autostart.sh"
_AS_REMOVE = "remove_autostart.bat" if IS_WIN else "remove_autostart.sh"
MANIFEST_NAME = "__DO_NOT_DELETE__.txt"

# ── Windows ──
_WIN_OPEN_ENV = r'''@echo off
start "" cmd /k "%~dp0.venv\Scripts\activate.bat"
'''
_WIN_RUN = r'''@echo off
cd /d "%~dp0"
call "%~dp0.venv\Scripts\activate.bat"
python "%~dp0main.py"
pause
'''
# Autostart wrappers go through the shared module so behaviour == Settings.
_WIN_ADD_AUTOSTART = r'''@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -c "import sys,os; sys.path.insert(0, os.getcwd()); from systema.common import autostart as a; ok,msg=a.enable_autostart(); print(msg)"
pause
'''
_WIN_REMOVE_AUTOSTART = r'''@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -c "import sys,os; sys.path.insert(0, os.getcwd()); from systema.common import autostart as a; ok,msg=a.disable_autostart(); print(msg)"
pause
'''

# ── POSIX (Linux + macOS) ──
_NIX_OPEN_ENV = r'''#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash --rcfile <(echo "source \"$SCRIPT_DIR/.venv/bin/activate\"; echo 'Systema Auxilium venv activated.'")
'''
# Launch DETACHED — no clinging terminal. The app redirects its own session log
# to data/logs/ (Python-level _Tee), so detaching loses nothing.
_NIX_RUN = r'''#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
VENVPY="$SCRIPT_DIR/.venv/bin/python"
[ -x "$VENVPY" ] || VENVPY="$(command -v python3 || command -v python)"
if command -v setsid >/dev/null 2>&1; then
    setsid nohup "$VENVPY" "$SCRIPT_DIR/main.py" >/dev/null 2>&1 </dev/null &
else
    nohup "$VENVPY" "$SCRIPT_DIR/main.py" >/dev/null 2>&1 </dev/null &
fi
disown 2>/dev/null || true
'''
_NIX_ADD_AUTOSTART = r'''#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PY="$SCRIPT_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
"$PY" -c "import sys,os; sys.path.insert(0, os.getcwd()); from systema.common import autostart as a; ok,msg=a.enable_autostart(); print(msg)"
'''
_NIX_REMOVE_AUTOSTART = r'''#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PY="$SCRIPT_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
"$PY" -c "import sys,os; sys.path.insert(0, os.getcwd()); from systema.common import autostart as a; ok,msg=a.disable_autostart(); print(msg)"
'''

_MAC_OPENENV_COMMAND = r'''#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/.venv/bin/activate"
echo "Systema Auxilium venv activated."
exec "$SHELL"
'''


def _linux_desktop_launcher() -> str:
    """Double-click .desktop entry with this install's paths baked in."""
    run_sh = ROOT / RUN_SCRIPT
    icon = ROOT / "assets" / "systema_auxilium.ico"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=Systema Auxilium\n"
        "GenericName=AI Desktop Assistant\n"
        "Comment=Systema Auxilium AI Desktop Assistant\n"
        f'Exec=bash "{run_sh}"\n'
        f"Path={ROOT}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
    )


def generated_files() -> list:
    """Every file setup.py generates for THIS OS (for the manifest + uninstall)."""
    if IS_WIN:
        names = [OPENENV_SCRIPT, RUN_SCRIPT, _AS_ADD, _AS_REMOVE]
    elif IS_MAC:
        names = [OPENENV_SCRIPT, RUN_SCRIPT, _AS_ADD, _AS_REMOVE]
    else:
        names = [OPENENV_SCRIPT, RUN_SCRIPT, _AS_ADD, _AS_REMOVE, "Systema Auxilium.desktop"]
    names.append(MANIFEST_NAME)
    return names


def _manifest_content() -> str:
    launch = "run.bat" if IS_WIN else ("bash run.command" if IS_MAC else "bash run.sh")
    recover = "python setup.py --recover" if IS_WIN else "python3 setup.py --recover"
    scripts = "  ".join(generated_files())
    return (
        "DO NOT DELETE THESE FILES\n"
        "=========================\n\n"
        "Systema Auxilium and its ecosystem depend on the files below. Deleting any\n"
        "of them breaks a feature — none are safe to remove.\n\n"
        "Generated launch / helper scripts (regenerate with recovery, see bottom):\n"
        f"    {scripts}\n"
        "  - The in-app \"Restart\" button and start-at-login relaunch through the\n"
        f"    run script ({RUN_SCRIPT}).\n"
        "  - The desktop shortcut and double-click launcher call the run script.\n\n"
        "Core files that must never be deleted:\n"
        "    requirements.txt   - read by the in-app self-updater to install new\n"
        "                         dependencies after an update; also `pip install -r`.\n"
        "                         It is AUTO-GENERATED by setup.py — do not hand-edit.\n"
        "    main.py            - the application entry point.\n"
        "    systema/           - the application package (all of the app's code).\n"
        "    assets/            - icons used by the app, shortcuts and launchers.\n"
        "    data/              - your sessions, chat history, logs and settings.\n"
        "                         Deleting this erases your history and configuration.\n"
        "    setup.py           - regenerates the helper scripts and this file.\n\n"
        "If a generated script is ever lost, you do NOT need to reinstall — regenerate\n"
        "all of them (your .venv and installed packages are left untouched):\n\n"
        f"    {recover}\n\n"
        f"To launch the app:  {launch}\n\n"
        "This reminder file itself is safe to delete — the files listed above are not.\n"
    )


def write_helpers(r: Reporter):
    r.step("Generating helper scripts")
    if IS_WIN:
        helpers = {OPENENV_SCRIPT: _WIN_OPEN_ENV, RUN_SCRIPT: _WIN_RUN,
                   _AS_ADD: _WIN_ADD_AUTOSTART, _AS_REMOVE: _WIN_REMOVE_AUTOSTART}
    else:
        helpers = {RUN_SCRIPT: _NIX_RUN, _AS_ADD: _NIX_ADD_AUTOSTART,
                   _AS_REMOVE: _NIX_REMOVE_AUTOSTART}
        helpers[OPENENV_SCRIPT] = _MAC_OPENENV_COMMAND if IS_MAC else _NIX_OPEN_ENV
    for name, content in helpers.items():
        path = ROOT / name
        path.write_text(content, encoding="utf-8")
        if not IS_WIN:
            os.chmod(path, 0o755)
        r.ok(name)
    if IS_LINUX:
        path = ROOT / "Systema Auxilium.desktop"
        path.write_text(_linux_desktop_launcher(), encoding="utf-8")
        os.chmod(path, 0o755)
        r.ok("Systema Auxilium.desktop")

    # Regenerate the canonical requirements.txt from the catalogue (single source).
    (ROOT / "requirements.txt").write_text(generate_requirements(), encoding="utf-8")
    r.ok("requirements.txt (generated)")

    # Reminder note listing every ecosystem-critical file.
    (ROOT / MANIFEST_NAME).write_text(_manifest_content(), encoding="utf-8")
    r.ok(MANIFEST_NAME)


# ══════════════════════════════════════════════════════════════════════════════
# Shortcut / autostart / uninstall — via the shared modules (== Settings)
# ══════════════════════════════════════════════════════════════════════════════

def do_shortcut(create: bool, as_admin: bool, r: Reporter):
    sc, _ = load_app_modules()
    if sc is None:
        r.fail("Shortcut module unavailable (systema/common/shortcuts.py).")
        return
    ok, msg = (sc.set_admin(as_admin) if create else sc.remove_shortcut())
    (r.ok if ok else r.fail)(msg)


def do_autostart(enable: bool, r: Reporter, as_admin: bool = False):
    """Enable/disable start-at-login (per-user). ``as_admin`` starts it ELEVATED at
    login — Windows: a logon Scheduled Task (a Startup shortcut can't elevate);
    Linux: the XDG entry re-launches via pkexec; macOS: an admin osascript wrap.
    Normal (the default) uses the plain per-user mechanism. On Linux, when this
    runs elevated the entry is installed for the invoking login user, not root."""
    _, aut = load_app_modules()
    if aut is None:
        r.fail("Autostart module unavailable (systema/common/autostart.py).")
        return
    ok, msg = (aut.enable_autostart(as_admin=as_admin) if enable else aut.disable_autostart())
    (r.ok if ok else r.fail)(msg)


def do_autostart_test(r: Reporter):
    """Launch the app now via the same run script autostart uses."""
    _, aut = load_app_modules()
    if aut is None:
        r.fail("Autostart module unavailable (systema/common/autostart.py).")
        return
    ok, msg = aut.run_now()
    (r.ok if ok else r.fail)(msg)


def uninstall_clean(remove_venv: bool, r: Reporter):
    """Remove generated scripts + autostart + desktop shortcut (+ optionally the
    venv). NEVER touches data/, main.py or the systema/ source."""
    r.step("Uninstall / clean")
    sc, aut = load_app_modules()
    if aut is not None:
        try:
            ok, msg = aut.disable_autostart(); r.ok(f"autostart: {msg}")
        except Exception as e:
            r.warn(f"autostart: {e}")
    if sc is not None:
        try:
            ok, msg = sc.remove_shortcut(); r.ok(f"shortcut: {msg}")
        except Exception as e:
            r.warn(f"shortcut: {e}")
    for name in generated_files():
        p = ROOT / name
        try:
            if p.exists():
                p.unlink(); r.ok(f"removed {name}")
        except Exception as e:
            r.warn(f"could not remove {name}: {e}")
    if remove_venv and VENV.exists():
        try:
            shutil.rmtree(VENV); r.ok("removed .venv")
        except Exception as e:
            r.warn(f"could not remove .venv: {e}")
    r.info("Left untouched: data/, main.py, systema/, assets/, requirements.txt.")


def verify(r: Reporter):
    py = venv_python()
    if py.exists():
        res = subprocess.run([str(py), "--version"], capture_output=True, text=True)
        r.ok(f"venv ready: {(res.stdout or res.stderr).strip()}")
    else:
        r.warn("venv python missing — setup may be incomplete.")


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrated flows
# ══════════════════════════════════════════════════════════════════════════════

def run_full_setup(selected_optional_keys, preconfig, r: Reporter):
    """preconfig: dict with keys shortcut/admin/autostart/autostart_admin (bools)."""
    try:
        create_venv(r)
        install_deps(selected_optional_keys, r)
        write_helpers(r)
        as_admin = bool(preconfig.get("admin"))
        if preconfig.get("shortcut"):
            do_shortcut(True, as_admin, r)   # this admin flag applies to the shortcut
        if preconfig.get("autostart"):
            do_autostart(True, r, as_admin=bool(preconfig.get("autostart_admin")))
        verify(r)
        r.step("Setup complete")
        r.ok(f"Launch with: {'run.bat' if IS_WIN else 'bash ' + RUN_SCRIPT}")
        r.info(f"Lost a script later? Regenerate with: "
               f"{'python' if IS_WIN else 'python3'} setup.py --recover")
    except subprocess.CalledProcessError as e:
        r.fail(f"Error during setup: {e}")
    except Exception as e:
        r.fail(f"Unexpected error: {e}")


def run_recovery(r: Reporter):
    """Regenerate helper scripts / launchers / requirements.txt / manifest only —
    venv and installed packages untouched."""
    r.step("Script recovery — regenerating helper scripts only")
    write_helpers(r)
    r.ok("Recovery complete (venv + packages left untouched).")


# ══════════════════════════════════════════════════════════════════════════════
# Frontend: terminal (robust numbered menus — no raw mode, no screen clears)
# ══════════════════════════════════════════════════════════════════════════════

def _ask(prompt, default=""):
    try:
        ans = input(prompt).strip()
    except EOFError:
        return default
    return ans or default


def _ask_yes_no(question, default_yes=True):
    ans = _ask(f"{question} {'[Y/n]' if default_yes else '[y/N]'} ").lower()
    if not ans:
        return default_yes
    return ans in ("y", "yes")


def _pick_privilege(what: str = "") -> bool:
    """Return True for admin, False for normal. ``what`` labels which thing the
    privilege applies to (e.g. 'shortcut', 'autostart') so it's never ambiguous."""
    tag = f" for the {what}" if what else ""
    ans = _ask(f"  Privilege{tag} — [1] Normal (recommended)  [2] Run as administrator: ", "1")
    return ans.strip() == "2"


def _select_optionals_terminal():
    print(c("\nOptional feature modules (essentials are always installed):", "bold"))
    for i, g in enumerate(OPTIONAL_GROUPS, 1):
        mark = "on " if g["default"] else "off"
        print(f"  {i}) [{mark}] {g['label']}")
        print(c(f"        {g['desc']}", "grey"))
    print(c("  Enter numbers to TOGGLE (e.g. 1,3), 'a'=all, 'n'=none, blank=accept defaults.",
            "grey"))
    checked = {g["key"]: g["default"] for g in OPTIONAL_GROUPS}
    ans = _ask("  > ").lower()
    if ans == "a":
        checked = {g["key"]: True for g in OPTIONAL_GROUPS}
    elif ans == "n":
        checked = {g["key"]: False for g in OPTIONAL_GROUPS}
    elif ans:
        for tok in ans.replace(" ", "").split(","):
            if tok.isdigit() and 1 <= int(tok) <= len(OPTIONAL_GROUPS):
                k = OPTIONAL_GROUPS[int(tok) - 1]["key"]
                checked[k] = not checked[k]
    return {k for k, v in checked.items() if v}


def _select_preconfig_terminal():
    print(c("\nPre-configuration:", "bold"))
    shortcut = _ask_yes_no("  Create a Desktop shortcut?", True)
    admin = _pick_privilege("shortcut") if shortcut else False
    autostart = _ask_yes_no("  Start automatically at login?", False)
    autostart_admin = _pick_privilege("autostart") if autostart else False
    return {"shortcut": shortcut, "autostart": autostart,
            "admin": admin, "autostart_admin": autostart_admin}


def terminal_main():
    r = ConsoleReporter()
    print(c("\n  Systema Auxilium — Setup", "cyan", "bold"))
    print(c(f"  {SYSTEM} · Python {platform.python_version()}", "grey"))
    if _import_tk_error:
        print(c(f"  (GUI unavailable: {_import_tk_error} — using terminal mode)", "grey"))
    while True:
        print(c("\n" + "─" * 60, "grey"))
        print("  1) Set up / install")
        print("  2) Recover run scripts")
        print("  3) Autostart  (enable / disable)")
        print("  4) Desktop shortcut  (create / remove)")
        print("  5) Uninstall / clean")
        print("  6) Install Python 3.10 + prerequisites")
        print("  7) Quit")
        choice = _ask(c("  Choose [1-7]: ", "bold"))
        if choice == "1":
            opt = _select_optionals_terminal()
            pre = _select_preconfig_terminal()
            print()
            run_full_setup(opt, pre, r)
            maybe_hide_self()
        elif choice == "2":
            run_recovery(r)
        elif choice == "3":
            if _ask_yes_no("  Enable autostart? (No = disable)", True):
                do_autostart(True, r, as_admin=_pick_privilege("autostart"))
                if _ask_yes_no("  Test it now (launch via the run script)?", False):
                    do_autostart_test(r)
            else:
                do_autostart(False, r)
        elif choice == "4":
            if _ask_yes_no("  Create/update shortcut? (No = remove)", True):
                do_shortcut(True, _pick_privilege("shortcut"), r)
            else:
                do_shortcut(False, False, r)
        elif choice == "5":
            if _ask_yes_no("  Remove generated scripts, autostart & shortcut?", False):
                uninstall_clean(_ask_yes_no("  Also delete the .venv?", False), r)
        elif choice == "6":
            info = detect_platform_installer()
            print(c(f"  Package manager: {info['name']}", "grey"))
            for cmd in info["py_cmds"] + info["sys_cmds"]:
                print(c("    $ " + " ".join(cmd), "grey"))
            if info["note"]:
                print(c("  " + info["note"], "yellow"))
            if (info["py_cmds"] or info["sys_cmds"]) and _ask_yes_no(
                    "  Run these now (sudo will prompt)?", False):
                bootstrap_prerequisites(r, gui=False)
        elif choice in ("7", "q", "quit", ""):
            print(c("  Bye.", "grey"))
            return
        else:
            print(c("  Unknown choice.", "yellow"))


def maybe_hide_self():
    if _ask_yes_no("\nHide setup.py (move it into setup-scripts/)?", default_yes=False):
        ARCHIVE_DIR.mkdir(exist_ok=True)
        target = ARCHIVE_DIR / "setup.py"
        try:
            shutil.move(str(Path(__file__).resolve()), str(target))
            print(c(f"  setup.py moved to {target.relative_to(ROOT)}.", "green"))
            print(c("  Re-run later with:  python setup-scripts/setup.py", "grey"))
        except Exception as e:
            print(c(f"  Could not move setup.py: {e}", "yellow"))


# ══════════════════════════════════════════════════════════════════════════════
# Frontend: tkinter GUI  (minimal, dark; falls back to terminal when unavailable)
# ══════════════════════════════════════════════════════════════════════════════

_import_tk_error = ""


def gui_available() -> bool:
    global _import_tk_error
    if "--cli" in sys.argv:
        _import_tk_error = "forced --cli"
        return False
    try:
        import tkinter  # noqa: F401
    except Exception as e:
        _import_tk_error = f"tkinter not installed ({e})"
        return False
    if IS_LINUX and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        _import_tk_error = "no display (DISPLAY/WAYLAND unset)"
        return False
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception as e:
        _import_tk_error = f"cannot open a window ({e})"
        return False


# Palette
_BG = "#0d1117"; _SURF = "#161b22"; _ELEV = "#1c2330"; _BORDER = "#2b3444"
_TEXT = "#e6edf3"; _MUTED = "#8b98a9"; _ACCENT = "#4aa8ff"; _GREEN = "#3fb950"
_RED = "#f85149"; _YELLOW = "#d29922"


class SetupGUI:
    def __init__(self):
        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.title("Systema Auxilium — Setup")
        self.root.configure(bg=_BG)
        self.root.geometry("760x600")
        self.root.minsize(680, 520)
        self.q = queue.Queue()
        self._busy = False
        self._build_header()
        self.body = tk.Frame(self.root, bg=_BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        self.show_landing()
        self.root.after(80, self._pump)

    # ── chrome ──
    def _build_header(self):
        tk = self.tk
        head = tk.Frame(self.root, bg=_BG)
        head.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(head, text="Systema Auxilium", bg=_BG, fg=_TEXT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(head, text=f"Cross-platform installer  ·  {SYSTEM}  ·  "
                 f"Python {platform.python_version()}",
                 bg=_BG, fg=_MUTED, font=("Segoe UI", 10)).pack(anchor="w")
        sep = tk.Frame(self.root, bg=_BORDER, height=1)
        sep.pack(fill="x", padx=18, pady=(6, 10))

    def _btn(self, parent, text, cmd, kind="normal"):
        tk = self.tk
        fg = "#05070a" if kind == "accent" else _TEXT
        bg = _ACCENT if kind == "accent" else _ELEV
        b = tk.Button(parent, text=text, command=cmd, fg=fg, bg=bg,
                      activebackground=_ACCENT if kind == "accent" else _SURF,
                      activeforeground=fg, relief="flat", bd=0, cursor="hand2",
                      font=("Segoe UI", 11, "bold" if kind == "accent" else "normal"),
                      padx=16, pady=12, anchor="w", justify="left")
        return b

    def _clear_body(self):
        for w in self.body.winfo_children():
            w.destroy()

    # ── landing ──
    def show_landing(self):
        tk = self.tk
        self._clear_body()
        rows = [
            ("Set up / Install", lambda: self.show_setup(), "accent",
             "Create the .venv, install modules, generate scripts."),
            ("Recover run scripts", lambda: self._run(lambda r: run_recovery(r)), "normal",
             "Regenerate run / launch scripts + requirements.txt (venv untouched)."),
            ("Autostart…", lambda: self.show_privilege("autostart"), "normal",
             "Enable, disable or test start-at-login (per-user, normal or elevated)."),
            ("Desktop shortcut…", lambda: self.show_privilege("shortcut"), "normal",
             "Create/update or remove the desktop shortcut (normal or admin)."),
            ("Install Python 3.10 + prerequisites", lambda: self._bootstrap(), "normal",
             "Fresh machine? Install Python 3.10 + system deps for your OS."),
            ("Uninstall / clean", lambda: self.confirm_uninstall(), "normal",
             "Remove generated scripts, autostart, shortcut (and optionally .venv)."),
            ("Quit", self.root.destroy, "normal", ""),
        ]
        for text, cmd, kind, desc in rows:
            card = tk.Frame(self.body, bg=_BG)
            card.pack(fill="x", pady=5)
            b = self._btn(card, text, cmd, kind)
            b.pack(fill="x")
            if desc:
                tk.Label(card, text=desc, bg=_BG, fg=_MUTED, font=("Segoe UI", 9),
                         anchor="w").pack(fill="x", padx=4, pady=(2, 0))

    # ── setup view ──
    def show_setup(self):
        tk = self.tk
        self._clear_body()
        canvas_wrap = tk.Frame(self.body, bg=_BG)
        canvas_wrap.pack(fill="both", expand=True)
        tk.Label(canvas_wrap, text="Optional feature modules", bg=_BG, fg=_TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(2, 2))
        tk.Label(canvas_wrap, text="Essentials are always installed. Toggle the extras you want.",
                 bg=_BG, fg=_MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 6))
        self._opt_vars = {}
        for g in OPTIONAL_GROUPS:
            v = tk.BooleanVar(value=g["default"])
            self._opt_vars[g["key"]] = v
            cb = tk.Checkbutton(canvas_wrap, text=f"  {g['label']}", variable=v,
                                bg=_BG, fg=_TEXT, selectcolor=_ELEV, activebackground=_BG,
                                activeforeground=_TEXT, font=("Segoe UI", 10),
                                anchor="w", highlightthickness=0, bd=0)
            cb.pack(fill="x")
            tk.Label(canvas_wrap, text=f"       {g['desc']}", bg=_BG, fg=_MUTED,
                     font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(0, 3))

        tk.Frame(canvas_wrap, bg=_BORDER, height=1).pack(fill="x", pady=8)
        tk.Label(canvas_wrap, text="Finishing steps", bg=_BG, fg=_TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self._sc_var = tk.BooleanVar(value=True)
        self._as_var = tk.BooleanVar(value=False)
        self._admin_var = tk.BooleanVar(value=False)
        self._as_admin_var = tk.BooleanVar(value=False)
        tk.Checkbutton(canvas_wrap, text="  Create a Desktop shortcut", variable=self._sc_var,
                       bg=_BG, fg=_TEXT, selectcolor=_ELEV, activebackground=_BG,
                       activeforeground=_TEXT, font=("Segoe UI", 10), anchor="w",
                       highlightthickness=0, bd=0).pack(fill="x")
        tk.Checkbutton(canvas_wrap, text="       ↳ run the desktop shortcut as administrator",
                       variable=self._admin_var, bg=_BG, fg=_YELLOW, selectcolor=_ELEV,
                       activebackground=_BG, activeforeground=_YELLOW, font=("Segoe UI", 9),
                       anchor="w", highlightthickness=0, bd=0).pack(fill="x")
        tk.Checkbutton(canvas_wrap, text="  Start automatically at login", variable=self._as_var,
                       bg=_BG, fg=_TEXT, selectcolor=_ELEV, activebackground=_BG,
                       activeforeground=_TEXT, font=("Segoe UI", 10), anchor="w",
                       highlightthickness=0, bd=0).pack(fill="x")
        tk.Checkbutton(canvas_wrap, text="       ↳ start autostart as administrator (elevated at login)",
                       variable=self._as_admin_var, bg=_BG, fg=_YELLOW, selectcolor=_ELEV,
                       activebackground=_BG, activeforeground=_YELLOW, font=("Segoe UI", 9),
                       anchor="w", highlightthickness=0, bd=0).pack(fill="x")

        row = tk.Frame(canvas_wrap, bg=_BG)
        row.pack(fill="x", pady=(12, 0))
        self._btn(row, "Begin setup", self._begin_setup, "accent").pack(side="left")
        self._btn(row, "Back", self.show_landing, "normal").pack(side="left", padx=(8, 0))

    def _begin_setup(self):
        opt = {k for k, v in self._opt_vars.items() if v.get()}
        pre = {"shortcut": self._sc_var.get(), "autostart": self._as_var.get(),
               "admin": self._admin_var.get(), "autostart_admin": self._as_admin_var.get()}
        self._run(lambda r: run_full_setup(opt, pre, r))

    # ── autostart / shortcut sub-view ──
    def show_privilege(self, kind):
        tk = self.tk
        self._clear_body()
        title = "Autostart" if kind == "autostart" else "Desktop shortcut"
        tk.Label(self.body, text=title, bg=_BG, fg=_TEXT,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(4, 2))
        # live status
        status = self._status_text(kind)
        tk.Label(self.body, text=status, bg=_BG, fg=_MUTED, font=("Segoe UI", 9),
                 anchor="w", justify="left", wraplength=680).pack(anchor="w", pady=(0, 10))
        row = tk.Frame(self.body, bg=_BG)
        if kind == "autostart":
            # Normal OR elevated. Windows normal = a Startup shortcut, elevated = a
            # logon Scheduled Task; Linux/macOS elevate via pkexec / an admin prompt.
            # Launches through the run script.
            tk.Label(self.body, text="Runs your run script at login (per-user). Choose normal or "
                     "elevated — Windows normal = a Startup shortcut, elevated = a logon Scheduled "
                     "Task; Linux/macOS elevate via pkexec / an admin prompt. Use “Test now” to "
                     "launch it immediately without logging out.",
                     bg=_BG, fg=_MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                     wraplength=680).pack(anchor="w", pady=(0, 8))
            as_priv = tk.StringVar(value=self._current_priv("autostart"))
            for val, label in (("normal", "Normal privileges"),
                               ("admin", "Start as administrator (elevated at login)")):
                tk.Radiobutton(self.body, text="  " + label, variable=as_priv, value=val,
                               bg=_BG, fg=_TEXT if val == "normal" else _YELLOW, selectcolor=_ELEV,
                               activebackground=_BG, font=("Segoe UI", 10), anchor="w",
                               highlightthickness=0, bd=0).pack(fill="x")
            row.pack(fill="x", pady=(12, 0))
            self._btn(row, "Enable / Update", lambda: self._run(
                lambda r: do_autostart(True, r, as_admin=as_priv.get() == "admin")),
                "accent").pack(side="left")
            self._btn(row, "Disable", lambda: self._run(
                lambda r: do_autostart(False, r)), "normal").pack(side="left", padx=(8, 0))
            self._btn(row, "Test now", lambda: self._run(
                lambda r: do_autostart_test(r)), "normal").pack(side="left", padx=(8, 0))
        else:
            priv = tk.StringVar(value=self._current_priv("shortcut"))
            for val, label in (("normal", "Normal privileges"),
                               ("admin", "Run as administrator (elevation prompt each launch)")):
                tk.Radiobutton(self.body, text="  " + label, variable=priv, value=val,
                               bg=_BG, fg=_TEXT if val == "normal" else _YELLOW, selectcolor=_ELEV,
                               activebackground=_BG, font=("Segoe UI", 10), anchor="w",
                               highlightthickness=0, bd=0).pack(fill="x")
            row.pack(fill="x", pady=(12, 0))
            self._btn(row, "Create / Update", lambda: self._run(
                lambda r: do_shortcut(True, priv.get() == "admin", r)), "accent").pack(side="left")
            self._btn(row, "Remove", lambda: self._run(
                lambda r: do_shortcut(False, False, r)), "normal").pack(side="left", padx=(8, 0))
        self._btn(row, "Back", self.show_landing, "normal").pack(side="right")

    def _current_priv(self, kind):
        """Reflect the existing entry's privilege so the radio opens on its real
        state ('admin'/'normal'); defaults to 'normal' when nothing exists."""
        sc, aut = load_app_modules()
        try:
            if kind == "autostart" and aut is not None:
                st = aut.status()
                if st.get("enabled") and st.get("admin"):
                    return "admin"
            if kind == "shortcut" and sc is not None:
                st = sc.status()
                if st.get("exists") and st.get("admin"):
                    return "admin"
        except Exception:
            pass
        return "normal"

    def _status_text(self, kind):
        sc, aut = load_app_modules()
        try:
            if kind == "autostart" and aut is not None:
                st = aut.status()
                if st["enabled"]:
                    lvl = "administrator" if st.get("admin") else "normal"
                    m = st.get("method")
                    mtxt = f", {m}" if m and m not in ("native", None) else ""
                    return f"Currently: enabled — runs at login ({lvl}{mtxt})."
                return "Currently: not set to start at login."
            if kind == "shortcut" and sc is not None:
                st = sc.status()
                if st["exists"]:
                    return f"Currently: shortcut present ({'administrator' if st['admin'] else 'normal'})."
                return "Currently: no desktop shortcut."
        except Exception as e:
            return f"Status unavailable: {e}"
        return ""

    def confirm_uninstall(self):
        from tkinter import messagebox
        if not messagebox.askyesno(
                "Uninstall / clean",
                "Remove the generated scripts, autostart entry and desktop shortcut?\n\n"
                "Your data/ (history, logs, settings), main.py and the systema/ source "
                "are NOT touched."):
            return
        rm_venv = messagebox.askyesno("Uninstall / clean", "Also delete the .venv "
                                      "(you'd need to re-run Setup to use the app)?")
        self._run(lambda r: uninstall_clean(rm_venv, r))

    def _bootstrap(self):
        from tkinter import messagebox
        info = detect_platform_installer()
        cmds = info["py_cmds"] + info["sys_cmds"]
        if not cmds:
            messagebox.showinfo("Install prerequisites",
                                "No automatic installer for this OS.\n\n" + (info["note"] or ""))
            return
        preview = "\n".join("$ " + " ".join(c) for c in cmds)
        note = ("\n\n" + info["note"]) if info["note"] else ""
        extra = ("\n\nA terminal will open so you can enter your sudo password."
                 if not (IS_WIN or IS_MAC) else "")
        if not messagebox.askyesno(
                "Install Python 3.10 + prerequisites",
                f"Run these with {info['name']}?\n\n{preview}{note}{extra}"):
            return
        self._run(lambda r: bootstrap_prerequisites(r, gui=True))

    # ── action runner: worker thread + log view ──
    def _run(self, fn):
        if self._busy:
            return
        self._busy = True
        self._clear_body()
        tk = self.tk
        self.log = tk.Text(self.body, bg=_SURF, fg=_TEXT, insertbackground=_TEXT,
                           relief="flat", bd=0, wrap="word", font=("Consolas", 9),
                           height=20, padx=10, pady=8)
        self.log.pack(fill="both", expand=True)
        for tag, col in (("ok", _GREEN), ("warn", _YELLOW), ("fail", _RED),
                         ("step", _ACCENT), ("info", _MUTED)):
            self.log.tag_config(tag, foreground=col)
        self.log.configure(state="disabled")
        self._back_btn = self._btn(self.body, "Back", self.show_landing, "normal")
        self._back_btn.pack(anchor="w", pady=(10, 0))
        self._back_btn.configure(state="disabled")

        def worker():
            r = QueueReporter(self.q)
            try:
                fn(r)
            finally:
                self.q.put(("__done__", ""))
        threading.Thread(target=worker, daemon=True).start()

    def _pump(self):
        try:
            while True:
                kind, text = self.q.get_nowait()
                if kind == "__done__":
                    self._busy = False
                    try:
                        self._back_btn.configure(state="normal")
                    except Exception:
                        pass
                    continue
                self._append(kind, text)
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _append(self, kind, text):
        prefix = {"step": "▸ ", "ok": "  ✓ ", "warn": "  ! ", "fail": "  ✗ ", "info": "    "}.get(kind, "")
        try:
            self.log.configure(state="normal")
            self.log.insert("end", prefix + text + "\n", kind)
            self.log.see("end")
            self.log.configure(state="disabled")
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    args = [a.lower() for a in sys.argv[1:]]
    if any(a in ("--recover", "--recovery", "--repair", "--regen") for a in args):
        run_recovery(ConsoleReporter())
        return
    if gui_available():
        try:
            SetupGUI().run()
            return
        except Exception as e:
            print(c(f"GUI failed ({e}) — falling back to terminal.", "yellow"))
    terminal_main()


if __name__ == "__main__":
    main()
