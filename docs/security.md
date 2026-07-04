# Security & Guarded Execution

Systema Auxilium runs AI-written Python on your machine, so it ships with a
layered safety gate modeled on how mature agent harnesses gate tool use:

```
static scan  →  policy (allow / ask / deny)  →  session allow-list  →  approval dialog  →  audit
```

Everything is configured under **Settings → Security**.

## Supervised Execution (the master switch)

**Supervised Execution** is the top-level control.

- **ON (default, recommended):** the policy applies. Risky operations are caught
  and — depending on your rules — auto-approved, prompted, or blocked.
- **OFF:** a true kill-switch. The assistant runs everything automatically with
  **no prompt**, and the policy is not applied at all — not even a `deny` category
  blocks. Only turn this off if you fully trust the model and the task.

The per-category policy and the "review even safe code" option below only take
effect while Supervised Execution is ON.

## The execution policy (per-category rules)

Every operation the scanner recognizes is sorted into a **category**, and each
category has a rule: **allow** (run without a prompt), **ask** (prompt for
approval), or **deny** (always block). `ask` and `deny` are honored whenever
Supervised Execution is ON.

Categories:

| Group | Category | Covers |
| --- | --- | --- |
| File operations | `file_create` | Creating new files/dirs/links: `mkdir`, `makedirs`, `touch`, `symlink`, `open(..., 'x')`, … |
| | `file_edit` | Writing to a path that **already exists**. |
| | `file_write` | An ambiguous content write whose target could not be resolved (create-or-edit). |
| | `file_move` | `rename`, `replace`, `shutil.move`. |
| | `file_copy` | `copy`, `copy2`, `copytree`. |
| | `file_delete` | `remove`, `unlink`, `rmdir`, `rmtree`, `truncate`. |
| Code & processes | `process` | `subprocess`, `os.system`, shell/exec/spawn. |
| | `dynamic` | `eval`, `exec`, `compile`, `__import__`, unsafe deserialization (`pickle`/`yaml.load`/…). |
| System & network | `system` | OS internals: registry, `ctypes`, env mutation, `kill`, permissions/ownership, privilege changes. |
| | `network` | HTTP, sockets, urllib, smtp/ftp, etc. |
| Credentials | `secrets` | Literal API keys / tokens detected in the code. |

### Create vs edit vs write

Content writes (`open('w'/'a')`, `write_text/bytes`, the `write_file()` helper)
are ambiguous at scan time. The gate resolves the target path where it can — from
a string literal, or from a variable already bound in the live interpreter — and
relabels the finding `file_edit` (path exists) or `file_create` (new path). If the
path cannot be resolved, it stays the conservative `file_write`. This lets you,
for example, allow edits to existing files while still being asked before new
files are created.

> Back-compat: a policy saved before the split (which only had `file_write`) makes
> `file_create` and `file_edit` inherit whatever you set for `file_write`, until
> you tune them.

## Presets

Pick a preset as a starting point under **Settings → Security → Execution Policy**,
then fine-tune individual rows. Built-ins:

- **Strict** — everything risky prompts (`ask`). The safest interactive default.
- **Balanced** — routine file writes/creates/edits/copies run; moves, deletes,
  processes, network, dynamic code and system calls still prompt.
- **Trusting** — all local file operations and network I/O auto-run; process
  spawns, deletes, dynamic code and OS-internal calls still prompt.
- **Paranoid** — everything prompts, and the two highest-risk classes (literal
  credentials, dynamically-built code) are hard-**denied**.

You can also save your own named presets.

## The code-approval dialog

When a run needs approval, the dialog shows the exact Python (editable), a live
risk scan that re-runs as you edit, and a built-in **Code Reviewer** sub-agent
that can explain the code or propose a safer version as a one-click diff.

Approving offers two "don't ask again" controls, both acting on the operation
**categories** in the code (no code is fingerprinted or stored):

- **Don't ask again this session** — adds those categories to an ephemeral
  session allow-list. Matching runs auto-approve until you restart. Clear it any
  time with *Settings → Security → Clear this session's allow-list*.
- **Always allow these operations** — promotes those categories from `ask` to
  `allow` in your saved policy (it never overrides a `deny`). If a Settings
  window is open, its policy rows update live.

Rejecting has an optional **reason box**: leave it empty for a normal reject, or
type a reason and it is passed back to the assistant as `REASON: <text>` so it
knows why and can adjust.

## Review even safe code

By default, code with no risky operations (plain `print`, math, a bare `import`)
runs without a prompt even under Supervised Execution — this keeps trivial code
from nagging. Tick **Review even safe code** to be prompted for *every* execution,
no matter how trivial. (Only meaningful while Supervised Execution is ON.)

## Audit log

Every gated run is recorded to an append-only audit log: what ran, how it was
decided (`user` / `policy` / `session-allow` / `safe` / `unsupervised`), and the
risk summary. View it, refresh it, or clear it under **Settings → Security →
Execution Policy & Audit**.

## Secret redaction

Before any code text leaves your machine (for example, into the Code Reviewer
sub-agent's prompt), API keys, tokens and emails are redacted.

## Scheduled tasks

A [scheduled task](tasks.md) can be granted permission to bypass the prompt and
execute immediately within the task, so its work runs unattended. Be deliberate
about each task's instructions and permissions.
