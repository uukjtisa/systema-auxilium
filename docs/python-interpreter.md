# The Python Interpreter & Code Execution

Systema Auxilium accomplishes tasks by writing and running Python on your machine.
The primary tool is `python_interpreter` — a persistent interpreter that ties a
task together. Quick one-shot actions (open an app, show a popup) run through the
same tool; the assistant launches blocking programs detached so the step returns
immediately. Alongside it is a **file subsystem** — `read_file`, `edit_file`,
`write_file` — for surgical file work.

All code passes through the [security gate](security.md) before anything runs.

## `python_interpreter` — the agentic loop

**Work mode** is how the assistant does real, multi-step tasks. It runs a
step of Python, sees the output, reasons about it, runs the next step, and
repeats — an observe/act loop over the same live interpreter. Work mode is a
STATE, not a single tool: `python_interpreter`, the file subsystem below, and
loading/unloading a skill all drive the same loop.

- Each step's `stdout` (and errors) come back to the model as the observation for
  the next step.
- Variables, imports, and objects persist across steps, so step 2 can use what
  step 1 defined.

### Finishing

Work mode ends the moment the assistant replies **without** a `python_interpreter`
(or file-subsystem, or skill) call. That final plain reply **is** the report shown
to you. There is no special "exit" keyword.

## The file subsystem — read_file / edit_file / write_file

These are work-mode steps (calling one never ends the loop):

- `read_file(path, start_line, max_lines)` — a numbered window into a file.
- `edit_file(path, old_text, new_text)` — a surgical anchor edit: `old_text` must
  match the file exactly and uniquely (a line-range fallback also exists).
- `write_file(path, content)` — create or fully rewrite a file.

Edits and writes pass through the same approval gate as code, shown as a per-hunk
diff, and every change is recorded to the file-history journal so it can be
undone (Settings ▸ System ▸ View file changes).

Loading or unloading a skill mid-task is also a work-mode step — the assistant
can pull in task-specific instructions without ever leaving the loop.

## The persistent interpreter

Work mode runs `python_interpreter` on a single, persistent Python interpreter
whose namespace lives across steps within a task. You can reset it (clearing all
state) from the app's menu / **Reset Python interpreter**.

Always-available helpers are injected into the namespace, including
`write_file(path, content, ...)` — write literal data to a file (pairs with
`#@FILE` blocks below). Note this is the in-code helper; standalone file work
uses the `write_file` TOOL above.

## `#@FILE` literal blocks

Writing file content as a normal Python string is fragile — backslashes, quotes
and triple-quotes can break the parse. Instead, supply file content in a literal
block bound to a variable **without** passing through the Python parser:

```
#@FILE my_data
...arbitrary file content, exactly as-is...
#@ENDFILE
```

The block content is injected into the interpreter namespace as the variable
`my_data`, which the code passes to `write_file(...)`.

## Where generated files go

Ad-hoc generated artifacts land in a `.generated/` folder next to the app's
working directory unless the code writes elsewhere explicitly.

## Relationship to tool-calling mode

Whether these tools are invoked natively or via fenced blocks depends on your
[Tool Calling mode](tool-calling.md). The behavior of work mode itself —
the observe/act loop and the "reply with no tool call to finish" rule — is the
same in both modes.
