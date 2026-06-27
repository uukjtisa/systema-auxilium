---
name: create-task
description: create a new automated task or add a listener in Systema Auxilium
---

## Purpose

Adds a new scheduled AI task to `tasks.json`, or registers a reusable function in `functions.json`. Tasks are always created **inactive** — tell the user to open the Task Manager and activate it (or edit it first if changes are needed).

## Scripts

### `create_task.py`

Appends one task to `tasks.json`. Injects `id` and `created_at` automatically.

**Args:** `arg1` — JSON-encoded task dict (schema below). Never include `id` or `created_at`.
**Output:** `OK: <name>` or `ERROR: <reason>`

### `add_function.py`

Appends one function to `functions.json`. Functions registered here become callable inside any task's `{{...}}` instruction blocks at ping time.

**Args:** `arg1` — JSON-encoded function dict: `{"name": "<fn_name>", "code": "<full def ...>"}`
**Output:** `OK: <name>` or `ERROR: <reason>`

## Templates

`templates/_template.py` — starter for script-trigger ping scripts. Copy it to `{app_root}/data/tasks/interval-scripts/<your_script>.py` and implement `fire_ping()`.

## Task Schema

```json
{
  "name":                       "<string>",
  "active":                     false,
  "instruction":                "<string — supports {{fn()}} blocks>",
  "interval_minutes":           30,
  "ping_mode":                  "startup_relative | schedule_relative",
  "ping_interval_mode":         "timed | specific_times | script_trigger",
  "use_specific_ping_times":    false,
  "specific_ping_times":        [],
  "script_name":                "",
  "script_poll_ms":             1000,
  "daily_schedule":             { "whole_day": true, "start": "00:00", "end": "23:59" },
  "permissions": {
    "allow_workmode":            true,
    "allow_execute_code":        false,
    "inject_image_tools":        false,
    "inject_controller_ref":     false,
    "inject_notify_tool":        false
  },
  "max_work_iterations":        20,
  "unlimited_work_iterations":  false,
  "loaded_skills":              [],
  "limit_session_messages":     { "enabled": false, "max_messages": 5 },
  "one_time_schedule":          { "enabled": false, "datetimes": [] }
}
```

**`active` is always `false` on creation. Tell the user to activate it in the Task Manager.**

## Field Reference

### `instruction`

The prompt the AI receives each ping. Supports `{{...}}` blocks — any expression inside is executed at ping-build time using the PythonInterpreter, with all `functions.json` entries pre-injected into scope.

```
# Call a registered function:
"instruction": "{{reminder_for_sleep()}}"

# Embed dynamic output inline:
"instruction": "System state:\n\n{{current_system_state()}}\n\nSend a summary email."
```

Only reference functions that exist in `functions.json`. If the task needs a new function, register it first with `add_function.py`.

---

### `ping_mode`

| Value | Behaviour |
|---|---|
| `startup_relative` | Waits `interval_minutes` from thread start, then repeats. |
| `schedule_relative` | Aligns pings to `interval_minutes` multiples from `daily_schedule.start` (clock-aligned). |

---

### `ping_interval_mode`

| Value | Required extra fields | Behaviour |
|---|---|---|
| `timed` | — | Fires on a repeating interval per `ping_mode`. |
| `specific_times` | `specific_ping_times` (list of `"HH:MM"`), `use_specific_ping_times: true` | Fires only at the listed clock times each day. |
| `script_trigger` | `script_name` (filename in `interval-scripts/`), `script_poll_ms` | Fires when `fire_ping()` in the named script returns `True`. See **Script Trigger** below. |

---

### `daily_schedule`

```json
{ "whole_day": true,  "start": "00:00", "end": "23:59" }   // runs all day
{ "whole_day": false, "start": "22:00", "end": "00:20" }   // restricted window
```

Thread sleeps outside the window and wakes at `start` automatically.

---

### `permissions`

| Field | When to enable |
|---|---|
| `allow_workmode` | Task needs multi-step tool use (email, code execution, etc.). |
| `allow_execute_code` | Task AI must run Python directly inside the session. |
| `inject_image_tools` | Task needs `take_screenshot` / `attach_image_to_context`. |
| `inject_controller_ref` | Task needs live access to the app controller (advanced). |
| `inject_notify_tool` | Task needs the `notify` shortcut. |

---

### `loaded_skills`

Skill names (kebab-case) injected into the task's system prompt at runtime.
```json
"loaded_skills": ["e-mailman", "notif-system"]
```

---

### `limit_session_messages`

Caps how many history messages are sent to the API per ping. Enable for long-running tasks to prevent context bloat.

---

### `one_time_schedule`

Fire once then auto-deactivate. Provide fallback datetimes sorted ascending — earliest future one fires.
```json
{ "enabled": true, "datetimes": ["2026-06-01T09:00", "2026-06-01T09:30"] }
```
Format: `YYYY-MM-DDTHH:MM`. If all have passed, task deactivates immediately.

---

### Script Trigger

For event-driven tasks. The script lives in `{app_root}/data/tasks/interval-scripts/<name>.py`.

**Contract (from `_template.py`):**
- Must define `fire_ping() -> bool`
- Return `True` once to fire the ping; return `False` to skip this poll cycle
- **Must reset its own trigger condition** — if it keeps returning `True`, the ping fires every poll tick
- **Script is reloaded on every poll — no in-memory state persists.** Save state to disk (e.g. a `.json` file in `Path.home()`)

```python
# Minimal example — fire when a sentinel file appears
from pathlib import Path

def fire_ping() -> bool:
    flag = Path.home() / "my_trigger.flag"
    if flag.exists():
        flag.unlink()   # ← reset so it doesn't re-fire
        return True
    return False
```

See `templates/_template.py` for the full annotated starter.

---

### Adding a Function to `functions.json`

Functions registered here are available as `{{fn_name()}}` in any task's instruction.

```python
fn = {
    "name": "my_function",
    "code": "def my_function():\n    print('hello from my_function')"
}
```

The `code` field is the complete `def` block as a string (use `\n` for newlines). The function has access to `controller` in its scope when executed inside a task ping.

## Example Usage

**Create a task:**
```python
import subprocess, sys, json

task = {
    "name": "Morning Briefing",
    "active": False,
    "instruction": "Send a good morning email to you@example.com with today's date and a motivational message.",
    "interval_minutes": 1440,
    "ping_mode": "schedule_relative",
    "ping_interval_mode": "timed",
    "use_specific_ping_times": False,
    "specific_ping_times": [],
    "script_name": "",
    "script_poll_ms": 1000,
    "daily_schedule": { "whole_day": False, "start": "08:00", "end": "08:15" },
    "permissions": {
        "allow_workmode": True, "allow_execute_code": False,
        "inject_image_tools": False, "inject_controller_ref": False, "inject_notify_tool": False
    },
    "max_work_iterations": 10,
    "unlimited_work_iterations": False,
    "loaded_skills": ["e-mailman"],
    "limit_session_messages": { "enabled": True, "max_messages": 3 },
    "one_time_schedule": { "enabled": False, "datetimes": [] }
}

script = rf"{skills_path}\\create-task\\scripts\\create_task.py"
result = subprocess.run([sys.executable, script, json.dumps(task)], capture_output=True, text=True, encoding="utf-8")
print(result.stdout)
if result.returncode != 0:
    print("SKILL ERROR:", result.stderr)
```

**Register a new function:**
```python
import subprocess, sys, json

fn = {
    "name": "get_weather",
    "code": "def get_weather():\n    import requests\n    r = requests.get('https://wttr.in/?format=3')\n    print(r.text)"
}

script = rf"{skills_path}\\create-task\\scripts\\add_function.py"
result = subprocess.run([sys.executable, script, json.dumps(fn)], capture_output=True, text=True, encoding="utf-8")
print(result.stdout)
if result.returncode != 0:
    print("SKILL ERROR:", result.stderr)
```

## Notes

- **Always create tasks with `active: false`.** After running the script, tell the user: *"The task has been created as inactive. Open the Task Manager to activate it — or edit it first if you'd like to make changes before it runs."*
- `id` and `created_at` are injected by the script. Never include them in the task dict.
- To use a new function in a task's `{{...}}` block, register it with `add_function.py` first, then reference it by name in the instruction.
- For script-trigger tasks, YOU must also create the script file and place it in `{app_root}/data/tasks/interval-scripts/`. Remind the user to verify the path after creation.
- A task AI pushes messages to the main chat window by calling the `send_message_main("...")` function, which is available in its Python namespace — it just calls it from inside `work_environment` (or `execute_code`). This works in both Compatibility and Native tool-calling modes and delivers immediately. (Read-only tasks with no code execution instead emit `{"tool": "send_message_main", "input": "..."}` on its own line, since they can't run a function.)

---

## Connecting External Apps as Chat Interfaces

Any platform that Python can reach can be wired into the task system as a live input channel for the agent. Chat apps, email, webhooks, IoT sensors, database row changes, RSS feeds — if a Python library or HTTP call can check it, a script-trigger task can listen to it and hand the agent whatever arrived.

The setup always has the same three pieces: a **trigger script**, a **reader function**, and a **task**. Each piece has one job.

---

### Piece 1 — The Trigger Script

Lives in `{app_root}/data/tasks/interval-scripts/`. The task manager reloads and re-executes this file from disk on every poll tick, so **no in-memory state survives between calls** — two busy-guards in the engine (`_script_busy`, `_ping_busy`) ensure only one script call and one AI ping run at a time.

`fire_ping()` has one job: check whether something new has arrived on the external platform. If yes, save whatever arrived to a state file on disk and return `True` — once. If no, return `False`. The state file is how the payload crosses from the trigger script into the reader function.

```python
# Generic shape — works for any platform
from pathlib import Path
import json

STATE_FILE = Path.home() / "my_platform_pending.json"

def fire_ping() -> bool:
    payload = _check_platform_for_new_event()   # your polling logic here
    if payload:
        STATE_FILE.write_text(json.dumps(payload), encoding="utf-8")
        return True      # fires the AI ping exactly once
    return False

def _check_platform_for_new_event():
    # Return a dict if something new is waiting, None/empty otherwise.
    # Examples of what goes here:
    #   - IMAP UNSEEN query for email
    #   - REST API call to a chat platform (Slack, Telegram, Discord, etc.)
    #   - Read a webhook queue file written by a separate listener process
    #   - Check an MQTT retained topic
    #   - Query a database for new rows since last checked
    #   - Poll an RSS feed for new entries
    #   - Check a shared folder for a new file
    return None
```

**Long-lived connections (bots, websockets):** Because the script has no persistent memory, you can't hold an open connection inside it. Instead, run a separate lightweight listener process (a bot, a webhook server, a socket reader) alongside the app. That process writes a state file when an event arrives. `fire_ping()` just checks for the file and deletes it — the connection management stays completely outside the script.

```python
# fire_ping() when a separate listener process is doing the heavy lifting
def fire_ping() -> bool:
    if STATE_FILE.exists():
        STATE_FILE.unlink()   # consume — prevents re-firing every tick
        return True
    return False
```

---

### Piece 2 — The Reader Function

Registered once in `functions.json` via `add_function.py`. When the task instruction contains `{{my_reader()}}`, the task manager executes it through `_resolve_instruction` at ping-build time — before the AI ever sees the message. Whatever the function prints gets injected inline into the instruction text.

```python
# Generic reader — reads the state file and prints the payload for injection
def my_platform_reader():
    import json
    from pathlib import Path
    f = Path.home() / "my_platform_pending.json"
    if not f.exists():
        print("(no event data)")
        return
    data = json.loads(f.read_text(encoding="utf-8"))
    # Print whatever context the AI needs to act on:
    print(f"From: {data.get('sender')}")
    print(f"Message: {data.get('body')}")
    print(f"Channel: {data.get('channel')}")
```

> If `fire_ping()` deletes the state file before the reader runs, write a second "last seen" copy (`my_platform_last.json`) inside the listener and have the reader target that instead. The trigger file and the reader file can be different paths.

---

### Piece 3 — The Task

Set `ping_interval_mode` to `script_trigger` and point `script_name` at your trigger file. The instruction is a simple conditional template — the `{{...}}` block gets replaced with the reader's output before the AI sees it.

```python
task = {
    "name": "Platform Listener",
    "active": False,
    "ping_mode":          "startup_relative",
    "ping_interval_mode": "script_trigger",
    "script_name":        "my_platform_trigger.py",   # your trigger file
    "script_poll_ms":     2000,                        # how often to poll
    "instruction": (
        "A new message arrived from the platform:\n\n"
        "{{my_platform_reader()}}\n\n"
        "Take appropriate action and reply."
    ),
    "permissions": {
        "allow_workmode":     True,
        "allow_execute_code": True,   # enable if the AI needs to run reply code itself
        # ... other permissions as needed
    },
    "limit_session_messages": { "enabled": True, "max_messages": 4 },
    # ... rest of schema
}
```

For the reply, the AI either uses a loaded skill (`loaded_skills`) or generates and executes the reply code inline via `execute_code` — no separate skill file is required if the platform's Python library is simple enough to use in one block.

---

### What "As Long As Python Can Reach It" Means

This pattern is not platform-specific. Anything that has a Python interface, a REST API, or produces a file becomes a viable input channel:

| Platform type | How `fire_ping` checks it |
|---|---|
| Chat app with a bot API | REST poll or bot event → write state file |
| Email (IMAP) | `imaplib` UNSEEN query |
| Webhook receiver | Separate Flask/FastAPI process writes a queue file; script checks it |
| MQTT / IoT sensor | `paho-mqtt` subscribe to a topic, check retained value |
| Database | SQL query for new rows since a stored `last_seen_id` |
| RSS / Atom feed | `feedparser`, compare latest entry ID to saved one |
| File drop folder | `Path.glob()` for new files since last check |
| SMS / phone (Twilio) | Twilio API poll for unread messages |
| OS clipboard or hotkey | `pyperclip` / `keyboard` library check |
| Any HTTP endpoint | `requests.get()` and inspect the response |

The agent on the other end is the same full AI session either way — work mode, tools, skills, `send_message_main` — just triggered by a different source. The script is the only part that changes between platforms.

---

### Checklist

1. Write the trigger script → save to `{app_root}/data/tasks/interval-scripts/`.
2. If using a long-lived connection, write a separate listener process; have `fire_ping()` only check the state file it writes.
3. Register the reader function → run `add_function.py` once.
4. Create the task → `create_task.py`, set `ping_interval_mode: "script_trigger"`, point `script_name` at your file.
5. Activate in Task Manager when ready.