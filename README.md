# Systema Auxilium

**An AI-powered desktop assistant that controls your computer through natural language.**

**License:** MIT  ·  **Author / Architect:** Niccc2007 ([@uukjtisa](https://github.com/uukjtisa))

> **Status:** Work in progress. Actively developed by one person; not every feature is fully polished, and bugs are expected. Issues, ideas, and pull requests are genuinely appreciated.

---

## Table of Contents

- [What is Systema Auxilium](#what-is-systema-auxilium)
- [A Glimpse of It](#a-glimpse-of-it)
- [What It Can Do](#what-it-can-do)
- [AI Providers and Tool Calling](#ai-providers-and-tool-calling)
  - [Modular Provider Architecture](#modular-provider-architecture)
  - [Configure a Provider (first-time setup)](#configure-a-provider-first-time-setup)
  - [Tool Calling: Native and Compatibility](#tool-calling-native-and-compatibility)
- [Software Updates](#software-updates)
- [Android Bridge App](#android-bridge-app)
- [Installation](#installation)
- [Recommended Python Environment](#recommended-python-environment)
- [Responsible Use and Safety](#responsible-use-and-safety)
- [Project Status and Considerations](#project-status-and-considerations)
- [Contributing](#contributing)
- [Notice](#notice)

---

## What is Systema Auxilium?

**Systema Auxilium** (Latin for *"System Helper"*) is an AI desktop assistant that lets you
control your computer in plain language. Instead of writing scripts or memorizing commands,
you describe what you want, and the assistant writes and runs the Python needed to do it,
observing the result and iterating until the task is done.

It runs locally as a PyQt6 desktop app. The reasoning comes from a configurable LLM provider
of your choice; everything else, including code execution, runs on your machine.

> **Full documentation lives in [`docs/`](docs/).** Every subsystem — providers,
> tool calling, the Python interpreter, security, tasks, updates, voice/TTS, skills & memory,
> and the Android bridge — has its own page. Start at [docs/README.md](docs/README.md).

---

## A Glimpse of It

<p align="center">
  <img src="demos/1.png" width="45%" />
  <img src="demos/2.png" width="45%" />
</p>
<p align="center">
  <img src="demos/3.png" width="45%" />
  <img src="demos/4.png" width="45%" />
</p>
<p align="center">
  <img src="demos/5.png" width="45%" />
  <img src="demos/6.png" width="45%" />
</p>
<p align="center">
  <img src="demos/7.png" width="45%" />
  <img src="demos/8.png" width="45%" />
</p>

---

## What It Can Do

Systema Auxilium works by writing and running Python on your computer, so its reach is broad.
Reviewing generated code before it runs (see [Responsible Use](#responsible-use-and-safety)) is
strongly encouraged.

- **File and folder management** — read, write, create, move, rename, delete; analyze folders,
  count file types, compute sizes, list contents from a single sentence.
- **App and window control** — open, launch, or close applications; show popups, dialogs, or
  custom GUI windows; interact with your desktop programmatically.
- **System information and monitoring** — CPU, memory, disk, running processes, specs, and more.
- **Calculations and data processing** — complex math, dataset processing, reading and parsing
  files (text, CSV, and so on) into structured results.
- **Voice mode** — speak to the assistant and hear replies via text-to-speech. ElevenLabs is
  supported out of the box (expressive output including laughter, sighs, emphasis), plus any
  custom TTS provider through the modular script system.
- **Modular AI providers** — every AI backend is a self-contained script you drop into a folder.
  No hardcoded providers, no codebase changes.
- **Scheduled tasks** — persistent background agents that ping the AI on a schedule, each with
  its own instruction, active window, ping interval, permissions, and pre-loaded skills. Task
  agents can send messages straight into your main chat.
- **Skills** — focused instruction packs (under `skills/`) that give the assistant specialized
  knowledge and helper scripts for specific jobs.
- **Self-knowledge** — the assistant can explain what it is and how it works, and check its own
  status and updates on request (see [Software Updates](#software-updates)).
- **Self-updating** — update the app from GitHub inside the app: review the exact changes, pick
  which files to apply, auto-install new dependencies, with a backup and one-click revert. Files
  that hold your configured provider accounts/keys are flagged and never blindly overwritten, and a
  built-in Manage view lets you resolve those changes hunk-by-hunk (or line-by-line) yourself.
- **Session naming** — conversations are named automatically so you can navigate back easily.
- **Guarded execution** — reviews RISKY generated Python before it runs: an automatic static risk
  scan, a built-in code editor you can chat with that proposes a safer version as a one-click-apply
  diff, per-category allow/ask/deny policy, and a "don't ask again" memory. Obviously-safe snippets
  (plain print, math) run without a prompt. You review, edit, approve, or reject every action.

> Scheduled tasks can't answer an approval prompt, so each task's risky operations are governed by a
> per-category allow/deny security policy you set up front — be deliberate about what you allow.

---

## AI Providers and Tool Calling

### Modular Provider Architecture

Systema Auxilium uses a fully modular provider system for both AI inference and text-to-speech.
There is no hardcoded provider list; each provider is a self-contained Python file in
`providers/`.

Each provider implements a small contract:

- **LLM providers** (`providers/large-language-models/`) define `chat(system_prompt, messages) -> str`
  - Optional: `chat_image(system_prompt, messages, image_paths)` for vision
  - Optional: `chat_tools(system_prompt, messages, tools, images=None) -> dict` for native
    function calling (see [Tool Calling](#tool-calling-native-and-compatibility))
- **TTS providers** (`providers/text-to-speech/`) define `speak(text, save_to) -> bool`

Drop a script in the right folder, hit Refresh in Settings, and it appears instantly. No codebase
edits, no restart.

**Included scripts:**

- LLM (all support native function calling unless noted):
  - `anthropic_provider.py` — Anthropic Claude
  - `gemini_provider.py` — Google Gemini
  - `provider_cloudflare_kimi_k2_6.py`, `provider_cloudflare_kimi_k2_7_code.py` — Cloudflare Workers AI / Kimi K2
  - `provider_nvidia_glm51.py`, `provider_nvidia_deepseek_v4_flash.py`, `provider_nvidia_deepseek_v4_pro.py` — NVIDIA Inference API
  - `provider_opencode_zen.py` — OpenCode Zen (free, OpenAI-compatible gateway)
  - `llama-cpp-provider.py` — fully offline local GGUF models (native tool calling is opt-in and model-dependent)
- TTS: `elevenlabs_tts.py` (expressive voice synthesis)

Each folder ships a `_template.py` with a ready-to-use skeleton, full docstrings, and a
paste-ready prompt you can give any AI to generate a working provider for your API. The barrier
to adding a provider is essentially zero.

### Configure a Provider (first-time setup)

> Before you can chat, configure at least one LLM provider. This is the only real setup step and
> works like any other AI platform: you supply an API key or credential for the service you want.

1. Open `providers/large-language-models/` and pick an included script (or copy `_template.py`).
2. Edit the file and fill in your API key, model name, or endpoint URL (clearly marked at the top).
3. In the app: **Settings -> AI -> Active Provider Script**, select your script, and **Save Settings**.

Provider configuration lives in the script rather than the GUI because every provider has
different fields; this keeps the codebase clean.

### Tool Calling: Native and Compatibility

The assistant drives its tools (the Python interpreter, file editing, skill load/unload, session
naming) in one of two modes. Switch in **Settings -> System -> Tool Calling Mode**:

- **Native.** Tool calls travel through the provider's own function-calling API. The system prompt
  is rebuilt fence-free, so the model relies purely on the structured tools channel. This is the
  more reliable path: invocations are well-formed by construction, so there is no fenced format
  for a model to mis-write and nothing leaks into chat. It requires a provider that genuinely
  supports function calling. Background tasks work in native too (a task agent reaches your main
  chat via `send_message_main()` in its Python namespace).
- **Compatibility (legacy, universal fallback).** Tools are described in the system prompt and the
  model invokes them as fenced blocks (for example ```` ```python_interpreter ````). This works with
  *any* model or provider, with no special API support. It costs prompt tokens, and a weaker model
  can occasionally mis-format a call. The app includes recovery safeguards for malformed calls, and
  if a provider does not declare native support (or ignores `tools`), it automatically falls back
  here, so nothing breaks.

A provider opts into native by declaring two markers and one function:

```python
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"   # "openai" | "anthropic" | "gemini"

def chat_tools(system_prompt, messages, tools, images=None) -> dict:
    # convert `tools` to your dialect, call the API, and return the normalized:
    #   {"text": str | None, "tool_calls": [{"id", "name", "arguments"}, ...]}
    ...
```

`systema/engine/native_adapters.py` handles schema conversion and response parsing for all three
dialects, so a native provider is only a few lines. See `_template.py` for a full example.

> Native tool-calling quality still varies by model. If a particular model misbehaves in native
> mode, switch to Compatibility, which always works.

---

## Software Updates

Systema Auxilium can update itself from GitHub, in-app, under **Settings -> System** (full controls)
or **Settings -> General** (shortcut) -> **Check for Updates**:

- **Review before applying** — see every changed file and its exact diff; the files with real
  textual changes are highlighted and selected for you, and you choose what to apply. The commit
  message(s) behind an update are shown, stacked when several updates have piled up unapplied.
- **Preserves your local edits** — a 3-way merge folds upstream changes into files you have
  modified; genuine conflicts are marked for you to resolve rather than silently overwritten.
- **Protects your configured accounts** — files that hold your provider accounts, API keys, or
  tokens (e.g. under `providers/`) are flagged and auto-deselected, so an update can't wipe your
  configuration. A built-in Manage view lets you resolve these hunk-by-hunk (or line-by-line)
  yourself — keep your version, take the update, or hand-edit — before anything is written.
- **Dependencies** — newly required Python packages are detected and installed automatically.
- **Backup and revert** — a snapshot is taken before anything changes, so the whole update can be
  reverted with one click. Snapshot history is kept.
- **Your data is never touched** — settings and the `data/` folder are excluded from updates.
- **Startup check** — if a new version is available, the app offers to open the updater on launch.

The updater is powered by the open-source library
[**updater-gitplucker**](https://github.com/uukjtisa/updater-gitplucker)
(`pip install updater-gitplucker`), which is reusable in any Python project.

---

## Android Bridge App

Talk to your assistant from anywhere on your local network.

- Repo: [systema-auxilium-android-module](https://github.com/uukjtisa/systema-auxilium-android-module)
- Releases: [download](https://github.com/uukjtisa/systema-auxilium-android-module/releases)

Enable the bridge in **Settings -> System -> Android Packet** and connect the phone app over
Wi-Fi LAN using `IP:port`.

---

## Installation

### Quick setup (all platforms)

Run the unified setup script with any system Python. It auto-detects your OS, creates a `.venv`,
lets you pick optional feature modules, generates the helper scripts, and installs dependencies:

```bash
python setup.py            # Windows
python3 setup.py           # Linux / macOS
python setup.py --cli      # force the terminal menu (skip the GUI)
python setup.py --recover  # only regenerate the helper scripts (venv untouched)
```

`setup.py` opens a small **graphical window** when tkinter and a display are available, and falls
back to a robust **terminal menu** otherwise (Kali/Debian ship Python without `python3-tk`; install
it with `sudo apt install python3-tk` if you want the GUI). The dependency list lives in `setup.py`
and it **generates `requirements.txt`** from it, so the two never drift — that generated file is kept
because the in-app self-updater reads it and `pip install -r requirements.txt` still works.

Afterwards you will have helper scripts in the project root:

- **Windows:** `run.bat`, `open_env.bat`, `add_autostart.bat`, `remove_autostart.bat`
- **Linux / macOS:** `run.sh` / `run.command`, `open_env.sh`, `add_autostart.sh`, `remove_autostart.sh`
  (Linux also gets a double-click `Systema Auxilium.desktop`)

⚠ **Do not delete the generated scripts, `requirements.txt`, `main.py`, `systema/`, `assets/` or
`data/`** — the app, its Restart button, autostart and the self-updater depend on them. A
`__DO_NOT_DELETE__.txt` in the project root lists them all with why. If a generated script is ever
lost, regenerate them with `python setup.py --recover` (no reinstall needed).

`setup.py` then offers to move itself into a `setup-scripts/` folder (defaults to no; the app's
recovery + updater expect it in the root). It is self-contained and replaces the old per-platform
setup scripts.

Tested on Windows 11, Windows 10, and Kali Linux (Python 3.10). The macOS path is untested.

### Manual setup (any platform)

1. Install Python 3.10.11.
2. Install dependencies: `pip install -r requirements.txt`
3. Configure an AI provider (see [Configure a Provider](#configure-a-provider-first-time-setup)).
4. Run the app: `python main.py`

---

## Recommended Python Environment

It is strongly recommended to run this project on **Python 3.10.11**, the exact version used during
development. Other versions may cause module incompatibilities or unexpected behavior.

- Official Python 3.10.11 release: https://www.python.org/downloads/release/python-31011/

Kali Linux install steps used during development (use at your own discretion):

```bash
sudo apt install -y build-essential wget libssl-dev zlib1g-dev \
libncurses5-dev libncursesw5-dev libreadline-dev libsqlite3-dev \
libgdbm-dev libdb5.3-dev libbz2-dev libexpat1-dev liblzma-dev tk-dev

cd /tmp
wget https://www.python.org/ftp/python/3.10.14/Python-3.10.14.tgz
tar -xf Python-3.10.14.tgz
cd Python-3.10.14

./configure --enable-optimizations
make -j$(nproc)
sudo make altinstall
```

---

## Responsible Use and Safety

Like any powerful tool, Systema Auxilium requires responsible use. Whether code is written by hand
or generated by an AI, you are responsible for what runs on your system.

- The assistant can perform system-level actions if run with sufficient permissions.
- Be deliberate with your prompts and with what you approve.
- Review generated code before execution, especially with guarded execution.
- The app warns about elevated permissions on startup; prefer minimal necessary privileges.
- Consider running in a VM or test environment initially, and keep regular backups.

---

## Project Status and Considerations

This is a hobby project built and maintained by one person, developed and tested primarily on
Windows 11, with Windows 10 and Kali Linux VMs (both on Python 3.10). Only one thing can be tested
at a time, so unintended behaviors are possible.

- **Bugs are expected.** Some features are not deeply tested. If something breaks, try to reproduce
  it and open an issue with details. Fixes and PRs are very welcome.
- **Model capability matters.** The project was built and tested with strong frontier models.
  Weaker models are less predictable. In **Compatibility** tool-calling mode a weak model can
  mis-format a fenced call; **Native** tool-calling mode avoids the fenced format entirely and is
  the more reliable option with a capable, function-calling provider. The modular provider system
  lets you test any model yourself.
- **System prompt is still being refined** to trim redundant instructions.
- Suggestions on the tooling and agent design are welcome; if you have agentic experience, mentoring
  is appreciated.

Development happens in whatever time is available, so updates may be slow or bursty. Co-authors are
welcome, with no experience bar; just reach out or open a PR.

---

## Contributing

Contributions of any kind are genuinely welcome: bug reports, feature ideas, pull requests, and
feedback. See [CONTRIBUTING.md](CONTRIBUTING.md) or reach out directly.

---

## Notice

For authorship, AI tooling disclosure, and legal information, see the [NOTICE](NOTICE) file.

---

**Systema Auxilium** — Niccc2007
