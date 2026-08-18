"""The dead-code baseline is enforced, not just documented.

Idea "codebase_cleanup_dead_code" item 4: `scripts/deadcode.py --strict` was
written to hold pyflakes at its baseline (326 findings -> 2 deliberate ones on
2026-08-02) but nothing ever ran it, so nothing stopped a regression. The
working copy is not a git repo, so there is no pre-commit hook to hang it on;
the suite is the one thing that always runs, here and in CI.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deadcode.py"


def test_no_new_pyflakes_findings():
    pytest.importorskip("pyflakes", reason="deadcode check needs pyflakes")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--strict"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    assert proc.returncode == 0, (
        "scripts/deadcode.py --strict found pyflakes entries beyond the "
        "deliberate baseline (PYFLAKES_ALLOWED). Fix them or, if the new entry "
        f"is genuinely deliberate, add it there with the reason.\n\n{proc.stdout}"
    )
