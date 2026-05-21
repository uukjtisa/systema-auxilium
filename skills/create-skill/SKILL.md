---
name: create-skill
description: scaffold a new skill into {app_root}/skills/
---

## Purpose

Creates a properly structured skill directory under `{app_root}/skills/<skill-name>/`, including a compliant `SKILL.md` and any requested script stubs. Use this whenever a new capability needs to be packaged as a reusable skill.

## Skill Structure

```
skills/
└── <skill-name>/              ← required; Must be the same as the "name" in the SKILL.md
    ├── SKILL.md               ← required; instructions for YOU
    ├── scripts/
    │   └── <script>.py        ← one script per distinct action
    └── templates/             ← optional; static files scripts read from
```

## SKILL.md Format

Every `SKILL.md` must begin with this frontmatter block — no exceptions:

```
---
name: <skill-name>
description: <one short phrase describing when to use. — no period>
---
```

Then the body. Required sections (include only those that apply):

| Section | Content |
|---|---|
| `## Purpose` | 1–3 sentences. What this skill does and why it exists. |
| `## Scripts` | One subsection per script: filename, args, what it does, and path rules. |
| `## Templates` | What files live in `templates/` and how scripts use them, and path rules. |
| `## Dependencies` | `pip install ...` block. Only if external packages are needed. |
| `## Output` | What the script returns/prints/writes. |
| `## Example Usage` | Subprocess call. See path rule below. Must obey path rule in the examples. |
| `## Notes` | Edge cases, warnings, how to handle error cases, gotchas. |

**Writing rules:**
- Refer to the agent as **YOU** or **THE AGENT** — never by name.
- Be precise, concise, and complete. Cut filler. Leave nothing ambiguous.
- Short description in frontmatter must fit on one line; no period.

## Path Rule (NON-NEGOTIABLE)

All example calls inside any `SKILL.md` must use the **full absolute path** via `skills_path`. Never write relative paths.

```python
import subprocess, sys

# skills_path is resolved in global config — never hardcode it
script = rf"{skills_path}\\<skill-name>\\scripts\\<script>.py"

result = subprocess.run(
    [sys.executable, script, "arg1", "arg2"],
    capture_output=True, text=True, encoding="utf-8"
)
print(result.stdout)
if result.returncode != 0:
    print("SKILL ERROR:", result.stderr)
```

Always use `sys.executable` — never `"python"` — to guarantee the correct environment.

## When to use

- YOU are asked to create, add, or define a new skill for the agent.
- YOU identify a repeated task that should be packaged as a reusable skill.

## When NOT to use

- The task is a one-off; it does not need to be reused.
- A skill for this capability already exists — extend it instead.

## Example Usage

Run the scaffold script to create the directory layout and blank files:

```python
import subprocess, sys

script = rf"{skills_path}\\create-skill\\scripts\\scaffold.py"

result = subprocess.run(
    [sys.executable, script, "<skill-name>"],
    capture_output=True, text=True, encoding="utf-8"
)
print(result.stdout)
if result.returncode != 0:
    print("SKILL ERROR:", result.stderr)
```

After scaffolding, YOU write the `SKILL.md` body and any script stubs manually following the format above.

## Notes

- Skill names must be kebab-case.
- A `SKILL.md` with only frontmatter and no body is invalid — always include at least `## Purpose`.
- If the skill has no scripts, omit the `scripts/` directory entirely.
- Never add placeholder sections. Only include sections that have real content.
