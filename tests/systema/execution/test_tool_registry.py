"""
Tests for systema/execution/tool_registry.py

Registry integrity: every canonical tool exposes the fields the prompt
renderers and native-schema builder rely on, and the `grep` entry round-trips
through its to_fence builder. Pure stdlib — no PyQt6.
"""
from systema.execution import tool_registry as reg


def test_grep_registered():
    assert "grep" in reg.CANONICAL_TOOLS
    spec = reg.CANONICAL_TOOLS["grep"]
    assert spec["param"][0] == "pattern"
    assert spec["exec"] is True
    assert callable(spec["to_fence"])


def test_grep_to_fence_roundtrip():
    body = reg.CANONICAL_TOOLS["grep"]["to_fence"]({
        "pattern": r"\bsend\(",
        "glob": "**/*.py",
        "output_mode": "content",
        "context": 2,
        "case_insensitive": True,
    })
    lines = body.split("\n")
    assert lines[0] == r"\bsend\("
    assert "glob: **/*.py" in lines
    assert "output: content" in lines
    assert "context: 2" in lines
    assert "case: true" in lines


def test_grep_default_output_mode_omitted_from_fence():
    body = reg.CANONICAL_TOOLS["grep"]["to_fence"]({
        "pattern": "x", "output_mode": "files_with_matches",
    })
    assert "output:" not in body


def test_grep_ignore_common_only_emitted_when_false():
    on = reg.CANONICAL_TOOLS["grep"]["to_fence"]({"pattern": "x"})
    assert "ignore_common" not in on
    off = reg.CANONICAL_TOOLS["grep"]["to_fence"](
        {"pattern": "x", "ignore_common": False})
    assert "ignore_common: false" in off


def test_every_tool_has_required_fields():
    for name, spec in reg.CANONICAL_TOOLS.items():
        assert spec.get("description"), f"{name} missing description"
        assert len(spec["param"]) == 2, f"{name} param must be (name, desc)"
        assert "exec" in spec, f"{name} missing exec flag"
        compat = spec.get("compat", {})
        for key in ("table_row", "fence_example", "usage"):
            assert compat.get(key), f"{name} missing compat.{key}"


def test_exec_tool_keys_derived_from_exec_flag():
    assert "grep" in reg.EXEC_TOOL_KEYS
    assert reg.EXEC_TOOL_KEYS == frozenset(
        k for k, v in reg.CANONICAL_TOOLS.items() if v.get("exec"))


def test_grep_excluded_from_file_tool_keys():
    # grep is read-only with no diff card, so it must NOT be in FILE_TOOL_KEYS.
    assert "grep" not in reg.FILE_TOOL_KEYS
    assert reg.FILE_TOOL_KEYS == ("read_file", "edit_file", "write_file")
