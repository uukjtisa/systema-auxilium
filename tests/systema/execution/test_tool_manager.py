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


# ── Cross-mode reconstruction round-trip (skills + retired tools) ─────────────
# A native tool call is reconstructed to fence text (stored in `content` so
# compat-mode replay can re-parse it) AND stripped for display. Retired tools
# (set_session_name, #31) reconstruct NAME-ONLY so the engine's retired-tool
# handling fires instead of the call silently vanishing.

def test_set_session_name_reconstructs_for_retired_handling(tm):
    fence = tm.tool_calls_to_fences(
        [{"name": "set_session_name", "arguments": {"name": "Deck Aesthetic Overhaul"}}])
    # the fence names the retired tool (argument deliberately not carried)
    assert "set_session_name" in fence
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


# ── Depth-aware fences (nested-fence bug) ─────────────────────────────────────
# A tool body containing markdown code fences must survive the reconstruct →
# store → re-parse round-trip: the outer fence is emitted longer than any inner
# backtick run, and the parser only closes on a run >= the opener's length.

def test_code_fence_outgrows_nested_backticks(tm):
    body = "line\n```python\nprint(1)\n```\nafter"
    fence = tm._code_fence("write_file", body)
    assert fence.startswith("````write_file")
    assert fence.rstrip().endswith("````")


def test_write_file_with_nested_fence_roundtrips(tm):
    content = ("# Example\n\nDefault log directory:\n\n"
               "```\n{app_root}\\data\\logs\n```\n\n"
               "More text after the code block.\n")
    fence = tm.tool_calls_to_fences(
        [{"name": "write_file",
          "arguments": {"path": "D:/tmp/doc.md", "content": content}}])
    parsed = tm.parse_write_file(fence)
    assert parsed is not None
    spec, _ = parsed
    assert spec["path"] == "D:/tmp/doc.md"
    # Before the fix the body truncated at the first inner ``` — everything
    # after "Default log directory:" was silently lost.
    assert "More text after the code block." in spec["content"]
    assert "```" in spec["content"]
    # display/strip removes the WHOLE fence, inner backticks included
    assert tm.strip_tool_calls(fence).strip() == ""


def test_single_exec_policy_ignores_inner_fences(tm):
    code = 'doc = """\n```read_file\nfoo.txt\n```\n"""\nprint(doc)'
    text = tm._code_fence("python_interpreter", code)
    _cleaned, violated, _ = tm.enforce_single_exec_policy(text)
    assert violated is False


def test_single_exec_policy_still_catches_two_calls(tm):
    text = (f"{BT}python_interpreter\nprint(1)\n{BT}\n\n"
            f"{BT}python_interpreter\nprint(2)\n{BT}")
    cleaned, violated, _ = tm.enforce_single_exec_policy(text)
    assert violated is True
    assert cleaned.count("python_interpreter") == 1


def test_recover_unclosed_depth_aware(tm):
    text = "````read_file: [peek]\nD:/tmp/x.txt\n"  # 4-tick opener, never closed
    fixed, tool = tm.recover_unclosed_tool_fence(text)
    assert tool == "read_file"
    parsed = tm.parse_read_file(fixed)
    assert parsed is not None
    assert parsed[0]["path"] == "D:/tmp/x.txt"


# ── Native single-exec policy (structured calls, never text) ─────────────────

def test_native_policy_keeps_first_exec_and_nonexec(tm):
    # Interpreter-only cap (2026-07 parallel revamp): non-exec calls pass
    # through freely (typos canonicalized); only EXTRA python_interpreter
    # calls are dropped, and the first one is kept in place.
    calls = [
        {"id": "1", "name": "read_file", "arguments": {"path": "a"}},
        {"id": "2", "name": "python_interpreter", "arguments": {"code": "1"}},
        {"id": "3", "name": "readfile", "arguments": {"path": "b"}},
        {"id": "4", "name": "python_interpreter", "arguments": {"code": "2"}},
    ]
    kept, violated, msg = tm.enforce_single_exec_policy_native(calls)
    assert violated is True
    assert [c["name"] for c in kept] == ["read_file", "python_interpreter", "read_file"]
    assert kept[1]["arguments"]["code"] == "1"
    assert "python_interpreter" in msg


def test_native_policy_canonicalizes_typo_names(tm):
    kept, violated, _ = tm.enforce_single_exec_policy_native(
        [{"id": "1", "name": "readfile", "arguments": {"path": "a"}}])
    assert violated is False
    assert kept[0]["name"] == "read_file"


def test_native_policy_no_calls_no_violation(tm):
    kept, violated, _ = tm.enforce_single_exec_policy_native([])
    assert kept == [] and violated is False


# ── Native argument extraction (native_args_to_spec) ─────────────────────────

def test_native_args_read_file_spec(tm):
    spec = tm.native_args_to_spec(
        "read_file", {"path": "D:/x.py", "start_line": "5",
                      "max_lines": 40, "annotation": "peek"})
    assert spec == {"path": "D:/x.py", "annotation": "peek",
                    "start": 5, "count": 40}


def test_native_args_edit_file_range_and_error(tm):
    spec = tm.native_args_to_spec(
        "edit_file", {"path": "f.py", "start_line": 3, "end_line": 4,
                      "new_text": "x = 1"})
    assert spec["start"] == 3 and spec["end"] == 4
    assert spec["error"] is None
    bad = tm.native_args_to_spec("edit_file", {"path": "f.py", "new_text": "x"})
    assert bad["error"]


def test_native_args_python_interpreter_code(tm):
    code = tm.native_args_to_spec(
        "python_interpreter", {"code": "print(1)", "annotation": "run"})
    assert code == "print(1)"
    assert tm.work.interpreter.last_annotation == "run"


def test_native_args_grep_defaults(tm):
    spec = tm.native_args_to_spec("grep", {"pattern": "TODO"})
    assert spec["output_mode"] == "files_with_matches"
    assert spec["ignore_common"] is True
    assert spec["error"] is None
