"""
scripts/deadcode.py — the repeatable dead-code check.

    python scripts/deadcode.py            # pyflakes (authoritative) + vulture triage
    python scripts/deadcode.py --strict   # exit 1 if pyflakes finds anything new

TWO LAYERS, and they are NOT equally trustworthy:

  * pyflakes  — authoritative. Unused imports, dead locals, placeholder-less
                f-strings. Full scope analysis, effectively no false positives.
                The tree is kept AT the baseline below; anything above it is a
                regression to fix, not to whitelist.

  * vulture   — advisory ONLY. It cannot see the three things this app leans on
                hardest, so its raw output is mostly false positives:
                  - Qt virtual overrides (paintEvent, highlightBlock, ...) are
                    called by the C++ event loop, never by name;
                  - string dispatch (ToolManager._CARD_DISPATCH maps a card_type
                    to a METHOD NAME; execution/capabilities.py and
                    tool_registry.py bind by name too);
                  - signals/slots, zoom_restyle closures, and the names injected
                    into the python_interpreter namespace.
                NEVER delete something because vulture listed it. Verify the
                call site by hand first — a wrongly deleted Qt slot fails only
                at runtime, in a path the suite does not cover.

Baseline (2026-08-02): pyflakes reports exactly the two entries in
PYFLAKES_ALLOWED below, both deliberate. It was 326 before that pass.

ENFORCED since 2026-08-18 by `tests/test_deadcode_baseline.py`, which runs
`--strict` as part of the suite. The working copy is not a git repo, so there
is no pre-commit hook to hang this on; the suite is the thing that always runs.

REMAINING CLEANUP — opportunistic, do it while you are already in the module,
NOT as its own sweep (this is the demoted remainder of the "codebase cleanup"
backlog item, retired 2026-08-18):

  1. Vulture triage. 76 candidates survive the filters below; all are 60%
     confidence and spot-checking has already turned up LIVE code among them.
     Verify the call site by hand before deleting anything. The five most
     plausible, because they are self-contained and their callers may have
     moved:
       engine/native_adapters.py  — to_dialect_tools, parse_response,
                                    result_messages
       updater/hunks.py           — build_hunks, apply_decisions (the Manage
                                    dialog was retired 2026-07-20 and conflict
                                    resolution moved inline into update_window)
       common/image_refs.py       — detached_refs, attached_marker
       common/relauncher.py       — process_alive, kill_and_relaunch
       ui/theme.py                — scrollbar_qss
  2. Commented-out code: ~23 lines tree-wide (`# self.foo(...)`, `# def ...`).
     None should survive unless labelled with why it is kept as reference.
  3. Orphaned modules: no import-graph pass has ever run, so "modules nobody
     imports" is unmeasured. pydeps or a small ast importer scan settles it.
     Re-derive the module list from the real tree — the original issue's list
     (app/core/, app/tools/, app/hooks/) describes a layout this project has
     never had.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ["systema", "main.py", "tests", "resources"]

# Deliberate, verified, and NOT to be "cleaned up":
PYFLAKES_ALLOWED = {
    # Re-exported for other modules to import from chat_window.
    ("systema/ui/chat_window.py", "InlineStatus"),
    # Sole statement of an elif branch: removing the assignment would move the
    # branch's control flow, which is a real change for a cosmetic finding.
    ("systema/net/android_bridge.py", "in_work_step"),
}

# Called by the Qt event loop, never by name from Python.
QT_OVERRIDES = {
    "paintEvent", "showEvent", "hideEvent", "closeEvent", "resizeEvent",
    "moveEvent", "eventFilter", "mousePressEvent", "mouseReleaseEvent",
    "mouseMoveEvent", "mouseDoubleClickEvent", "wheelEvent", "keyPressEvent",
    "keyReleaseEvent", "enterEvent", "leaveEvent", "focusInEvent",
    "focusOutEvent", "dragEnterEvent", "dragMoveEvent", "dropEvent",
    "contextMenuEvent", "changeEvent", "sizeHint", "minimumSizeHint", "event",
    "run", "timerEvent", "nativeEvent", "highlightBlock", "data", "rowCount",
    "columnCount", "headerData", "flags", "setData", "parent", "index",
}

# Parameters a third-party callback signature forces on us.
FORCED_PARAMS = {"kwargs", "time_info", "status", "frame_count", "in_data"}


def _run(mod: str, *args: str) -> list[str]:
    out = subprocess.run([sys.executable, "-m", mod, *args, *TARGETS],
                         cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8")
    return (out.stdout or "").splitlines()


def pyflakes_report() -> list[str]:
    pat = re.compile(r"^(.*?):(\d+):\d+: .*?'(.+?)'")
    unexpected = []
    for line in _run("pyflakes"):
        line = line.strip()
        if not line:
            continue
        m = pat.match(line)
        if m:
            f = m.group(1).replace("\\", "/")
            name = m.group(3).split(".")[-1]
            if (f, name) in PYFLAKES_ALLOWED:
                continue
        unexpected.append(line)
    return unexpected


def vulture_report() -> list[str]:
    """Advisory list, with the structurally-invisible cases filtered out."""
    try:
        lines = _run("vulture", "--min-confidence", "60")
    except FileNotFoundError:
        return ["(vulture not installed — pip install vulture)"]
    pat = re.compile(r"^(.*?):(\d+): unused (\w+) '(.+?)' \((\d+)% confidence\)$")
    srcs = {}
    for p in ROOT.rglob("*.py"):
        sp = str(p).replace("\\", "/")
        if ".venv" in sp or "site-packages" in sp or "/data/" in sp:
            continue
        try:
            srcs[sp] = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
    blob = "\n".join(srcs.values())
    keep = []
    for line in lines:
        m = pat.match(line.strip())
        if not m:
            continue
        f, ln, kind, name = (m.group(1).replace("\\", "/"), m.group(2),
                             m.group(3), m.group(4))
        if name in QT_OVERRIDES or name in FORCED_PARAMS:
            continue
        if name.startswith("__") and name.endswith("__"):
            continue
        if re.search(r"['\"]" + re.escape(name) + r"['\"]", blob):
            continue          # named as a string somewhere = dispatched by name
        if re.search(r"\." + re.escape(name) + r"\b", blob):
            continue          # reached as an attribute somewhere
        # Decorated = something else owns the call: @pytest.fixture, @app.route,
        # @property, @staticmethod on a dispatch table, ...
        own = srcs.get(f, "").split("\n")
        prev = own[int(ln) - 2].strip() if 1 < int(ln) <= len(own) else ""
        if prev.startswith("@"):
            continue
        keep.append(f"{kind:10} {name:38} {f}:{ln}")
    return sorted(keep)


def main() -> int:
    pf = pyflakes_report()
    print("=" * 72)
    print("pyflakes (authoritative)")
    print("=" * 72)
    if pf:
        print("\n".join(pf))
        print(f"\n{len(pf)} finding(s) ABOVE the allowed baseline — fix these.")
    else:
        print(f"clean ({len(PYFLAKES_ALLOWED)} deliberate entries allowed)")

    vl = vulture_report()
    print()
    print("=" * 72)
    print("vulture (ADVISORY — verify every call site by hand before deleting)")
    print("=" * 72)
    print("\n".join(vl) if vl else "nothing after filtering")
    print(f"\n{len(vl)} candidate(s). These are NOT confirmed dead.")

    return 1 if (pf and "--strict" in sys.argv) else 0


if __name__ == "__main__":
    raise SystemExit(main())
