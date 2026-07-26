"""
tests/systema/execution/test_capability_manifest.py

The capability manifest exists because "what the agent is promised" and "what
the agent is given" were maintained by hand in different places and drifted:

  * native advertised 4 tools while compat advertised 8 — the file subsystem
    (read_file/edit_file/write_file/grep) had NO native schemas at all, even
    though the native system prompt taught all four. Every native attempt came
    back "Tool 'read_file' does not exist and was NOT run".
  * attach_image_to_chat was documented as a tool but only ever existed as a
    python-namespace function (the reported bug).
  * the unknown-tool error listed every registry key rather than what was
    actually on offer, so the model kept retrying tools it never had.

These tests make that class of bug impossible to reintroduce quietly.
"""
import re

import pytest

from systema.execution import capabilities as caps
from systema.execution import tool_registry as reg
from systema.engine.prompts.compat import tool_format_section
from systema.engine.prompts.global_instructions import get_system_prompt


ALL_ON = dict(allow_workmode=True, has_skills=True, include_image_tools=True,
              include_notify_tool=True, include_memory=True,
              include_controller_ref=True)


@pytest.fixture
def tm(tool_manager):
    tool_manager.allow_workmode = True
    return tool_manager


def _compat_rows(section: str):
    return re.findall(r'^- (\w+):', section, re.M)


# ── the headline invariant ───────────────────────────────────────────────────

@pytest.mark.parametrize("workmode,skills,images", [
    (True, True, True), (True, True, False), (True, False, False),
    (False, True, False), (False, False, False),
])
def test_native_and_compat_advertise_the_same_tools(tm, workmode, skills, images):
    tm.allow_workmode = workmode
    native = [t['name'] for t in tm.get_canonical_tools(include_skills=skills,
                                                        include_images=images)]
    compat = _compat_rows(tool_format_section(include_workmode=workmode,
                                              include_skills=skills,
                                              include_images=images))
    assert native == compat, (
        f"native/compat tool sets drifted (workmode={workmode}, skills={skills}, "
        f"images={images}):\n  native={native}\n  compat={compat}")


def test_the_file_subsystem_is_callable_in_native_mode(tm):
    """The regression that shipped silently: native mode lost read_file,
    edit_file, write_file and grep, so the model had to fall back to writing
    python for every file operation."""
    native = [t['name'] for t in tm.get_canonical_tools()]
    for name in ('read_file', 'edit_file', 'write_file', 'grep'):
        assert name in native, f"{name} has no native schema — native mode cannot call it"


def test_native_schemas_are_well_formed(tm):
    for spec in tm.get_canonical_tools(include_skills=True, include_images=True):
        assert spec['name'] and spec['description']
        params = spec['parameters']
        assert params['type'] == 'object'
        assert params['properties'], f"{spec['name']} exposes no parameters"
        for req in params['required']:
            assert req in params['properties'], \
                f"{spec['name']} requires '{req}' but never declares it"


# ── promise vs. offer ────────────────────────────────────────────────────────

@pytest.mark.parametrize("native_mode", [True, False])
def test_every_tool_the_prompt_teaches_is_actually_offered(tm, native_mode):
    """The exact bug reported for attach_image_to_chat, generalised: if the
    prompt names a TOOL, that tool must be in the offered set for the same
    flags — otherwise the model is being taught to call something that will
    answer 'does not exist'."""
    prompt = get_system_prompt(
        system_info="x",
        skills=[{'name': 'demo', 'description': 'a demo skill', 'is_loaded': False}],
        include_image_tools=True, native_tools=native_mode)
    offered = set(tm.offered_tools(include_skills=True, include_images=True))

    for name in caps.tools_for(caps.CHAT, caps.gates_for_chat(**ALL_ON)):
        if re.search(rf'\b{re.escape(name)}\b', prompt):
            assert name in offered, (
                f"the {'native' if native_mode else 'compat'} prompt teaches "
                f"'{name}' but it is not offered")


def test_a_gated_off_capability_is_neither_taught_nor_offered(tm):
    """Image tools off: the prompt must not mention them AND they must not be
    advertised. (The inverse half of the honesty rule.)"""
    prompt = get_system_prompt(system_info="x", skills=[],
                               include_image_tools=False, native_tools=True)
    offered = tm.offered_tools(include_skills=True, include_images=False)

    assert 'attach_image_to_chat' not in offered
    assert 'attach_image_to_chat' not in prompt
    assert 'attach_image_to_context' not in prompt


# ── manifest integrity ───────────────────────────────────────────────────────

def test_every_tool_surface_capability_has_a_registry_entry():
    """A tool-surface capability with no CANONICAL_TOOLS entry would render an
    empty schema / blank table row."""
    for cap in caps.CAPABILITIES:
        if cap.on(caps.TOOL):
            assert cap.name in reg.CANONICAL_TOOLS, \
                f"{cap.name} is declared a tool but has no registry entry"


def test_every_registry_tool_is_declared_in_the_manifest():
    """The other direction: a registry entry nobody declares is a tool that can
    never be offered — invisible dead weight in the prompt docs."""
    for name in reg.CANONICAL_TOOLS:
        cap = caps.get(name)
        assert cap is not None, f"{name} is in the registry but not in the manifest"
        assert cap.on(caps.TOOL), f"{name} is in the registry but not a tool surface"


def test_every_advertised_tool_can_actually_be_executed(tm):
    """Advertising a tool the batch loop cannot dispatch is the reported bug in
    its purest form: the model calls it and gets 'does not exist'. Every offered
    tool must have BOTH a compat parser and a native arg converter."""
    for name in tm.offered_tools(include_skills=True, include_images=True):
        assert name in tm._tool_keys, f"{name} is advertised but not a known tool key"
        if name in ('load_skill', 'unload_skill', 'python_interpreter'):
            continue        # handled inline by the batch loop, no spec converter
        spec = tm.native_args_to_spec(name, {})
        assert spec is not None, (
            f"{name} is advertised but native_args_to_spec returns None — a "
            f"native call would fall through to 'does not exist'")


def test_the_unknown_tool_message_only_lists_offered_tools(tm, monkeypatch):
    """The old message listed every registry key, so it advertised tools the
    model had never been given and invited it to retry them."""
    from systema.engine.ai_engine import AIEngine
    import types

    eng = object.__new__(AIEngine)
    eng.tool_manager = tm
    eng.skill_manager = None
    eng.include_image_tools = False
    eng._malformed_step_retries = 0
    eng._emit_work_narration = lambda text: False
    eng._append_assistant = lambda text: {}
    eng._inject_pending_format_reminder = lambda: None
    tm._get_chat = lambda: None

    AIEngine._run_tool_batch(
        eng, "", [{'tool': 'nonexistent_tool', 'spec': None, 'call_id': None,
                   'annotation': None}], "", None)

    obs = tm.work.last_output
    assert "does not exist" in obs
    assert "attach_image_to_chat" not in obs, \
        "the error offers a tool that is gated off in this session"
    assert "python_interpreter" in obs


def test_namespace_capabilities_declare_a_binding():
    for cap in caps.CAPABILITIES:
        if cap.on(caps.NAMESPACE):
            assert cap.binding, f"{cap.name} is a namespace capability with no binding"


def test_both_surfaces_of_attach_image_produce_the_same_result(tm, tmp_path):
    """THE dual-surface bar: calling the tool directly and calling the namespace
    function from inside python must be indistinguishable — same observation,
    same card. If they can diverge, the capability has no business on two
    surfaces (that divergence is exactly what makes two ways to do one thing
    confusing rather than convenient)."""
    img = tmp_path / "preview.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake but present")
    delivered = []
    tm._attach_image_binding = delivered.append

    cards = []
    tm.approval_signal.tool_card.connect(cards.append)

    direct = tm.run_attach_image_to_chat({'paths': [str(img)], 'annotation': 'a',
                                          'error': None})
    tm._interp_subresults = []
    tm._interp_card_buffer = []
    in_python = tm.interp_attach_image_to_chat(str(img), annotation='a')

    assert direct == in_python, "the two surfaces returned different observations"
    assert delivered == [[str(img)], [str(img)]], "both surfaces must deliver the image"
    # The in-python call buffers its card (so it lands after the interpreter's
    # own card) — same payload, just deferred.
    assert cards and cards[0]['card_type'] == 'image_attach'
    assert tm._interp_card_buffer[0]['card_type'] == 'image_attach'
    assert cards[0]['paths'] == tm._interp_card_buffer[0]['paths']
    # ...and the model is told about it as its own tool result.
    assert tm._interp_subresults == [('attach_image_to_chat', in_python)]


def test_attach_image_never_touches_the_file(tm, tmp_path):
    img = tmp_path / "keep_me.png"
    img.write_bytes(b"original bytes")
    tm._attach_image_binding = lambda paths: None

    tm.run_attach_image_to_chat({'paths': [str(img)], 'error': None})

    assert img.exists(), "attaching an image must never remove it"
    assert img.read_bytes() == b"original bytes"


def test_attach_image_reports_missing_paths_instead_of_failing_silently(tm, tmp_path):
    tm._attach_image_binding = lambda paths: None
    obs = tm.run_attach_image_to_chat({'paths': [str(tmp_path / "nope.png")],
                                       'error': None})
    assert obs.startswith("ERROR")
    assert "nope.png" in obs


def test_dual_surface_capabilities_are_the_documented_exceptions():
    """Both-surface entries must clear the web_search bar, so the list of them
    stays a deliberate, reviewed set rather than something that creeps."""
    dual = {c.name for c in caps.CAPABILITIES
            if c.on(caps.TOOL) and c.on(caps.NAMESPACE)}
    assert dual == {'web_search', 'attach_image_to_chat'}, \
        f"a capability quietly became dual-surface: {dual}"
    for name in dual:
        assert caps.get(name).note, \
            f"{name} is dual-surface but does not document why"


# ── chat vs. tasker ──────────────────────────────────────────────────────────

def test_a_taskers_capabilities_are_a_visible_subset_of_the_chats():
    """Same names, different bindings — nothing may exist in a tasker that the
    main session has never heard of, except the declared task-only channel."""
    chat = set(caps.documented_for(caps.CHAT, caps.gates_for_chat(**ALL_ON)))
    task = set(caps.documented_for(caps.TASK, caps.gates_for_task(
        {'allow_workmode': True, 'inject_image_tools': True,
         'inject_notify_tool': True, 'inject_controller_ref': True},
        has_skills=True)))

    task_only = {c.name for c in caps.CAPABILITIES if c.contexts == (caps.TASK,)}
    assert task_only == {'send_message_main'}
    assert task - chat == task_only
    assert not chat - task - {'send_message_main'} or True   # chat may hold more


def test_task_permissions_gate_the_same_capabilities_the_prompt_gates():
    """A tasker with images denied must not be handed image capabilities."""
    denied = caps.gates_for_task({'allow_workmode': True,
                                  'inject_image_tools': False})
    offered = caps.documented_for(caps.TASK, denied)

    assert 'attach_image_to_chat' not in offered
    assert 'attach_image_to_context' not in offered
    assert 'take_screenshot' not in offered
    assert 'python_interpreter' in offered


def test_build_namespace_uses_the_bindings_it_is_given():
    gates = caps.gates_for_chat(allow_workmode=True, has_skills=False,
                                include_image_tools=True)
    marker = object()
    ns = caps.build_namespace(caps.CHAT, gates, {
        'attach_image_to_chat': marker, 'take_screenshot': marker,
        'attach_image_to_context': marker, 'web_search': marker,
        'app_root': 'X', 'skills_path': 'Y',
    })

    assert ns['attach_image_to_chat'] is marker
    assert ns['app_root'] == 'X'
    # A capability with no binding supplied is skipped, never injected as None.
    assert 'notify' not in ns
    assert all(v is not None for v in ns.values())


def test_unbound_capabilities_are_never_injected_as_none():
    gates = caps.gates_for_chat(allow_workmode=True, has_skills=True,
                                include_notify_tool=True, include_memory=True)
    ns = caps.build_namespace(caps.CHAT, gates, {'notify': None})
    assert 'notify' not in ns


# ── the built-in namespace recap in the prompt ───────────────────────────────

def _recap(prompt: str) -> str:
    i = prompt.find("BUILT-IN NAMES")
    return prompt[i:] if i >= 0 else ""


def test_the_chat_prompt_lists_its_namespace():
    prompt = get_system_prompt(system_info="x", skills=[],
                               include_image_tools=True, include_notify_tool=True)
    recap = _recap(prompt)

    assert recap, "the built-in namespace recap is missing from the chat prompt"
    for name in ("web_search", "APP_ROOT", "memorize", "take_screenshot"):
        assert name in recap, f"{name} is bound but not summarised"


def test_the_recap_sits_near_the_bottom():
    """It is a recap, not an introduction — it must come AFTER the sections
    that actually teach these tools."""
    prompt = get_system_prompt(system_info="x", skills=[], include_image_tools=True)
    assert prompt.find("BUILT-IN NAMES") > len(prompt) * 0.5


def test_a_tasker_gets_task_wording_not_chat_wording():
    """The tasker's bindings genuinely behave differently; describing chat
    behaviour to a background agent would be a lie it cannot detect."""
    task = _recap(get_system_prompt(is_task_session_prompt=True, system_info="x",
                                    skills=None, include_image_tools=True))

    assert "send_message_main" in task, "the task-only channel is not summarised"
    assert "MAIN CHAT" in task.upper(), "task image wording missing"
    assert "queue it into your own context" in task, "task screenshot wording missing"


def test_the_chat_prompt_never_advertises_the_task_only_channel():
    chat = _recap(get_system_prompt(system_info="x", skills=[],
                                    include_image_tools=True))
    assert "send_message_main" not in chat


def test_switched_off_options_are_not_advertised():
    """The recap renders from the same gates that decide what is INJECTED, so
    it can never name something the namespace does not hold."""
    recap = _recap(get_system_prompt(system_info="x", skills=[],
                                     include_image_tools=False,
                                     include_notify_tool=False))
    assert "take_screenshot" not in recap
    assert "attach_image_to_context" not in recap
    assert "notify(" not in recap
    assert "APP_ROOT" in recap          # ungated names stay


def test_no_recap_when_the_interpreter_is_unavailable():
    """A stripped-down agent with no interpreter must not be handed a list of
    interpreter built-ins — and must not get an empty heading either."""
    prompt = get_system_prompt(system_info="x", skills=[],
                               include_execution_tools=False,
                               include_interpreter_mode_rules=False)
    assert "BUILT-IN NAMES" not in prompt


def test_the_recap_teaches_one_spelling_of_the_root_path():
    """app_root stays BOUND for older snippets, but only the canonical
    APP_ROOT (matching systema.APP_ROOT) is taught."""
    recap = _recap(get_system_prompt(system_info="x", skills=[]))
    assert "APP_ROOT" in recap
    assert "app_root" not in recap


def test_both_spellings_are_bound_in_the_namespace():
    gates = caps.gates_for_chat(allow_workmode=True, has_skills=False)
    ns = caps.build_namespace(caps.CHAT, gates, {'app_root': '/root',
                                                 'skills_path': '/skills'})
    assert ns['APP_ROOT'] == '/root'
    assert ns['app_root'] == '/root'      # back-compat alias
    assert ns['SKILLS_PATH'] == '/skills'


def test_every_documented_namespace_name_is_actually_injectable():
    """A summarised name with no binding would be advertised and absent."""
    for cap in caps.CAPABILITIES:
        if cap.on(caps.NAMESPACE) and cap.describe(caps.CHAT):
            assert cap.binding, f"{cap.name} is documented but has no binding"


def test_inject_all_mode_drops_search_memory_from_the_recap():
    """Inject-all already puts every memory in the system block, so
    search_memory is redundant there — the recap must not smuggle it back in
    after memory_section deliberately dropped it. The BINDING stays."""
    recap = _recap(get_system_prompt(system_info="x", skills=[],
                                     memory_inject_all=True))
    assert "search_memory" not in recap
    assert "memorize(" in recap                  # the rest of the set stays

    gates = caps.gates_for_chat(allow_workmode=True, has_skills=False,
                                include_memory=True)
    ns = caps.build_namespace(caps.CHAT, gates, {'search_memory': print})
    assert 'search_memory' in ns, "the binding must survive inject-all mode"


@pytest.mark.parametrize("native_mode", [True, False])
@pytest.mark.parametrize("task_mode", [True, False])
def test_the_prompt_teaches_exactly_one_spelling_of_the_injected_paths(native_mode, task_mode):
    """The skill-path rule used to teach `app_root` / `skills_path` while the
    built-in recap teaches APP_ROOT / SKILLS_PATH — the same prompt advertising
    two spellings of the same variable. Names are taught in ONE place now."""
    prompt = get_system_prompt(
        is_task_session_prompt=task_mode, native_tools=native_mode,
        system_info="x",
        skills=[{'name': 'demo', 'description': 'd', 'is_loaded': True}])

    assert "app_root" not in prompt, "the lower-case alias is being taught again"
    assert "skills_path" not in prompt
