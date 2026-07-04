# Work Mode & Code Execution

Systema Auxilium accomplishes tasks by writing and running Python on your machine.
There are two execution tools, plus a persistent interpreter that ties a task
together.

Both tools pass through the [security gate](security.md) before anything runs.

## `execute_code` — one-shot actions

A single block of Python for a quick, self-contained action (open an app, show a
popup, do a calculation). It runs once; output is not fed back into a reasoning
loop.

## `work_environment` — the agentic loop

**Work mode** is how the assistant does real, multi-step tasks. It enters work
mode, runs a step of Python, sees the output, reasons about it, runs the next
step, and repeats — an observe/act loop over the same live interpreter.

- Each step's `stdout` (and errors) come back to the model as the observation for
  the next step.
- Variables, imports, and objects persist across steps (see the interpreter
  below), so step 2 can use what step 1 defined.

### Finishing work mode

Work mode ends the moment the assistant replies **without** a work/execute tool
call. That final plain reply **is** the report shown to you. There is no special
"exit" keyword — a normal reply with no tool call is the signal that the task is
complete.

## The persistent interpreter

Work mode runs on a single, persistent Python interpreter whose namespace lives
across steps within a task. You can reset it (clearing all state) from the app's
menu / **Reset Python interpreter**.

Always-available helpers are injected into the namespace, including:

- `write_file(path, content, ...)` — write literal data to a file, creating parent
  directories as needed. Pairs with `#@FILE` blocks (below).

## `#@FILE` literal blocks

Writing file content as a normal Python string is fragile — backslashes, quotes
and triple-quotes can break the parse. Instead, the assistant can supply file
content in a literal block that is bound to a variable **without** passing through
the Python parser:

```
#@FILE my_data
...arbitrary file content, exactly as-is...
#@ENDFILE
```

The block content is injected into the interpreter namespace as the variable
`my_data`, which the code can then pass to `write_file(...)`. This keeps source
text (code, JSON, quotes) intact.

## Where generated files go

Ad-hoc generated artifacts land in a `.generated/` folder next to the app's
working directory unless the code writes elsewhere explicitly.

## Relationship to tool-calling mode

Whether these tools are invoked natively or via fenced blocks depends on your
[Tool Calling mode](tool-calling.md). The behavior of work mode itself — the
observe/act loop and the "reply with no tool call to finish" rule — is the same in
both modes.
