"""
systema/execution/capabilities.py

THE manifest of what an agent can actually do — one declarative table that
decides, for every capability, WHERE it is offered (tool call / python
namespace), IN WHICH context (main chat / background tasker), and UNDER WHICH
gate. Stdlib-only, like tool_registry.py.

Why this exists
---------------
What the agent was PROMISED and what it was actually GIVEN used to be
maintained in separate hand-written places, and they drifted:

  * `ToolManager.get_canonical_tools()` (native schemas) and
    `prompts/compat.py:tool_format_section()` (compat tool table) were two
    hardcoded lists. The file subsystem was added to the compat one only, so in
    NATIVE mode `read_file`/`edit_file`/`write_file`/`grep` had no schemas at
    all — while the native system prompt happily taught them. Every attempt
    came back "Tool 'read_file' does not exist and was NOT run".
  * `attach_image_to_chat` was documented as a tool but existed only as a python
    namespace function — the same failure from the other direction.
  * The main chat and the tasker hand-injected DIFFERENT namespaces, with gates
    that disagreed with the prompt's own gating (notify/memory injected
    unconditionally into tasks while the prompt hid them).

So: `tool_registry.CANONICAL_TOOLS` remains the single source of truth for tool
SCHEMAS and compat docs; this module is the layer above it that answers "is this
offered here, right now?". Every advertise list, every namespace injection and
every prompt section renders from these queries — parity is structural, not a
convention someone has to remember.

The both-surface rule (set by `web_search`)
-------------------------------------------
A capability may occupy BOTH surfaces only if the namespace call routes through
the same dispatcher and produces the same card and the same observation as the
tool call (see `ToolManager.interp_web_search` -> `_interp_subresults` ->
`emit_tool_card`). Anything that cannot meet that bar is single-surface, so the
agent never has two ways to do one thing that behave differently.
"""
from dataclasses import dataclass, field

# ── contexts ─────────────────────────────────────────────────────────────────
CHAT = 'chat'          # the user's live session
TASK = 'task'          # a background tasker sub-agent (its own AIEngine)
CONTEXTS = (CHAT, TASK)

# ── surfaces ─────────────────────────────────────────────────────────────────
TOOL = 'tool'          # a real tool call (native schema + compat fence)
NAMESPACE = 'namespace'  # a function inside the python_interpreter namespace
SURFACES = (TOOL, NAMESPACE)

# ── gates ────────────────────────────────────────────────────────────────────
# Canonical gate names. Each context maps its own flags onto these (see
# gates_for_chat / gates_for_task) so a capability declares ONE condition
# instead of knowing about `allow_workmode` vs `permissions.inject_image_tools`.
G_WORKMODE = 'workmode'
G_SKILLS = 'skills'
G_IMAGES = 'images'
G_NOTIFY = 'notify'
G_MEMORY = 'memory'
G_CONTROLLER = 'controller_ref'
G_MAIN_CHANNEL = 'main_channel'   # a tasker's link back to the user's chat


@dataclass(frozen=True)
class Capability:
    """One thing the agent can do, and where it is offered."""
    name: str
    surfaces: tuple
    contexts: tuple = CONTEXTS
    gate: str = ''                 # '' = always offered in its contexts
    binding: str = ''              # namespace surface: key the binder supplies
    note: str = ''                 # declared divergence / design intent
    # Agent-facing one-liners for the namespace summary in the system prompt.
    # `summary` is the default; `task_summary` overrides it where a tasker's
    # binding genuinely behaves differently, so the prompt can never describe
    # chat behaviour to a background agent.
    summary: str = ''
    task_summary: str = ''
    signature: str = ''            # how it is called, e.g. "notify(title, msg)"

    def offered_in(self, context: str, gates: dict) -> bool:
        if context not in self.contexts:
            return False
        return True if not self.gate else bool((gates or {}).get(self.gate))

    def on(self, surface: str) -> bool:
        return surface in self.surfaces

    def describe(self, context: str = CHAT) -> str:
        """The one-liner for this context ('' when undocumented)."""
        if context == TASK and self.task_summary:
            return self.task_summary
        return self.summary


# ── THE MANIFEST ─────────────────────────────────────────────────────────────
# Declaration order IS presentation order in the prompt and the schema list.
CAPABILITIES = (
    # ── tool surface: execution + the file subsystem ─────────────────────────
    Capability('python_interpreter', (TOOL,), gate=G_WORKMODE),
    Capability('read_file', (TOOL,), gate=G_WORKMODE,
               note="File subsystem rides with execution capability (default-on)."),
    Capability('edit_file', (TOOL,), gate=G_WORKMODE),
    Capability('write_file', (TOOL,), gate=G_WORKMODE),
    Capability('grep', (TOOL,), gate=G_WORKMODE),

    # ── both surfaces (the web_search bar) ───────────────────────────────────
    Capability('web_search', (TOOL, NAMESPACE), gate=G_WORKMODE,
               binding='web_search',
               signature="web_search(query, mode='search'|'open'|'links', offset=0)",
               summary="Search the web or read a page. Same tool as the direct "
                       "call — from here it still gets its own result card.",
               note="Reference dual-surface capability: the in-python call routes "
                    "through interp_web_search so it spawns the same card and comes "
                    "back as its own tool result."),
    Capability('attach_image_to_chat', (TOOL, NAMESPACE), gate=G_IMAGES,
               binding='attach_image_to_chat',
               signature="attach_image_to_chat(path_or_paths)",
               summary="Show image(s) to the user. Use when you built the path in "
                       "this same step; otherwise call the tool directly.",
               task_summary="Show image(s) to the USER'S MAIN CHAT (a background "
                            "task has no chat of its own).",
               note="TASK BINDING DIFFERS: a tasker has no chat turn of its own, so "
                    "its images are delivered into the user's MAIN chat."),

    # ── tool surface: skills ─────────────────────────────────────────────────
    Capability('load_skill', (TOOL,), gate=G_SKILLS),
    Capability('unload_skill', (TOOL,), gate=G_SKILLS),

    # ── namespace surface ────────────────────────────────────────────────────
    Capability('attach_image_to_context', (NAMESPACE,), gate=G_IMAGES,
               binding='attach_image_to_context',
               signature="attach_image_to_context(path)",
               summary="Feed ONE image into your OWN context for the next step "
                       "(private; the file on disk is never touched).",
               note="Private one-turn vision context. NEVER deletes the source file."),
    Capability('take_screenshot', (NAMESPACE,), gate=G_IMAGES,
               binding='take_screenshot',
               signature="take_screenshot(save_path=None)",
               summary="Capture the screen to data/temp/ and return the path. "
                       "Does NOT attach it — pass the path on yourself.",
               task_summary="Capture the screen AND queue it into your own context "
                            "for the next step; returns the path.",
               note="TASK BINDING DIFFERS: the tasker's capture auto-queues itself "
                    "into that task's own context; the chat one only returns a path."),
    Capability('notify', (NAMESPACE,), gate=G_NOTIFY, binding='notify',
               signature="notify(title, message)",
               summary="Raise a desktop notification."),
    Capability('memorize', (NAMESPACE,), gate=G_MEMORY, binding='memorize',
               signature="memorize(text)",
               summary="Store a long-term memory."),
    Capability('search_memory', (NAMESPACE,), gate=G_MEMORY, binding='search_memory',
               signature="search_memory(query)",
               summary="Search long-term memories.",
               note="Prompt-level nuance: inject-all recall mode stops DOCUMENTING "
                    "this (every memory is already in the block); the binding stays."),
    Capability('update_memory', (NAMESPACE,), gate=G_MEMORY, binding='update_memory',
               signature="update_memory(memory_id_or_title, new_text)",
               summary="Rewrite an existing memory."),
    Capability('forget_memory', (NAMESPACE,), gate=G_MEMORY, binding='forget_memory',
               signature="forget_memory(memory_id_or_title)",
               summary="Delete a memory."),
    Capability('send_message_main', (NAMESPACE,), contexts=(TASK,),
               gate=G_MAIN_CHANNEL, binding='send_message_main',
               signature="send_message_main(message)",
               task_summary="Send a message to the user's main chat — your ONLY "
                            "way to reach them. Delivered immediately.",
               note="Task-only: the ONE supported way for a tasker to reach the user."),
    Capability('controller', (NAMESPACE,), gate=G_CONTROLLER, binding='controller',
               signature="controller",
               summary="Live app object (settings, windows, sessions). Powerful — "
                       "read before you write.",
               note="Raw app handle — powerful, so it is gated in both contexts."),
    # Canonical spelling matches the codebase constant (systema.APP_ROOT), so a
    # path written in a snippet reads the same as one written in the app. The
    # lower-case aliases are kept bound for older skills/snippets but are NOT
    # advertised, so the prompt teaches exactly one spelling.
    Capability('APP_ROOT', (NAMESPACE,), binding='app_root',
               signature="APP_ROOT",
               summary="Absolute path to the app root, as a string. Build every "
                       "app path from this — it is immune to os.chdir()."),
    Capability('SKILLS_PATH', (NAMESPACE,), binding='skills_path',
               signature="SKILLS_PATH",
               summary="Absolute path to the skills folder."),
    Capability('app_root', (NAMESPACE,), binding='app_root',
               note="Back-compat alias of APP_ROOT — bound, never documented."),
    Capability('skills_path', (NAMESPACE,), binding='skills_path',
               note="Back-compat alias of SKILLS_PATH — bound, never documented."),
)

BY_NAME = {c.name: c for c in CAPABILITIES}


# ── queries (the only public entry points) ───────────────────────────────────

def get(name: str):
    """The Capability with this name, or None."""
    return BY_NAME.get(name)


def _offered(context: str, gates: dict, surface: str) -> list:
    return [c for c in CAPABILITIES
            if c.on(surface) and c.offered_in(context, gates)]


def tools_for(context: str = CHAT, gates: dict = None) -> list:
    """Tool NAMES offered here — drives BOTH the native schema list and the
    compat tool table, so the two can never disagree again."""
    return [c.name for c in _offered(context, gates or {}, TOOL)]


def namespace_for(context: str = CHAT, gates: dict = None) -> list:
    """Capabilities to inject into the python namespace here."""
    return _offered(context, gates or {}, NAMESPACE)


def documented_for(context: str = CHAT, gates: dict = None) -> list:
    """Every capability name the prompt is allowed to teach in this context.
    Anything a prompt names that is NOT in here is a promise the agent cannot
    keep — which is exactly the bug class this module exists to kill."""
    seen, out = set(), []
    for surface in SURFACES:
        for c in _offered(context, gates or {}, surface):
            if c.name not in seen:
                seen.add(c.name)
                out.append(c.name)
    return out


def build_namespace(context: str, gates: dict, bindings: dict) -> dict:
    """The namespace dict for this context: every offered NAMESPACE capability,
    resolved through `bindings` (name -> callable/value supplied by the caller).

    Same names in both worlds, different implementations — a capability can
    never silently mean something different in a tasker than it does in chat,
    because the manifest names it once and only the binding varies.
    A capability with no binding supplied is skipped (never injected as None).
    """
    ns = {}
    for cap in namespace_for(context, gates):
        key = cap.binding or cap.name
        if key in bindings and bindings[key] is not None:
            ns[cap.name] = bindings[key]
    return ns


# ── gate mapping per context ─────────────────────────────────────────────────

def gates_for_chat(*, allow_workmode: bool, has_skills: bool,
                   include_image_tools: bool = False,
                   include_notify_tool: bool = False,
                   include_memory: bool = True,
                   include_controller_ref: bool = False) -> dict:
    """Main-session flags -> canonical gates."""
    return {
        G_WORKMODE: bool(allow_workmode),
        G_SKILLS: bool(has_skills),
        G_IMAGES: bool(include_image_tools),
        G_NOTIFY: bool(include_notify_tool),
        G_MEMORY: bool(include_memory),
        G_CONTROLLER: bool(include_controller_ref),
        G_MAIN_CHANNEL: False,       # chat IS the main channel
    }


def gates_for_task(permissions: dict, *, has_skills: bool = False,
                   has_main_channel: bool = True,
                   include_memory: bool = True) -> dict:
    """Task permission flags -> the SAME canonical gates, so a tasker's offered
    set is a visible subset of the chat's rather than whatever happened to get
    injected."""
    p = permissions or {}
    return {
        G_WORKMODE: bool(p.get('allow_workmode', False)),
        G_SKILLS: bool(has_skills),
        G_IMAGES: bool(p.get('inject_image_tools', False)),
        G_NOTIFY: bool(p.get('inject_notify_tool', False)),
        G_MEMORY: bool(include_memory),
        G_CONTROLLER: bool(p.get('inject_controller_ref', False)),
        G_MAIN_CHANNEL: bool(has_main_channel),
    }
