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


# ── inject-all recall mode: search_memory dropped, everything's in the prompt ──

INJECT_ALL_FNS = ("memorize(", "update_memory(", "forget_memory(")


def test_inject_all_memory_section_drops_search_keeps_the_rest():
    text = shared.memory_section("hint", inject_all=True)
    assert "search_memory" not in text, "inject-all must drop search_memory"
    for fn in INJECT_ALL_FNS:
        assert fn in text, f"inject-all memory_section is missing {fn}"
    assert "SYSTEM MEMORY BLOCK" in text   # points the agent at the injected set


def test_inject_all_assembled_both_modes_drop_search():
    for native in (False, True):
        p = global_instructions.get_system_prompt(system_info="t",
                                                  memory_inject_all=True,
                                                  native_tools=native)
        assert "search_memory" not in p
        for fn in INJECT_ALL_FNS:
            assert fn in p
        if native:
            assert "```" not in p and "JSON" not in p


def test_default_mode_still_teaches_search():
    # Regression: the non-inject-all path (RAG / tasks in RAG) keeps search.
    p = global_instructions.get_system_prompt(system_info="t")
    assert "search_memory(" in p
