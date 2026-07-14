---
name: create-skill
description: create a new skill for systema auxilium.
---

## Purpose

Creates a properly structured skill directory under `{app_root}/skills/<skill-name>/`, including a compliant `SKILL.md` and any requested script stubs. Use this whenever a new capability needs to be packaged as a reusable skill.

## Skill Structure

```
skills/
└── <skill-name>/              ← required; must match the "name" in the SKILL.md frontmatter
    ├── SKILL.md               ← required; the instructions YOU read
    └── scripts/               ← optional; one script per distinct action
        └── <script>.py
```

Most skills need only `SKILL.md` (plus `scripts/` if they run code). A `templates/`
folder is **rarely needed** — add one only when your scripts read from bundled static
files (a canned email body, a config stub). Never create an empty `templates/`.

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
| `## Dependencies` | `pip install ...` block. Only if external packages are needed. |
| `## Templates` | *Optional — rare.* Only if the skill ships static files in `templates/`: what they are, how scripts read them, and their path rules. |
| `## Output` | What the script returns/prints/writes. |
| `## Example Usage` | Subprocess call. See path rule below. Must obey path rule in the examples. |
| `## Notes` | Edge cases, warnings, how to handle error cases, gotchas. |

**Writing rules:**
- Refer to the agent as **YOU** or **THE AGENT** — never by name.
- Be precise, concise, and complete. Cut filler. Leave nothing ambiguous.
- Short description in frontmatter must fit on one line; no period.
- Include only sections that apply — omit the rest; never leave a placeholder.
- Every `## Example Usage` must obey the Path Rule below (absolute path via `skills_path`, `sys.executable`).

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

After scaffolding, author the files with YOUR native file tools — not by hand-piping through the interpreter:

- **`write_file`** — create `SKILL.md` and each `scripts/*.py` with its full contents.
- **`edit_file`** — make surgical follow-up changes to any file you already wrote.
- **`read_file`** — re-read a file to confirm what's there before editing.
- **`grep`** — search existing `skills/` for a similar skill to model yours on (before creating a duplicate).

(You may skip `scaffold.py` entirely and just `write_file` each path directly — the script is only a convenience for stamping out the empty directory layout.)

## A Complete Minimal Skill

This is what a finished, valid skill looks like end to end — copy this shape. It
takes one argument and prints a greeting. Nothing more is required.

`skills/say-hello/SKILL.md` (the leading spaces are just to show the file literally;
the real file starts at column 0):

    ---
    name: say-hello
    description: greet a person by name from the python interpreter
    ---

    ## Purpose

    Prints a friendly greeting for a given name. Use when YOU are asked to greet someone.

    ## Scripts

    ### `greet.py`
    **Args:** `arg1` — the name to greet.
    **Output:** the greeting line on stdout.

    ## Example Usage

    ```python
    import subprocess, sys
    script = rf"{skills_path}\say-hello\scripts\greet.py"
    result = subprocess.run([sys.executable, script, "Ada"],
                            capture_output=True, text=True, encoding="utf-8")
    print(result.stdout)
    if result.returncode != 0:
        print("SKILL ERROR:", result.stderr)
    ```

`skills/say-hello/scripts/greet.py`:

```python
import sys

def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "there"
    print(f"Hello, {name}!")

if __name__ == "__main__":
    main()
```

That is the whole skill: frontmatter + `## Purpose` + one documented script + an
`## Example Usage` that obeys the path rule. No `templates/`, no empty sections.

## Notes

- Skill names must be kebab-case.
- A `SKILL.md` with only frontmatter and no body is invalid — always include at least `## Purpose`.
- If the skill has no scripts, omit the `scripts/` directory entirely.
- Never add placeholder sections. Only include sections that have real content.
