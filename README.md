# Systema Auxilium

**Current License:** Systema Auxilium Community License v2.0
**Author / Architect:** Nic2007 ([@uukjtisa](https://github.com/uukjtisa))

> ⚠️ **Work in Progress** — This project is actively being developed. Not all features are fully polished or tested. Currently, [Puter.js](https://puter.com) is the primary tested API provider. Other providers (Anthropic Claude API, Google Gemini) are integrated but remain largely untested due to resource constraints as I am currently a grade 12 graduating student with no significant income. Contributions and patience are greatly appreciated.

---

## 🎬 See It In Action

[▶ Watch the Demo on Google Drive](https://drive.google.com/file/d/1RmdZhXz-lvwBVq2JGpx3q7bip8Jtmynm/view?usp=sharing)

---

## What is Systema Auxilium?

**Systema Auxilium** (Latin for *"System Helper"*) is an AI-powered desktop assistant that lets you control your computer through plain natural language. Instead of writing scripts or memorizing commands, you simply describe what you want done — and the assistant figures out the Python code to make it happen.

It bridges the gap between human language and system-level automation, making powerful OS operations accessible to anyone.

---

## What Can It Do?

Systema Auxilium uses Python as its "hands" — meaning anything Python can do on your computer, Systema Auxilium can do too. Here's what that looks like in practice:

**File & Folder Management**
Read, write, create, move, rename, or delete files and directories. Analyse folders, count file types, calculate sizes, list contents — all from a single sentence.

**App & Window Control**
Open, launch, or close applications. Show popups, dialogs, or custom GUI windows. Interact with your desktop environment programmatically.

**System Information & Monitoring**
Check CPU usage, memory, disk space, running processes, system specs, and more — then get a clear human-readable summary.

**Calculations & Data Processing**
Perform complex calculations, process datasets, read and parse files (text, CSV, etc.), and return structured results.

**Voice Mode**
Speak to Systema Auxilium and have it respond via text-to-speech. Supports ElevenLabs for realistic emotional voice output, including natural expressions like laughter, sighs, and emphasis.

**Multi-Provider AI Support**
Switch between AI backends — Puter.js (free, primary), Anthropic Claude API, and Google Gemini — with configurable models per provider.

**Session Naming**
The assistant automatically names each conversation session so you can easily navigate back to previous tasks.

**Guided Execution Mode** *(In Development)*
A safety-first mode that shows you the generated Python code *before* it runs, so you can review, approve, or reject every action.

**"What Does This Do?" Button** *(Planned)*
For non-technical users — get a plain-English breakdown of any generated code before it executes.

The core idea: **if Python can do it on your machine, you can ask Systema Auxilium to do it for you.**

---

## About AI System Control

### Is this "bad practice"?

**Short answer: No, when implemented responsibly.**

Systema Auxilium represents a new paradigm in human-computer interaction, where AI serves as an intelligent intermediary between natural language and system operations. While some may view AI-controlled system access as risky, the same concerns apply to any automation tool, scripting language, or remote administration software.

Consider: scripting languages (Python, PowerShell, Bash) have always allowed programmatic system control. Task automation tools execute system commands based on triggers. Remote desktop software grants external control over systems. Package managers run installation scripts with elevated privileges.

The key difference is that Systema Auxilium makes automation accessible through natural language — but the underlying mechanism of executing vetted code remains the same as traditional scripting.

### User Responsibility

Like any powerful tool, Systema Auxilium requires responsible use. Users should understand the commands they're authorizing. The system warns about elevated permissions on startup. It is recommended to run with minimal necessary privileges, and regular backups are advised.

Whether you write a Python script manually, copy code from StackOverflow, or use an AI assistant — you are ultimately responsible for code execution on your system. Systema Auxilium simply changes the interface, not the responsibility.

---

## Recommended Python Environment

It is **strongly recommended** to run this project using **Python 3.10.11**, the exact version used during development. Using a different Python version may lead to module incompatibilities or unexpected behavior.

**Official Python 3.10.11 release:** https://www.python.org/downloads/release/python-31011/

---

## How to Install

Run `create_env.bat` — this will install all dependencies and generate a `run.bat` for you.

Tested on **Windows 11** and **Windows 10**.

Alternatively, you can set up manually:

1. Ensure Python 3.10.11 is installed
2. Install dependencies: `pip install -r requirements.txt`
3. Configure your AI provider API keys in settings
4. Run the application: `python main.py`

---

## Safety Warnings

⚠️ **IMPORTANT SAFETY INFORMATION** ⚠️

- This AI can execute system-level actions if run with sufficient permissions
- Use caution when issuing prompts and commands
- Review generated code before execution (especially once Guided Mode is available)
- You are responsible for all actions taken by the system
- Consider running in a virtual machine or test environment initially
- Keep regular backups of important data

---

## Contributing

This project is maintained by one person and is growing beyond what one person can easily manage. Contributions of any kind are genuinely welcome — bug reports, feature ideas, pull requests, and general feedback all help.

If you'd like to get involved, please see [CONTRIBUTING.md](CONTRIBUTING.md) or reach out directly.

> A personal note: I'm currently in Grade 12 and built this without a budget for paid API access, so testing across all providers has been limited. If you have access to Claude API or Gemini and want to help test or improve those integrations, that would mean a lot.

---

## NOTICE

For authorship, AI tooling disclosure, and legal information, see the [NOTICE](NOTICE) file.

---

**Systema Auxilium** — Bridging natural language and system automation through responsible AI integration.
