"""
Tests for systema/engine/prompts/{compat,native}.py

Guards the two hard prompt invariants and confirms the grep tool is advertised
in BOTH tool-calling modes:

  * native renderings contain NO code fence (```) and NO literal word "JSON"
    (weak native models echo any format they see).
  * the compat format section contains EXACTLY ONE "NEVER use JSON" line.

These import only the prompt modules + tool_registry — no PyQt6.
"""
from systema.engine.prompts import compat, native


def test_native_header_has_no_fence_or_json():
    text = native.native_header()
    assert "```" not in text
    assert "JSON" not in text


def test_native_advertises_grep():
    text = native.native_header()
    assert "grep(" in text


def test_native_module_constants_fence_free():
    # The module docstring promises nothing in native carries a fence or "JSON".
    for name in ("MUST_REMEMBER", "WORK_OPTIONS", "SESSION_NAMING_TAIL",
                 "MALFORMED_WORK_STEP_PROMPT_NATIVE"):
        text = getattr(native, name)
        assert "```" not in text, f"{name} contains a code fence"
        assert "JSON" not in text, f"{name} contains the word JSON"


def test_compat_format_section_single_never_use_json():
    text = compat.tool_format_section()
    assert text.count("NEVER use JSON") == 1


def test_compat_advertises_grep_in_table_and_example():
    text = compat.tool_format_section()
    assert "grep:" in text          # table row
    assert "```grep" in text        # fence example


def test_compat_and_native_agree_on_grep_presence():
    assert "grep" in native.native_header()
    assert "grep" in compat.tool_format_section()
