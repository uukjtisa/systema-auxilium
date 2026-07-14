"""
Tests for systema/execution/tool_manager.py — the grep parse/run path.

Exercises parse_grep (fence -> spec) and run_grep (spec -> observation)
through the real ToolManager. ToolManager builds Qt signal objects, so these
require PyQt6; the `qapp` fixture skips the module when Qt is unavailable.
"""
import pytest

BT = "`" * 3


@pytest.fixture
def tm(qapp):
    from systema.execution.tool_manager import ToolManager
    return ToolManager()


def _fwd(p):
    """Forward-slash a path so it drops cleanly into a fence body."""
    return str(p).replace("\\", "/")


def test_parse_grep_full_opts(tm, sample_tree):
    fence = (
        f"{BT}grep: [find sends]\n"
        r"\bsend\(" "\n"
        f"path: {_fwd(sample_tree)}\n"
        "glob: **/*.py\n"
        "output: content\n"
        "context: 1\n"
        "case: true\n"
        f"{BT}"
    )
    parsed = tm.parse_grep(fence)
    assert parsed is not None
    spec, _remaining = parsed
    assert spec["error"] is None
    assert spec["pattern"] == r"\bsend\("
    assert spec["output_mode"] == "content"
    assert spec["glob"] == "**/*.py"
    assert spec["context"] == 1
    assert spec["case_insensitive"] is True
    assert spec["annotation"] == "find sends"


def test_parse_grep_defaults(tm):
    fence = f"{BT}grep\nTODO\n{BT}"
    parsed = tm.parse_grep(fence)
    assert parsed is not None
    spec, _ = parsed
    assert spec["pattern"] == "TODO"
    assert spec["output_mode"] == "files_with_matches"
    assert spec["ignore_common"] is True
    assert spec["error"] is None


def test_parse_grep_empty_pattern_flags_error(tm):
    fence = f"{BT}grep\n\n{BT}"
    parsed = tm.parse_grep(fence)
    assert parsed is not None
    spec, _ = parsed
    assert spec["error"]


def test_run_grep_finds_matches(tm, sample_tree):
    spec = {
        "pattern": r"def send", "path": str(sample_tree),
        "output_mode": "content", "type": "py",
    }
    obs = tm.run_grep(spec).replace("\\", "/")
    assert "src/a.py:3:def send" in obs


def test_run_grep_reports_error_spec(tm):
    obs = tm.run_grep({"error": "boom", "pattern": ""})
    assert obs.startswith("ERROR:")
    assert "boom" in obs


# ── Cross-mode reconstruction round-trip (set_session_name / skills) ──────────
# A native tool call is reconstructed to fence text (stored in `content` so
# compat-mode replay can re-parse it) AND stripped for display. Before the fix a
# name-only turn reconstructed to an EMPTY string, so the call was lost on a
# cross-mode session reload. These lock the round-trip for all three tools.

def test_set_session_name_reconstructs_and_reparses(tm):
    fence = tm.tool_calls_to_fences(
        [{"name": "set_session_name", "arguments": {"name": "Deck Aesthetic Overhaul"}}])
    assert "set_session_name" in fence
    assert "Deck Aesthetic Overhaul" in fence
    # compat replay: the stored fence re-parses back to the same name
    parsed = tm.parse_set_session_name(fence)
    assert parsed is not None and parsed[0] == "Deck Aesthetic Overhaul"
    # display: the fence is stripped so the user never sees it
    assert tm.strip_tool_calls(fence).strip() == ""


def test_load_skill_reconstructs_and_reparses(tm):
    fence = tm.tool_calls_to_fences(
        [{"name": "load_skill", "arguments": {"skill_name": "pynavigator"}}])
    assert "load_skill" in fence and "pynavigator" in fence
    parsed = tm.parse_load_skill(fence)
    assert parsed is not None and parsed[0] == "pynavigator"
    assert tm.strip_tool_calls(fence).strip() == ""


def test_unload_skill_reconstructs_and_reparses(tm):
    fence = tm.tool_calls_to_fences(
        [{"name": "unload_skill", "arguments": {"skill_name": "pynavigator"}}])
    assert "unload_skill" in fence and "pynavigator" in fence
    parsed = tm.parse_unload_skill(fence)
    assert parsed is not None and parsed[0] == "pynavigator"
    assert tm.strip_tool_calls(fence).strip() == ""


def test_set_session_name_reconstruct_is_never_empty(tm):
    # The exact regression: a name-only native turn must not reconstruct to "".
    fence = tm.tool_calls_to_fences(
        [{"name": "set_session_name", "arguments": {"name": "X"}}])
    assert fence.strip() != ""
