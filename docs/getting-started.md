# Getting Started

## Requirements

- **Python 3.10.11** is strongly recommended (the exact version used in
  development). Other 3.10.x releases usually work; far-newer/older versions may
  hit dependency incompatibilities.
- Windows 10/11 are the primary tested platforms. Kali Linux (Python 3.10) is
  tested. macOS support exists in the code paths but is untested.

## Install

### Quick setup (all platforms)

Run the unified setup script with any system Python. It auto-detects your OS,
creates a `.venv`, lets you pick optional modules, generates the helper scripts,
and installs the dependencies:

```bash
python setup.py            # Windows
python3 setup.py           # Linux / macOS
python setup.py --cli      # force the terminal menu (skip the GUI)
python setup.py --recover  # only regenerate the helper scripts (venv untouched)
```

It shows a small graphical window when tkinter + a display are available, else a
terminal menu (on Kali/Debian, `sudo apt install python3-tk` enables the GUI).
The dependency list lives in `setup.py`, which **generates `requirements.txt`**
from it — so they never drift, and the self-updater + `pip install -r` still work.

Afterwards you get helper scripts in the project root:

- **Windows:** `run.bat`, `open_env.bat`, `add_autostart.bat`, `remove_autostart.bat`
- **Linux / macOS:** `run.sh` / `run.command`, `open_env.sh`, `add_autostart.sh`, `remove_autostart.sh`

Start-at-login and the desktop shortcut can run with **normal or administrator**
privileges (choose in setup or in Settings → General). A `__DO_NOT_DELETE__.txt`
in the root lists every file the app depends on — if a generated script is lost,
`python setup.py --recover` regenerates them all. `setup.py` also offers to move
itself into `setup-scripts/`.

### Manual setup

```bash
pip install -r requirements.txt
python main.py
```

## Known limitations

- **Start-at-login on Linux is unreliable (known issue).** On the developer's Kali
  Linux the autostart entry does **not** fire at login, despite the app installing
  it for the invoking login user, honouring `$XDG_CONFIG_HOME`, using a launcher
  that waits for the graphical session, and offering a systemd `--user` fallback.
  The root cause is not yet identified, and autostart is **untested on other Debian
  systems and desktop environments** — treat login autostart on Linux as best‑effort
  for now. Workaround: launch the app manually with `run.sh` or from the desktop
  shortcut. If you want to help diagnose it, use **Settings → General → Start at
  Login → Diagnose / View log** after a login to see whether the entry fired and
  where it stopped.
- **macOS is untested.** The macOS code paths (LaunchAgent autostart, the
  `.command` launchers, the admin/elevated variants) exist but have not been run on
  a real Mac.

## First run: configure a provider

Before you can chat you must configure at least one LLM provider. Providers are
plain Python scripts under `resources/providers/large-language-models/`.

1. Open `resources/providers/large-language-models/` and pick an included script (or copy
   `_template.py`).
2. Edit it and fill in your API key / model / endpoint — all clearly marked
   constants at the top of the file.
3. In the app: **Settings → AI → Active Provider Script**, select your script,
   and **Save Settings**.

Configuration lives in the script (not the GUI) because every provider has
different fields. See [Providers](providers.md) for the full contract and for a
paste-ready prompt that lets any AI generate a provider for your API.

> Tip: every included provider has a `__main__` block. Run it directly
> (`python resources/providers/large-language-models/<your_provider>.py`) to verify your key
> and connection before selecting it in the app.

## Choose a tool-calling mode

Under **Settings → System → Tool Calling Mode** pick:

- **Native** — tools travel through the provider's function-calling API. More
  reliable, lighter prompt; requires a provider that genuinely supports function
  calling.
- **Compatibility** — tools are described in the prompt and invoked as fenced
  blocks. Works with any model. The app auto-falls-back to this if a provider
  does not declare native support.

See [Tool Calling](tool-calling.md) for details.

## Decide how much oversight you want

By default **Supervised Execution** is ON: the assistant shows you the Python it
wants to run and waits for approval. You can tune this from a prompt-everything
posture down to full trust — see [Security & Guarded Execution](security.md).

## Optional: voice, tasks, updates, phone

- **Voice** — speak and hear replies; see [Voice & TTS](voice-and-tts.md).
- **Tasks** — wake the assistant on a schedule or a signal; see [Tasks](tasks.md).
- **Updates** — pull reviewed updates from GitHub; see [Updates](updates.md).
- **Android** — control it from your phone over LAN; see [Android Bridge](android-bridge.md).
