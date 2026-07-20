"""
Tests for the memory prompt docs (shared.memory_section) and their presence in
BOTH assembled tool-calling modes — the 4-function toolset must be documented
identically in compat and native, and the removed functions must be gone.
Qt-free.
"""
from systema.engine.prompts import shared, global_instructions

FOUR_FNS = ("memorize(", "search_memory(", "update_memory(", "forget_memory(")
REMOVED_FNS = ("view_all_memory", "delete_memory")


def test_memory_section_documents_exactly_the_four_functions():
    text = shared.memory_section("hint")
    for fn in FOUR_FNS:
        assert fn in text, f"memory_section is missing {fn}"
    for fn in REMOVED_FNS:
        assert fn not in text, f"memory_section still mentions removed {fn}"


def test_memory_section_present_in_both_assembled_modes():
    compat_p = global_instructions.get_system_prompt(system_info="t")
    native_p = global_instructions.get_system_prompt(system_info="t",
                                                     native_tools=True)
    for p in (compat_p, native_p):
        for fn in FOUR_FNS:
            assert fn in p
        for fn in REMOVED_FNS:
            assert fn not in p


def test_native_assembled_prompt_stays_fence_and_json_free():
    native_p = global_instructions.get_system_prompt(system_info="t",
                                                     native_tools=True)
    assert "```" not in native_p
    assert "JSON" not in native_p
