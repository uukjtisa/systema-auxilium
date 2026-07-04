# Scheduled Tasks & Triggers

A **scheduled task** is a background agent that wakes the assistant on a condition
you choose and hands it an instruction to carry out — unattended. Tasks turn
Systema Auxilium from something you talk to into something that can *react* to the
world.

Each task has:

- an **instruction** (what the assistant should do when it wakes),
- a **trigger** (when it wakes — an interval, or a custom Python **Script**
  trigger),
- its own **permissions** (including whether code runs without a prompt),
- optional **pre-loaded skills**, and
- its own working context.

A task agent can reach your main chat by calling `send_message_main()` from its
Python namespace, so it can report back or notify you.

> Because a task can be granted permission to execute code immediately (no
> approval prompt), be deliberate about each task's instruction and permissions.
> See [Security](security.md).

## Trigger types

- **Interval** — fire every N units of time.
- **Script** — you write a Python function that decides when to fire. This is the
  powerful one: it can hook the assistant to *anything*.

## The Script trigger: `fire_ping()`

A Script trigger is a Python file that defines:

```python
def fire_ping() -> bool:
    ...
```

The poller imports the file and calls `fire_ping()` every **Poll Rate**
milliseconds:

- return **`True`** → fire the AI ping now (an autonomous turn on the task's
  instruction);
- return **`False`** → do nothing this cycle; poll again after the Poll Rate.

### Two rules that matter

1. **It is not persistent.** The file is re-imported fresh on every poll, so
   module-level globals do **not** survive between calls. Keep any state you need
   on disk (a small file, JSON, or a database).
2. **You must reset the condition after firing**, or the ping repeats every cycle.
   The template's file-sentinel example deletes its flag file for exactly this
   reason.

### Hook the assistant to any interface or signal

Because `fire_ping()` is plain Python, a trigger can connect the assistant to:

- **online signals** — poll an HTTP/REST endpoint, a webhook flag, an RSS feed, a
  message queue, a mailbox, a trading or status API;
- **local signals** — a sentinel file, a folder change, a serial/GPIO pin, a
  running process, a window title, a new log line;
- **time** — a schedule, a deadline, an interval.

When the condition is met and `fire_ping()` returns `True`, the assistant wakes,
reads the signal, runs tools, and acts on your instruction — for example: "when
the build endpoint reports a failure, read the log and summarize the cause in my
chat."

### Example (from the template)

```python
from pathlib import Path

TRIGGER_FILE = Path.home() / "systema_trigger.flag"

def fire_ping() -> bool:
    try:
        if TRIGGER_FILE.exists():
            TRIGGER_FILE.unlink()   # reset: consume the signal so it fires once
            return True
    except OSError:
        pass
    return False
```

Create `systema_trigger.flag` in your home folder and the assistant fires once.
Swap the body for your own signal.

### Let an AI write your trigger

The trigger template (`data/tasks/interval-scripts/_template.py`) contains a
fill-in-the-brackets prompt. Paste it into any AI, describe your signal, and drop
the result in.

> Wrap network or I/O in `try/except` and return `False` on error, so a transient
> failure just skips the cycle instead of crashing the poll.
