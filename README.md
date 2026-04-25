# Systema Auxilium

**License:** MIT

**Author / Architect:** Nic2007 ([@uukjtisa](https://github.com/uukjtisa))

> ⚠️ **Work in Progress** — This project is actively developed. Not all features are fully polished or tested. Contributions and patience are genuinely appreciated.

---

## Things to take into consideration.

This is a **hobby project** built and maintained by a single person.

**Bugs are expected.** Some features have not been deeply tested, and certain functions may behave unexpectedly depending on your setup. And tool calls may leak if the LLM misses proper usage. If something breaks, please try to replicate it and open an issue. It would help a lot if.

## **Lighter models may struggle to follow the system prompt tool format reliably.**
- **Model compatibility is limited.** This project was developed and tested almost exclusively using the **GPT-5.2** model via the free [Puter.js](https://puter.com) API. Other models, both lighter open-source ones and stronger alternatives, have **not** been tested. 
- When the LLM fails to follow the format in tool usage, unintentional results may occur, like tool usage leaking into chat.
- I'm open to suggestions for suggestions in Revamping the tool system. If you're experienced in agentic stuff, please consider mentoring me! **:)**

Development happens in whatever time I can spare. Updates may be slow, inconsistent, or temporarily halted during academic periods. I have no prior experience maintaining a codebase of this scale, so there may be structural ambiguity in places. I appreciate your patience.

**Co-authors are welcome.** If you find this project interesting and want to help build it, you are more than welcome. No experience bar. No formality. Just reach out or open a PR.

---

## 🎬 See It In Action

[▶ Watch the Demo on Google Drive](https://drive.google.com/file/d/1RmdZhXz-lvwBVq2JGpx3q7bip8Jtmynm/view?usp=sharing)

---

## What is Systema Auxilium?

**Systema Auxilium** (Latin for *"System Helper"*) is an AI-powered desktop assistant that lets you control your computer through plain natural language. Instead of writing scripts or memorizing commands, you simply describe what you want done — and the assistant figures out the Python code to make it happen.

It serves as a personalized companion that helps with general automation, making powerful OS operations accessible to anyone.

---

## What Can It Do?

Systema Auxilium uses Python to do active work on you computer. So it can do almost anything the Python Interpreter can do on your computer.

**File & Folder Management**
Read, write, create, move, rename, or delete files and directories. Analyse folders, count file types, calculate sizes, list contents — all from a single sentence.

**App & Window Control**
Open, launch, or close applications. Show popups, dialogs, or custom GUI windows. Interact with your desktop environment programmatically.

**System Information & Monitoring**
Check CPU usage, memory, disk space, running processes, system specs, and more.

**Calculations & Data Processing**
Perform complex calculations, process datasets, read and parse files (text, CSV, etc.), and return structured results.

**Voice Mode**
Speak to Systema Auxilium and have it respond via text-to-speech. Supports ElevenLabs for realistic emotional voice output, including natural expressions like laughter, sighs, and emphasis.

**Multi-Provider AI Support**
Switch between AI backends with ease:
- **Puter.js** — free, browser-based, no API key required (primary tested provider)
- **Anthropic Claude API** — integrated, configurable models
- **Google Gemini** — integrated, configurable models
- **Manual Provider** — type responses by hand, useful for testing and debugging
- **Custom Script Provider** — point to any `.py` file and use it as your AI backend. No need to touch the main codebase. Think of it like modding — write a script, plug it in, done. See `custom_provider_template.py` for instructions.

**Session Naming**
The assistant automatically names each conversation session so you can easily navigate back to previous tasks.

**Guided Execution Mode** *(Implemented as of now but not perfect. Any suggestions would be welcomed.)*
A safety-first mode that shows you the generated Python code *before* it runs, so you can review, approve, or reject every action.

---

## Custom Script Provider — Bring Your Own Provider

You can connect *any* AI provider to Systema Auxilium without touching the main codebase at all. Just write a Python script that implements:

```python
def chat(system_prompt: str, messages: list[dict]) -> str:
    ...
```

Point the app at it in Settings → AI → Custom Script Provider. The script is reloaded on every request, so live edits take effect immediately. A full template and instructions are included in `custom_provider_template.py` at the repo root, including a ready-made prompt you can give to any AI to generate a working provider script for you.

---

## About AI System Control

### User Responsibility

Like any powerful tool, Systema Auxilium requires responsible use. Users should understand the commands they're authorizing. The system warns about elevated permissions on startup. Running with minimal necessary privileges and maintaining regular backups is recommended. Whether you write a Python script manually or use an AI assistant — you are ultimately responsible for code execution on your system.

---

## Recommended Python Environment

It is **strongly recommended** to run this project using **Python 3.10.11**, the exact version used during development. Using a different Python version may lead to module incompatibilities or unexpected behavior.

**Official Python 3.10.11 release:** https://www.python.org/downloads/release/python-31011/

---

## How to Install

Run `create_env.bat` — this will install all dependencies and generate a `run.bat` for you.

Tested on **Windows 11** and **Windows 10**.

Alternatively, set up manually:

1. Ensure Python 3.10.11 is installed
2. Install dependencies: `pip install -r requirements.txt`
3. Configure your AI provider in Settings (Puter.js works out of the box — no key needed)
4. Run the application: Double click `run.bat` (works with or without Admin privileges)

---

## Safety Warnings

⚠️ **IMPORTANT SAFETY INFORMATION** ⚠️

- This AI can execute system-level actions if run with sufficient permissions
- Use caution when issuing prompts and commands
- Review generated code before execution (especially once Guided Mode is complete)
- You are responsible for all actions taken by the system
- Consider running in a virtual machine or test environment initially
- Keep regular backups of important data

---

## Contributing

This project is maintained by one person only as of April 25, 2026, and has grown to over 22,000 lines. Contributions of any kind are genuinely welcome — bug reports, feature ideas, pull requests, and general feedback all help.

If you'd like to get involved, please see [CONTRIBUTING.md](CONTRIBUTING.md) or reach out directly.

---

## NOTICE

For authorship, AI tooling disclosure, and legal information, see the [NOTICE](NOTICE) file.

---

**Systema Auxilium** — Nic2007