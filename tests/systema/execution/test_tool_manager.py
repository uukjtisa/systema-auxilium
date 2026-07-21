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


def test_exec_policy_cap_retired_allows_two_calls(tm):
    # 2026-07 revamp: the one-python_interpreter-per-response cap is RETIRED —
    # multiple calls run sequentially in emitted order. The enforcer is a
    # no-op pass-through keeping the (text, violated, msg) contract.
    text = (f"{BT}python_interpreter\nprint(1)\n{BT}\n\n"
            f"{BT}python_interpreter\nprint(2)\n{BT}")
    cleaned, violated, msg = tm.enforce_single_exec_policy(text)
    assert violated is False
    assert msg == ""
    assert cleaned == text                      # nothing dropped or rewritten
    assert cleaned.count("python_interpreter") == 2


def test_recover_unclosed_depth_aware(tm):
    text = "````read_file: [peek]\nD:/tmp/x.txt\n"  # 4-tick opener, never closed
    fixed, tool = tm.recover_unclosed_tool_fence(text)
    assert tool == "read_file"
    parsed = tm.parse_read_file(fixed)
    assert parsed is not None
    assert parsed[0]["path"] == "D:/tmp/x.txt"


# ── Native single-exec policy (structured calls, never text) ─────────────────

def test_native_policy_cap_retired_keeps_all_calls(tm):
    # Cap retired (2026-07): extra python_interpreter calls are NO LONGER
    # dropped — the native enforcer only canonicalizes typo'd tool names.
    calls = [
        {"id": "1", "name": "read_file", "arguments": {"path": "a"}},
        {"id": "2", "name": "python_interpreter", "arguments": {"code": "1"}},
        {"id": "3", "name": "readfile", "arguments": {"path": "b"}},
        {"id": "4", "name": "python_interpreter", "arguments": {"code": "2"}},
    ]
    kept, violated, msg = tm.enforce_single_exec_policy_native(calls)
    assert violated is False
    assert msg == ""
    assert [c["name"] for c in kept] == [
        "read_file", "python_interpreter", "read_file", "python_interpreter"]
    assert kept[1]["arguments"]["code"] == "1"
    assert kept[3]["arguments"]["code"] == "2"


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


# ── web_search: parse / native spec / fence round-trip / interp buffering ────

def test_parse_web_search_full_opts(tm):
    fence = (f"{BT}web_search: [find asyncio docs]\n"
             "https://docs.python.org/3/library/asyncio.html\n"
             "mode: open\n"
             "max_results: 5\n"
             "fetch_top: 2\n"
             f"{BT}")
    parsed = tm.parse_web_search(fence)
    assert parsed is not None
    spec, _remaining = parsed
    assert spec["error"] is None
    assert spec["mode"] == "open"
    assert spec["query"] == "https://docs.python.org/3/library/asyncio.html"
    assert spec["max_results"] == 5
    assert spec["fetch_top"] == 2
    assert spec["annotation"] == "find asyncio docs"


def test_parse_web_search_defaults(tm):
    fence = f"{BT}web_search\npython asyncio tutorial\n{BT}"
    parsed = tm.parse_web_search(fence)
    assert parsed is not None
    spec, _ = parsed
    assert spec["mode"] == "search"
    assert spec["query"] == "python asyncio tutorial"
    assert spec["max_results"] == 8
    assert spec["fetch_top"] == 0
    assert spec["error"] is None


def test_parse_web_search_empty_query_flags_error(tm):
    fence = f"{BT}web_search\n\nmode: search\n{BT}"
    parsed = tm.parse_web_search(fence)
    assert parsed is not None
    spec, _ = parsed
    assert spec["error"]


def test_parse_web_search_bad_mode_falls_back(tm):
    fence = f"{BT}web_search\nquery here\nmode: browse\n{BT}"
    spec, _ = tm.parse_web_search(fence)
    assert spec["mode"] == "search"


def test_native_args_web_search_spec(tm):
    spec = tm.native_args_to_spec(
        "web_search", {"query": "rust borrow checker", "mode": "search",
                       "max_results": "3", "annotation": "look up"})
    assert spec == {"mode": "search", "query": "rust borrow checker",
                    "max_results": 3, "fetch_top": 0,
                    "annotation": "look up", "error": None}
    # url alias for open/links + missing query flags an error
    spec2 = tm.native_args_to_spec(
        "web_search", {"url": "https://example.com", "mode": "links"})
    assert spec2["mode"] == "links" and spec2["query"] == "https://example.com"
    assert tm.native_args_to_spec("web_search", {"mode": "search"})["error"]


def test_web_search_fence_roundtrip(tm):
    # native call -> compat fence (registry to_fence) -> parse: same spec.
    fences = tm.tool_calls_to_fences(
        [{"name": "web_search",
          "arguments": {"query": "qt event loop", "mode": "search",
                        "max_results": 4, "annotation": "research"}}])
    parsed = tm.parse_web_search(fences)
    assert parsed is not None
    spec, _ = parsed
    assert spec["mode"] == "search"
    assert spec["query"] == "qt event loop"
    assert spec["max_results"] == 4
    assert spec["error"] is None


def test_interp_web_search_buffers_card_and_subresult(tm, monkeypatch):
    # Inside the interpreter: card is BUFFERED (python card first), the full
    # output is recorded as a separate tool subresult, and the interpreter
    # gets only a short confirmation string.
    from systema.net import web_research as wr
    fake = [{"title": "T1", "href": "https://a", "body": "B1"}]
    monkeypatch.setattr(wr, "search", lambda q, max_results=8, config=None: fake)
    tm._interp_card_buffer.clear()
    tm._interp_subresults.clear()
    ret = tm.interp_web_search("qt docs")
    assert "separate tool result" in ret
    assert tm._card_capture is False                    # restored
    assert len(tm._interp_card_buffer) == 1
    card = tm._interp_card_buffer[0]
    assert card["card_type"] == "web_search"
    assert card["results"] == fake
    assert len(tm._interp_subresults) == 1
    name, obs = tm._interp_subresults[0]
    assert name == "web_search" and "T1" in obs and "https://a" in obs
    # flush empties the buffer (emission itself needs a chat window — not here)
    tm.flush_interp_cards()
    assert tm._interp_card_buffer == []
