"""
tests/systema/execution/test_work_ping.py

The work-mode continuation ping is the one prompt that is cheap to enrich:
`AIEngine.continue_work()` slims the PREVIOUS ping down to output-only and
appends a fresh full one, so only the latest copy ever carries the instruction
block. Anything added there is paid for once per turn instead of riding in the
system prompt on every request forever — which is why the situational reminders
live here.

Two things are pinned. First the builder, `shared.work_context_block`, which is
a pure function: what just ran, whether it failed, and — the mistake this was
built for — a loaded skill's path rendered with its REAL FOLDER, because the
model keeps writing SKILLS_PATH\\scripts\\x.py instead of
SKILLS_PATH\\<folder>\\scripts\\x.py. Second, that ToolManager assembles it into
BOTH modes' templates without breaking the native invariants (no fences, never
the word "JSON").
"""
import pytest

from systema.engine.prompts import shared
from systema.engine.prompts.global_instructions import (WORK_MODE_PROMPT,
                                                        WORK_MODE_PROMPT_NATIVE)
from systema.execution import capabilities as caps


# ── the builder: what just happened ──────────────────────────────────────────

def test_nothing_to_report_renders_nothing():
    """A ping with no live state must not grow an empty heading."""
    assert shared.work_context_block() == ""


def test_the_last_tool_is_named():
    block = shared.work_context_block(last_tool='interpreter', observation="ok")
    assert "python_interpreter" in block
    assert "---WHERE YOU ARE---" in block


def test_the_step_annotation_grounds_the_output():
    """The annotation is the label the model itself wrote for the step — it ties
    the raw output back to the intent behind it."""
    block = shared.work_context_block(last_tool='interpreter',
                                      annotation="checking the signature",
                                      observation="ok")
    assert "checking the signature" in block


def test_a_traceback_steers_away_from_a_blind_retry():
    block = shared.work_context_block(
        last_tool='interpreter',
        observation='Traceback (most recent call last):\nValueError: nope')
    assert "FAILED" in block
    assert "unchanged will fail again" in block


def test_an_error_observation_counts_as_a_failure():
    block = shared.work_context_block(last_tool='read_file',
                                      observation="ERROR: FileNotFoundError")
    assert "FAILED" in block


def test_an_empty_observation_steers_to_inspect():
    block = shared.work_context_block(last_tool='interpreter',
                                      observation="No previous output")
    assert "printed nothing" in block


def test_a_healthy_file_step_gets_its_own_steer():
    assert "Re-read" in shared.work_context_block(last_tool='edit_file',
                                                  observation="Edit applied.")


def test_only_one_steer_is_emitted():
    """A failure must not ALSO collect the generic per-tool advice — the block
    has a line budget, and two contradictory steers is worse than one."""
    block = shared.work_context_block(
        last_tool='edit_file', observation="ERROR: no such file")
    assert "FAILED" in block
    assert "Re-read" not in block


# ── the builder: the skill-path mistake ──────────────────────────────────────

def test_a_loaded_skill_renders_its_real_folder_in_the_path():
    block = shared.work_context_block(last_tool='skill',
                                      loaded_skills=[("PPTX Builder", "pptx")],
                                      observation="Skill loaded.")
    assert r"SKILLS_PATH\pptx\scripts" in block


def test_the_folderless_path_is_never_produced():
    """The regression itself: SKILLS_PATH\\scripts\\... with the skill folder
    dropped is the exact path the model keeps building."""
    block = shared.work_context_block(loaded_skills=[("PPTX Builder", "pptx")])
    assert r"SKILLS_PATH\scripts" not in block


def test_the_display_name_and_the_folder_are_both_shown():
    """They differ — the skill is KNOWN by its frontmatter name but LIVES in a
    folder, and conflating the two is how the wrong path gets built."""
    block = shared.work_context_block(loaded_skills=[("PPTX Builder", "pptx")])
    assert "PPTX Builder" in block and "pptx" in block


def test_no_skill_line_when_nothing_is_loaded():
    block = shared.work_context_block(last_tool='interpreter', observation="ok")
    assert "SKILLS_PATH" not in block


def test_many_loaded_skills_are_capped():
    """Line budget: the ping is per-turn, not free."""
    skills = [(f"skill {i}", f"folder-{i}") for i in range(9)]
    block = shared.work_context_block(loaded_skills=skills)
    assert block.count("is loaded") == 3
    assert "6 more skill(s) loaded" in block


def test_a_skill_with_no_resolvable_folder_is_skipped():
    assert shared.work_context_block(loaded_skills=[("Ghost", "")]) == ""


# ── the builder: namespace recap ─────────────────────────────────────────────

def test_namespace_names_are_listed_on_one_line():
    block = shared.work_context_block(namespace_names=["APP_ROOT", "web_search"])
    assert "APP_ROOT, web_search" in block
    assert "never import or redefine" in block


def test_no_namespace_line_without_names():
    """Interpreter switched off → nothing to recap, and nothing is claimed."""
    block = shared.work_context_block(last_tool='read_file', observation="ok")
    assert "namespace" not in block


def test_the_block_stays_compact():
    """Everything at once still fits the budget — this is per-turn text."""
    block = shared.work_context_block(
        last_tool='interpreter', annotation="a" * 60,
        loaded_skills=[(f"s{i}", f"f{i}") for i in range(9)],
        namespace_names=[f"name_{i}" for i in range(20)],
        observation="ERROR: boom")
    assert len(block.strip().splitlines()) <= 12


def test_the_block_is_a_pure_function_of_its_inputs():
    args = dict(last_tool='interpreter', annotation="x",
                loaded_skills=[("a", "b")], namespace_names=["APP_ROOT"],
                observation="ok")
    assert shared.work_context_block(**args) == shared.work_context_block(**args)


# ── mode parity ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("template", [WORK_MODE_PROMPT, WORK_MODE_PROMPT_NATIVE],
                         ids=["compat", "native"])
def test_both_templates_carry_the_situation(template):
    sit = shared.work_context_block(last_tool='interpreter', observation="ok")
    out = template.format(work_output="OUT", situation=sit)
    assert "---WHERE YOU ARE---" in out
    assert "OUT" in out


def test_an_empty_situation_leaves_the_ping_shape_untouched():
    """Spacing regression guard: the placeholder must vanish completely, not
    leave a stray blank paragraph in every plain ping."""
    out = WORK_MODE_PROMPT.format(work_output="OUT", situation="")
    assert "OUT\n\n---DECISION TIME---" in out
    assert "{situation}" not in out


def test_the_native_ping_stays_fence_free_and_never_says_json():
    """The standing native invariant (see test_prompts.py) must survive
    anything the situation block adds."""
    sit = shared.work_context_block(
        last_tool='skill', annotation="load the deck builder",
        loaded_skills=[("PPTX Builder", "pptx")],
        namespace_names=["APP_ROOT", "SKILLS_PATH"],
        observation="Skill loaded.")
    out = WORK_MODE_PROMPT_NATIVE.format(work_output="OUT", situation=sit)
    assert "```" not in out
    assert "JSON" not in out


def test_the_slim_history_variant_carries_output_only():
    """Only the LIVE ping may carry the block — otherwise every turn's copy
    accumulates in history and the saving is undone."""
    slim = shared.WORK_MODE_OUTPUT_ONLY_PROMPT.format(work_output="OUT")
    assert "---WHERE YOU ARE---" not in slim
    assert "---DECISION TIME---" not in slim
    assert "OUT" in slim


# ── ToolManager assembly ─────────────────────────────────────────────────────

def test_get_work_prompt_fills_both_placeholders(tool_manager):
    tool_manager.work.last_output = "42\n"
    tool_manager.work.last_tool = 'interpreter'
    tool_manager.work.interpreter.last_annotation = "adding the numbers"

    prompt = tool_manager.get_work_prompt()

    assert "42" in prompt
    assert "adding the numbers" in prompt
    assert "{situation}" not in prompt and "{work_output}" not in prompt


def test_braces_in_the_observation_survive_formatting(tool_manager):
    """The observation is a VALUE, never a template — a printed dict used to be
    the kind of thing that turns .format() into a KeyError."""
    tool_manager.work.last_output = "{'key': 'value'}"
    tool_manager.work.last_tool = 'interpreter'

    assert "{'key': 'value'}" in tool_manager.get_work_prompt()


def test_a_broken_situation_never_kills_the_work_loop(tool_manager, monkeypatch):
    monkeypatch.setattr(shared, "work_context_block",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    tool_manager.work.last_output = "still here"

    prompt = tool_manager.get_work_prompt()

    assert "still here" in prompt
    assert "---DECISION TIME---" in prompt


def test_the_recap_lists_what_this_agent_was_taught(tool_manager):
    tool_manager.prompt_context = caps.CHAT
    tool_manager.documented_gates = caps.gates_for_chat(
        allow_workmode=True, has_skills=False,
        include_notify_tool=False, include_memory=False)
    tool_manager.work.last_output = "ok"

    prompt = tool_manager.get_work_prompt()

    assert "APP_ROOT" in prompt
    assert "notify" not in prompt, "advertised an option the user switched off"


def test_a_tasker_is_never_reminded_of_chat_only_names(tool_manager):
    """send_message_main exists ONLY for a tasker; the chat ping must not name
    it, and the tasker's ping must."""
    tool_manager.prompt_context = caps.TASK
    tool_manager.documented_gates = caps.gates_for_task(
        {'allow_workmode': True}, has_main_channel=True)
    tool_manager.work.last_output = "ok"

    assert "send_message_main" in tool_manager.get_work_prompt()

    tool_manager.prompt_context = caps.CHAT
    tool_manager.documented_gates = caps.gates_for_chat(
        allow_workmode=True, has_skills=False)
    assert "send_message_main" not in tool_manager.get_work_prompt()
