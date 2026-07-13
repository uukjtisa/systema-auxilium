"""
systema/execution/tool_registry.py

The single source of truth for the AI's canonical tools — pure data, stdlib
only (no PyQt), so both the ToolManager and the prompt modules
(systema.engine.prompts.*) can import it headlessly.

Each entry carries:
  description    native function-calling schema description
  param          (name, description) of the single required string parameter
  extra_params   [(name, description, required[, enum]), ...] optional args
  exec           True = a code-execution tool (subject to the single-exec
                 policy and the supervised-execution gate)
  compat         prompt-side rendering bits for compat (fence) mode:
                   table_row      one-liner for the tools summary table
                   fence_example  the canonical fence example
                   usage          short per-tool usage note

ADDING A TOOL is a 3-touch change:
  1. Add its entry here — the native schema AND the compat fence docs render
     from this registry automatically, in both tool-calling modes.
  2. Add a parse_<tool>() method on ToolManager (mirrors parse_work_environment).
  3. Add one dispatch branch in AIEngine._process_ai_response.

RETIRED tools live in LEGACY_STRIP_KEYS: their fences in OLD sessions are
still stripped for display, but they are never advertised, parsed as
dispatchable, or included in native schemas. (execute_code was retired in
2026-07 — work_environment is the only way to run code.)
"""

CANONICAL_TOOLS = {
    'work_environment': {
        'description': (
            "Run Python in a persistent workspace and SEE its output (stdout + the last "
            "expression's value). The ONLY way to run code. Use it for everything: reading "
            "files, calculations, gathering data, launching apps (start them detached), and "
            "multi-step tasks where you observe results before continuing. The namespace "
            "persists across calls within a work session."
        ),
        'param': ('code', 'The Python code to execute. You receive its stdout and return value.'),
        'extra_params': [
            ('annotation',
             "A short 3-6 word label describing what this code does (e.g. 'Reading config "
             "file', 'Counting desktop files'). ALWAYS include it — it is shown to the user "
             "as this step's title.",
             False),
            ('message_to_user',
             "Optional fallback. Normally just write your reply as normal text alongside this "
             "call — it is shown to the user. Only put words here if you won't emit any text "
             "this turn.",
             False),
        ],
        'exec': True,
        'compat': {
            'table_row': "work_environment: Python code to run (you SEE output)",
            'fence_example': (
                "```work_environment: [Brief label of what this block does]\n"
                "import os\n"
                "print(os.listdir('.'))\n"
                "```"
            ),
            'usage': "Your one execution tool — run code, see output, chain turns.",
        },
    },
    'set_session_name': {
        'description': (
            "Set a short, descriptive title for the current conversation. Emit it ALONGSIDE "
            "your other tool call in the same response when your provider supports parallel "
            "calls; use then_tool/then_code only if it does not."
        ),
        'param': ('name', 'A short title — just a few words.'),
        'extra_params': [
            ('message_to_user',
             "Optional fallback. Just write your reply as normal text this turn — naming the "
             "session runs quietly in the background. Only put words here if you won't emit any "
             "text this turn.",
             False),
            ('then_tool',
             "FALLBACK chaining, for providers that only permit a single tool call per "
             "response: name the tool to also run this turn ('work_environment'). Prefer "
             "emitting set_session_name and the work call as two parallel calls instead.",
             False, ('work_environment',)),
            ('then_code',
             "The Python code for the tool named in then_tool. Required when then_tool is set.",
             False),
            ('then_annotation',
             "For then_tool='work_environment': a short 3-6 word label of what the chained "
             "code does (shown to the user as that step's title).",
             False),
        ],
        'exec': False,
        'compat': {
            'table_row': "set_session_name: Short title for this conversation",
            'fence_example': (
                "```set_session_name\n"
                "Chat About File Sorting\n"
                "```"
            ),
            'usage': ("Name the session once, early. Exempt from the one-code-tool rule — "
                      "it may accompany a work_environment fence in the same response."),
        },
    },
    'load_skill': {
        'description': (
            "Load a skill's full instructions into your context. Use when the task matches an "
            "available skill that is not already loaded."
        ),
        'param': ('skill_name', 'The exact name of the skill to load.'),
        'extra_params': [
            ('message_to_user',
             "Optional fallback. Normally just write your reply as normal text alongside this "
             "call — it is shown to the user. Only put words here if you won't emit any text "
             "this turn.",
             False),
            ('then_tool',
             "FALLBACK chaining, for providers that only permit a single tool call per "
             "response: run ONE code tool right after the skill loads ('work_environment').",
             False, ('work_environment',)),
            ('then_code',
             "The Python code for the tool named in then_tool. Required when then_tool is set.",
             False),
            ('then_annotation',
             "For then_tool='work_environment': a short 3-6 word label of what the chained "
             "code does (shown to the user as that step's title).",
             False),
        ],
        'exec': False,
        'compat': {
            'table_row': "load_skill: Load a skill's instructions",
            'fence_example': (
                "```load_skill\n"
                "skill_name\n"
                "```"
            ),
            'usage': "Loads the skill's instructions into your system context.",
        },
    },
    'unload_skill': {
        'description': "Remove a previously loaded skill from your context to free space.",
        'param': ('skill_name', 'The exact name of the skill to unload.'),
        'exec': False,
        'compat': {
            'table_row': "unload_skill: Unload a loaded skill",
            'fence_example': (
                "```unload_skill\n"
                "skill_name\n"
                "```"
            ),
            'usage': "Removes the skill from your active context.",
        },
    },
}

# Code-execution tools — subject to the single-exec policy and capability gates.
EXEC_TOOL_KEYS = frozenset(k for k, v in CANONICAL_TOOLS.items() if v.get('exec'))

# Retired tools: strip-only. Old sessions containing these fences still render
# cleanly, and an unclosed legacy fence still gets auto-closed — but they are
# never advertised, dispatched, or schema'd.
LEGACY_STRIP_KEYS = ('execute_code',)
