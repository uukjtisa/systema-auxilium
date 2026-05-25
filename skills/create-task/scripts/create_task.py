"""
create-task skill — create_task.py
────────────────────────────────────────────────────────────────
Usage:
    python create_task.py '<json_task_dict>'

Appends a new task to tasks.json, injecting a fresh UUID and
created_at. Prints "OK: <name>" on success, "ERROR: <msg>" on fail.
────────────────────────────────────────────────────────────────
"""

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

# ── Resolve app root (two levels above core/, same as task_manager.py) ────────
_SCRIPT_DIR = Path(__file__).resolve().parent          # skills/create-task/scripts/
# skills/ → app root is wherever tasks.json lives; walk up until found
def _find_tasks_json() -> Path:
    candidate = _SCRIPT_DIR
    for _ in range(10):
        target = candidate / "data" / "tasks" / "tasks.json"
        if target.exists():
            return target
        candidate = candidate.parent
    raise FileNotFoundError(
        "Could not locate data/tasks/tasks.json — searched 10 levels up from script dir."
    )


def main():
    if len(sys.argv) < 2:
        print("ERROR: No task JSON provided as argument.")
        sys.exit(1)

    raw = sys.argv[1]
    try:
        task = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON — {e}")
        sys.exit(1)

    if not isinstance(task, dict):
        print("ERROR: Task must be a JSON object.")
        sys.exit(1)

    # ── Inject system fields ───────────────────────────────────────────────────
    task["id"]         = str(uuid.uuid4())
    task["created_at"] = datetime.now().isoformat()

    # ── Load existing tasks ────────────────────────────────────────────────────
    try:
        tasks_path = _find_tasks_json()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    try:
        existing = json.loads(tasks_path.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []

    # ── Append and save ────────────────────────────────────────────────────────
    existing.append(task)
    try:
        tasks_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"ERROR: Could not write tasks.json — {e}")
        sys.exit(1)

    print(f"OK: {task.get('name', '(unnamed)')}")


if __name__ == "__main__":
    main()
