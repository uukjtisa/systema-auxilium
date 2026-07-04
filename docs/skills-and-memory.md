# Skills & Memory

## Skills

**Skills** are focused instruction packs that give the assistant specialized
knowledge and helper scripts for a specific kind of job. Each skill lives in its
own folder under `skills/` and can be loaded and unloaded on demand — loading a
skill folds its instructions (and any helper files it ships) into the assistant's
context; unloading removes them.

- The assistant can load/unload skills itself as a tool (see
  [Tool Calling](tool-calling.md)), so it can pull in the right expertise for a
  task and drop it afterward to keep its context lean.
- A [scheduled task](tasks.md) can pre-load specific skills so its agent starts
  with the right knowledge every time it fires.
- `skills/` is treated as **user-owned** by the updater: your skill files are
  protected from being silently overwritten during an update (see
  [Updates](updates.md)).

Examples that ship with the app include a self-knowledge skill (lets the
assistant explain what it is and check its own status/updates) and a create-task
skill (its trigger-script template lives at
`data/tasks/interval-scripts/_template.py`).

To add a skill, create a folder under `skills/` with its instructions and any
helper scripts, then let the assistant load it.

## Memory

The assistant has a **semantic memory store** so useful facts persist across
conversations. Memories are embedded and retrieved by meaning (not just keyword
match), so relevant context surfaces when it applies to what you are doing.

- Memory lives under the app's `data/` folder and, like the rest of `data/`, is
  **never touched by updates**.
- Because it is under `data/`, your memories, sessions, and tasks stay entirely
  local to your machine.

Both skills and memory are managed from within the app; you do not edit engine
code to use them.
