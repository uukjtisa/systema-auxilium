"""
Script Trigger Template  (Scheduled Task -> "Script" trigger type)
================================================================================
A task's Script trigger lets you WAKE THE AI on any condition you can express in
Python. The poller imports this file and calls fire_ping() every N milliseconds
(your Poll Rate). Return True to fire the AI ping now; return False to wait and
poll again.

Because it is plain Python, a trigger can hook the assistant to ANYTHING:
  - online signals  -- poll an HTTP/REST endpoint, a webhook flag, an RSS feed, a
                       message queue, a mailbox, a trading/status API;
  - local signals   -- a sentinel file, a folder change, a serial/GPIO pin, a
                       running process, a window title, a new log line;
  - time            -- fire on a schedule, at a deadline, or after an interval.
When fire_ping() returns True, Systema sends the AI a "ping" (an autonomous turn)
so it can react: read the signal, run tools, message you, or update something.

CONTRACT
--------
    def fire_ping() -> bool
        True  -> fire the AI ping immediately.
        False -> do nothing this cycle; poll again after Poll Rate ms.

IMPORTANT
---------
- This file is re-imported fresh on every poll. It is NOT persistent -- module
  globals do not survive between calls. Keep any state on disk (a file, a small
  JSON, a database), never in a module-level variable.
- YOU must reset the condition after firing, or the ping repeats every cycle.
  (The file-sentinel example below deletes its flag file for exactly this reason.)

================================================================================
LET AN AI WRITE YOUR TRIGGER
================================================================================
Copy this prompt into any AI assistant, fill in the brackets, and paste the
result back into this file:

--------------------------------------------------------------------------------
Write me the body of a Python function for my desktop AI assistant "Systema
Auxilium". The function is:

    def fire_ping() -> bool

The app calls it every [poll rate, e.g. 2000] ms. It must return True when the
assistant should wake up and act, and False otherwise. After returning True it
must reset its condition so it does not fire again on the next call. The file is
re-imported on every call, so DO NOT keep state in globals -- use a file on disk
if you need to remember anything.

I want it to fire when: [describe your signal, e.g. "an HTTP GET to
https://example.com/status returns JSON with {\"alert\": true}", or "a new email
arrives in my inbox", or "the file C:/trigger.flag exists", or "it is 9:00 AM"].

Use only the standard library plus [requests / none]. Wrap any network or I/O in
try/except and return False on error, so a transient failure just skips the
cycle. Keep it self-contained.
--------------------------------------------------------------------------------

================================================================================
"""

from pathlib import Path

# This example fires whenever TRIGGER_FILE appears. Create the file (empty is
# fine) to wake the assistant once. Replace this whole body with your own signal
# -- an HTTP poll, a queue check, a sensor read -- using the prompt above.
TRIGGER_FILE = Path.home() / "systema_trigger.flag"


def fire_ping() -> bool:
    """Fire once each time TRIGGER_FILE appears, then delete it so the ping does
    not repeat. Any error is swallowed so a bad cycle simply skips."""
    try:
        if TRIGGER_FILE.exists():
            TRIGGER_FILE.unlink()      # reset: consume the signal so it fires once
            return True
    except OSError:
        pass
    return False
