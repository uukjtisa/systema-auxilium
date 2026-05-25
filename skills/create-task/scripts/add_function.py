"""
create-task skill — add_function.py
────────────────────────────────────────────────────────────────
Usage:
    python add_function.py '<json_function_dict>'

Appends a new function entry to functions.json (does NOT replace
existing entries). Prints "OK: <name>" on success, "ERROR: <msg>"
on fail.

Expected input shape:
    {
        "name": "my_function",
        "code": "def my_function():\n    print('hello')"
    }
────────────────────────────────────────────────────────────────
"""

import json
import sys
from pathlib import Path

# ── Resolve functions.json (walk up from script dir) ──────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent   # skills/create-task/scripts/

def _find_functions_json() -> Path:
    candidate = _SCRIPT_DIR
    for _ in range(10):
        target = candidate / "data" / "tasks" / "functions.json"
        if target.exists():
            return target
        candidate = candidate.parent
    raise FileNotFoundError(
        "Could not locate data/tasks/functions.json — searched 10 levels up from script dir."
    )


def main():
    if len(sys.argv) < 2:
        print("ERROR: No function JSON provided as argument.")
        sys.exit(1)

    raw = sys.argv[1]
    try:
        fn = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON — {e}")
        sys.exit(1)

    if not isinstance(fn, dict):
        print("ERROR: Input must be a JSON object.")
        sys.exit(1)

    # ── Validate required fields ───────────────────────────────────────────────
    name = fn.get("name", "").strip()
    code = fn.get("code", "").strip()

    if not name:
        print("ERROR: 'name' field is missing or empty.")
        sys.exit(1)

    if not code:
        print("ERROR: 'code' field is missing or empty.")
        sys.exit(1)

    if not code.startswith("def "):
        print("ERROR: 'code' must begin with a 'def ' statement.")
        sys.exit(1)

    # ── Locate functions.json ──────────────────────────────────────────────────
    try:
        functions_path = _find_functions_json()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # ── Load existing functions ────────────────────────────────────────────────
    try:
        existing = json.loads(functions_path.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []

    # ── Duplicate name check ───────────────────────────────────────────────────
    if any(entry.get("name") == name for entry in existing):
        print(f"ERROR: A function named '{name}' already exists in functions.json. "
              "Remove or rename the existing entry first.")
        sys.exit(1)

    # ── Append and save ────────────────────────────────────────────────────────
    existing.append({"name": name, "code": code})

    try:
        functions_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"ERROR: Could not write functions.json — {e}")
        sys.exit(1)

    print(f"OK: {name}")


if __name__ == "__main__":
    main()
