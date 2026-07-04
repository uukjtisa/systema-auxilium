# Systema Auxilium — Documentation

Systema Auxilium is a local PyQt6 desktop AI assistant that controls your
computer in plain language: you describe a goal, the assistant writes and runs
the Python needed to reach it, observes the result, and iterates. The reasoning
comes from a provider you configure; everything else runs on your machine.

This folder documents every major subsystem. Start with **Getting Started**, then
read whichever areas you use.

## Contents

| Doc | What it covers |
| --- | --- |
| [Getting Started](getting-started.md) | Install, first run, configure a provider, run the app. |
| [Providers](providers.md) | The modular provider system: the `chat` / `chat_image` / `chat_tools` / `speak` contracts, included scripts, and how to add your own (or have an AI write one). |
| [Tool Calling](tool-calling.md) | Native vs Compatibility modes, the native contract, and `native_adapters` (openai / anthropic / gemini dialects). |
| [Work Mode & Code Execution](work-mode.md) | How the assistant writes and runs Python: work mode, direct execution, the persistent interpreter, `#@FILE` literal blocks, and finishing. |
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
                                          ├─ Tools: work mode, execute code,
                                          │         load/unload skill, name session
                                          │
                                          ▼
                                   Security gate  ─▶  Python interpreter
                                   (scan → policy →      (runs on your machine)
                                    approve/deny → audit)
```

- **Providers** are self-contained scripts under `providers/`. No provider is
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
