"""
create-skill · scaffold.py
Creates the directory layout and blank SKILL.md for a new skill.

USAGE — always call via subprocess with full path:
    import subprocess, sys
    script = rf"{SKILLS_DIR}\\create-skill\\scripts\\scaffold.py"
    subprocess.run([sys.executable, script, "<skill-name>"], ...)
"""
import sys
import os
from pathlib import Path

# ── CLI args ───────────────────────────────────────────────────────────────
if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} <skill-name>")
    sys.exit(1)

skill_name = sys.argv[1].strip().lower().replace(" ", "-")

# ── Resolve skills dir from this script's location ────────────────────────
# scripts/ -> skill-dir/ -> skills/ -> app_root/
SKILLS_DIR = Path(__file__).resolve().parent.parent.parent

skill_dir  = SKILLS_DIR / skill_name
scripts_dir = skill_dir / "scripts"
skill_md   = skill_dir / "SKILL.md"

TEMPLATE = f"""---
name: {skill_name}
description: 
---

## Purpose



## When to use



## When NOT to use



## Example Usage

```python
import subprocess, sys

script = rf"{{SKILLS_DIR}}\\\\{skill_name}\\\\scripts\\\\script.py"

result = subprocess.run(
    [sys.executable, script, "arg1"],
    capture_output=True, text=True, encoding="utf-8"
)
print(result.stdout)
if result.returncode != 0:
    print("SKILL ERROR:", result.stderr)
```
"""

# ── Create structure ───────────────────────────────────────────────────────
if skill_dir.exists():
    print(f"ERROR: skill already exists at {skill_dir}")
    sys.exit(1)

scripts_dir.mkdir(parents=True)
skill_md.write_text(TEMPLATE, encoding="utf-8")

print(f"Created: {skill_dir}")
print(f"  {skill_md.relative_to(SKILLS_DIR)}")
print(f"  {(scripts_dir).relative_to(SKILLS_DIR)}/")
print(f"\nNext: fill in {skill_name}/SKILL.md and add scripts to {skill_name}/scripts/")
