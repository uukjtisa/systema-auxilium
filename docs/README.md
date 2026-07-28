# Systema Auxilium — Documentation

Systema Auxilium is an open-source, flexible PyQt6 desktop AI harness that
controls your computer in plain language: you describe a goal, the assistant
writes and runs the Python needed to reach it, observes the result, and iterates.
The reasoning comes from a provider you configure — a hosted API or a local model
you run yourself — while code execution and the app run on your machine. What
leaves your machine depends on which provider you choose.

This folder documents every major subsystem. Start with **Getting Started**, then
read whichever areas you use.

## Contents

| Doc | What it covers |
| --- | --- |
| [Getting Started](getting-started.md) | Install, first run, configure a provider, run the app. |
| [Providers](providers.md) | The modular provider system: the unified `chat()` contract (vision, native tools, streaming, `Display` settings forms), the `speak` TTS contract, included scripts, and how to add your own (or have an AI write one). |
| [Tool Calling](tool-calling.md) | Native vs Compatibility modes, the native contract, and `native_adapters` (openai / anthropic / gemini dialects). |
| [The Python Interpreter & Code Execution](python-interpreter.md) | How the assistant writes and runs Python: interpreter mode, direct execution, the persistent interpreter, `#@FILE` literal blocks, and finishing. |
| [Security & Guarded Execution](security.md) | Supervised Execution, the per-category policy engine, presets, the code-approval dialog (session / persistent allow, reason box), and the audit log. |
| [Scheduled Tasks & Triggers](tasks.md) | Background task agents, the `fire_ping()` script trigger, hooking the assistant to any interface or online signal, permissions, and messaging the main chat. |
| [Software Updates](updates.md) | In-app self-update from GitHub: diff review, 3-way merge, dependency diff, backup and revert. |
| [Voice & Text-to-Speech](voice-and-tts.md) | Voice mode and the modular TTS providers. |
| [Skills & Memory](skills-and-memory.md) | Instruction/skill packs and the semantic memory store. |
| [Android Bridge](android-bridge.md) | Talk to the assistant from a phone over your LAN. |

## The big picture

```
You  ─▶  Chat / Voice / Android  ─▶  AI Engine  ─▶  LLM provider (your script)
                                          │
                                          ├─ Tools: the Python interpreter,
                                          │         load/unload skill, name session
                                          │
                                          ▼
                                   Security gate  ─▶  Python interpreter
                                   (scan → policy →      (runs on your machine)
                                    approve/deny → audit)
```

- **Providers** are self-contained scripts under `resources/providers/`. No provider is
  hardcoded; drop a file in and select it in Settings.
- **Tool calling** runs in one of two modes — Native (through the provider's
  function-calling API) or Compatibility (fenced tool calls in the prompt, works
  with any model).
- **The security gate** sits in front of every code run: it statically scans the
  code, applies your per-category policy, optionally asks you to approve, and
  records the decision in an audit log.
- **Tasks** wake the assistant autonomously on any condition you can write in
  Python.
- **Updates** are reviewed diffs pulled from GitHub, with a 3-way merge that
  preserves your local edits and a one-click revert.

Anything that is not covered here is either self-explanatory in the app's
Settings, or lives in the top-level [`README.md`](../README.md).
