"""
systema/engine/prompts/global_instructions.py

Thin façade over the prompts package. All callers (ai_engine, tool_manager,
controller.build_task_system_prompt, debug hot-reload) keep importing from THIS
module; the actual text lives in:

  shared.py   mode-agnostic behavioral sections, authored once
  compat.py   fence renderings + compat tails/primer/injected prompts
  native.py   native renderings + native tails/primer/injected prompts

Tool docs (tables, fence examples, native schemas) render from
systema.execution.tool_registry — the single source of truth for tools.

Parity rule (.dev-copy): every behavioral instruction must be present in BOTH
modes' output; native output contains no code fences and never the word "JSON";
the assembled compat prompt says "NEVER use JSON" exactly once; the FINISH rule
is stated exactly once per assembled prompt (in the work section).
"""

from systema.engine.prompts import shared, compat, native

# ── Re-exports (stable public names) ──────────────────────────────────────────
get_skill_path_rule = shared.get_skill_path_rule
WORK_MODE_OUTPUT_ONLY_PROMPT = shared.WORK_MODE_OUTPUT_ONLY_PROMPT
SKILL_ALREADY_LOADED_PROMPT = shared.SKILL_ALREADY_LOADED_PROMPT
SKILL_NOT_LOADED_PROMPT = shared.SKILL_NOT_LOADED_PROMPT
SKILL_LOADED_CHAT_PROMPT = shared.SKILL_LOADED_CHAT_PROMPT
SKILL_UNLOADED_CHAT_PROMPT = shared.SKILL_UNLOADED_CHAT_PROMPT
EMPTY_EXIT_SUMMARY_PROMPT = shared.EMPTY_EXIT_SUMMARY_PROMPT
PREFILLING = compat.PREFILLING
PREFILLING_NATIVE = native.PREFILLING_NATIVE
EXEC_CODE_TOOLCALL_VIOLATION_PROMPT = compat.EXEC_CODE_TOOLCALL_VIOLATION_PROMPT
EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE = (
    native.EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE)
EXECUTE_CODE_RETIRED_PROMPT = compat.EXECUTE_CODE_RETIRED_PROMPT
EXECUTE_CODE_RETIRED_PROMPT_NATIVE = native.EXECUTE_CODE_RETIRED_PROMPT_NATIVE
FORMAT_AUTOFIX_REMINDER = compat.FORMAT_AUTOFIX_REMINDER
MALFORMED_WORK_STEP_PROMPT = compat.MALFORMED_WORK_STEP_PROMPT
MALFORMED_WORK_STEP_PROMPT_NATIVE = native.MALFORMED_WORK_STEP_PROMPT_NATIVE

# ── Injected work-continuation prompts (per-mode options tail composed in) ────
# .replace keeps the {work_output} placeholder intact for the caller's .format.
_CORE = shared.WORK_CONTINUATION_CORE
WORK_MODE_PROMPT = ("<SYSTEM_MESSAGE>\n"
                    + _CORE.replace("{options}", compat.WORK_OPTIONS)
                    + "</SYSTEM_MESSAGE>")
WORK_MODE_PROMPT_NATIVE = ("<SYSTEM_MESSAGE>\n"
                           + _CORE.replace("{options}", native.WORK_OPTIONS)
                           + "</SYSTEM_MESSAGE>")

_SKILL_LOADED_HDR = ("<SYSTEM_MESSAGE>\n"
                     "SKILL '{skill_name}' has been loaded into your system context.\n"
                     "You now have its full instructions available. Proceed with your task.\n\n")
_SKILL_UNLOADED_HDR = ("<SYSTEM_MESSAGE>\n"
                       "SKILL '{skill_name}' has been unloaded from your system context.\n"
                       "You now have its full instructions removed. Proceed with your task.\n\n")
SKILL_LOADED_WORK_PROMPT = (
    _SKILL_LOADED_HDR + _CORE.replace("{options}", compat.WORK_OPTIONS) + "</SYSTEM_MESSAGE>")
SKILL_LOADED_WORK_PROMPT_NATIVE = (
    _SKILL_LOADED_HDR + _CORE.replace("{options}", native.WORK_OPTIONS) + "</SYSTEM_MESSAGE>")
SKILL_UNLOADED_WORK_PROMPT = (
    _SKILL_UNLOADED_HDR + _CORE.replace("{options}", compat.WORK_OPTIONS) + "</SYSTEM_MESSAGE>")
SKILL_UNLOADED_WORK_PROMPT_NATIVE = (
    _SKILL_UNLOADED_HDR + _CORE.replace("{options}", native.WORK_OPTIONS) + "</SYSTEM_MESSAGE>")


def get_system_prompt(
        is_task_session_prompt: bool = False,
        system_info: str = "",
        voice_mode: bool = False,
        elevenlabs_enabled: bool = False,
        skills=None,
        # ── Modular section flags — all True by default ──
        include_tool_format: bool = True,
        include_memory: bool = True,
        memory_inject_all: bool = False,   # inject-all recall mode → drop search_memory
        include_execution_tools: bool = True,
        include_fence_syntax: bool = True,
        include_interpreter_mode_rules: bool = True,
        include_must_remember: bool = True,
        # Optionals, off by default (token cost).
        include_image_tools: bool = False,
        include_controller_ref: bool = False,
        include_notify_tool: bool = False,
        # Native function calling: fence-free renderings + native header.
        native_tools: bool = False,
):
    """Assemble the system prompt for the requested tool-calling mode.

    Disable specific sections for stripped-down agents (background tasks,
    skill loaders). Tasker sub-agents flow through this same function, so
    native/compat parity there is automatic.
    """
    hint = native.INVOKE_HINT if native_tools else compat.INVOKE_HINT
    voice_instructions = shared.voice_section(elevenlabs_enabled) if voice_mode else ""
    skills_present = bool(skills)

    body = []
    if native_tools:
        body.append(native.native_header(include_skills=skills_present))
    elif include_tool_format:
        body.append(compat.tool_format_section(
            include_workmode=include_interpreter_mode_rules and include_fence_syntax,
            include_skills=skills_present,
            include_images=include_image_tools))

    if include_memory:
        body.append(shared.memory_section(hint, inject_all=memory_inject_all))
    if include_execution_tools and include_interpreter_mode_rules:
        body.append(shared.PYTHON_INTERPRETER_SECTION)
        body.append(shared.FILE_TOOLS_SECTION)
    if include_image_tools:
        body.append(shared.image_tools_section(
            task=is_task_session_prompt, invoke_hint=hint))
    if include_controller_ref:
        body.append(shared.controller_ref_section(hint))
    if include_notify_tool:
        body.append(shared.NOTIFY_SECTION)
    if include_must_remember:
        body.append(native.MUST_REMEMBER if native_tools else compat.MUST_REMEMBER)

    # ── Skills block (list + per-mode how-to) ────────────────────────────────
    skills_block = ""
    if skills_present:
        skills_block = (shared.skills_list_block(skills)
                        + (native.skills_howto_tail() if native_tools
                           else compat.skills_howto_tail()))

    # Only inject the skill path rule when at least one skill is loaded.
    _any_loaded = skills and any(s.get('is_loaded') for s in skills)
    _skill_path_rule = ("\n\n" + shared.get_skill_path_rule()) if _any_loaded else ""

    preamble_parts = filter(None, [
        shared.PREAMBLE,
        system_info,
        voice_instructions,
    ])

    return (
        "\n\n".join(preamble_parts)
        + "\n\n"
        + "\n\n".join(body)
        + _skill_path_rule
        + (f"\n\n{skills_block}" if skills_block else "")
    )
