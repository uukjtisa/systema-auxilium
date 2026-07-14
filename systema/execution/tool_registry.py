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
  to_fence       optional callable(args) -> fence BODY string, for multi-arg
                 tools whose native arguments must be recomposed into the
                 compat body (the file tools). Single-param tools omit it.

ADDING A TOOL is a 3-touch change:
  1. Add its entry here — the native schema AND the compat fence docs render
     from this registry automatically, in both tool-calling modes.
  2. Add a parse_<tool>() method on ToolManager (mirrors parse_python_interpreter).
  3. Add one dispatch branch in AIEngine._process_ai_response.

RETIRED tools live in LEGACY_STRIP_KEYS: their fences in OLD sessions are
still stripped for display, but they are never advertised, parsed as
dispatchable, or included in native schemas. (execute_code was retired in
2026-07 — python_interpreter is the only way to run code.)
"""

def _read_file_body(args):
    body = str(args.get('path', '') or '').strip()
    start = args.get('start_line')
    count = args.get('max_lines')
    if start or count:
        s = int(start or 1)
        body += f"\nlines: {s}-{s + int(count or 200) - 1}"
    return body


def _edit_file_body(args):
    lines = [str(args.get('path', '') or '').strip()]
    if args.get('replace_all'):
        lines.append("flags: all")
    start, end = args.get('start_line'), args.get('end_line')
    if start and end and not (args.get('old_text') or '').strip():
        # line-range fallback form: no OLD block, remainder is the new text
        lines.append(f"lines: {int(start)}-{int(end)}")
        lines.append(str(args.get('new_text', '') or ''))
        return "\n".join(lines)
    lines.append("<<<<<<< OLD")
    lines.append(str(args.get('old_text', '') or ''))
    lines.append("=======")
    lines.append(str(args.get('new_text', '') or ''))
    lines.append(">>>>>>> NEW")
    return "\n".join(lines)


def _write_file_body(args):
    return (str(args.get('path', '') or '').strip() + "\n"
            + str(args.get('content', '') or ''))


def _grep_body(args):
    out = [str(args.get('pattern', '') or '').strip()]
    for key in ('path', 'glob', 'type'):
        v = str(args.get(key, '') or '').strip()
        if v:
            out.append(f"{key}: {v}")
    om = str(args.get('output_mode', '') or '').strip()
    if om and om != 'files_with_matches':
        out.append(f"output: {om}")
    if args.get('case_insensitive'):
        out.append("case: true")
    if args.get('line_numbers') is False:
        out.append("line_numbers: false")
    for key in ('before', 'after', 'context'):
        if args.get(key):
            out.append(f"{key}: {int(args[key])}")
    if args.get('only_matching'):
        out.append("only_matching: true")
    if args.get('multiline'):
        out.append("multiline: true")
    if args.get('head_limit') not in (None, ''):
        out.append(f"head_limit: {int(args['head_limit'])}")
    if args.get('ignore_common') is False:
        out.append("ignore_common: false")
    return "\n".join(out)


CANONICAL_TOOLS = {
    'python_interpreter': {
        'description': (
            "Run Python in a persistent workspace and SEE its output (stdout + the last "
            "expression's value). The ONLY way to run code. Use it for everything: reading "
            "files, calculations, gathering data, launching apps (start them detached), and "
            "multi-step tasks where you observe results before continuing. The namespace "
            "persists across calls within a interpreter session."
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
            'table_row': "python_interpreter: Python code to run (you SEE output)",
            'fence_example': (
                "```python_interpreter: [Brief label of what this block does]\n"
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
             "response: name the tool to also run this turn ('python_interpreter'). Prefer "
             "emitting set_session_name and the work call as two parallel calls instead.",
             False, ('python_interpreter',)),
            ('then_code',
             "The Python code for the tool named in then_tool. Required when then_tool is set.",
             False),
            ('then_annotation',
             "For then_tool='python_interpreter': a short 3-6 word label of what the chained "
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
                      "it may accompany a python_interpreter fence in the same response."),
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
             "response: run ONE code tool right after the skill loads ('python_interpreter').",
             False, ('python_interpreter',)),
            ('then_code',
             "The Python code for the tool named in then_tool. Required when then_tool is set.",
             False),
            ('then_annotation',
             "For then_tool='python_interpreter': a short 3-6 word label of what the chained "
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
    # ── The file-editing subsystem (part of work mode, default-on) ───────────
    'read_file': {
        'description': (
            "Read a window of a file as NUMBERED lines. Part of your work-mode file "
            "subsystem: prefer it over Python file I/O for inspecting files — it is "
            "surgical (windowed, numbered) and never breaks your work-mode state. "
            "ALWAYS read a file before editing it."
        ),
        'param': ('path', 'Absolute (or working-dir-relative) path of the file to read.'),
        'extra_params': [
            ('start_line', "First line to read (1-based). Default 1.", False),
            ('max_lines', "How many lines to return. Default 200.", False),
            ('annotation',
             "A short 3-6 word label (e.g. 'Reading config file'). ALWAYS include it — "
             "shown to the user as this step's title.", False),
        ],
        'exec': True,
        'to_fence': _read_file_body,
        'compat': {
            'table_row': "read_file: Read a file as numbered lines (windowed)",
            'fence_example': (
                "```read_file: [Reading the config]\n"
                "D:/project/config.py\n"
                "lines: 1-200\n"
                "```"
            ),
            'usage': ("Line 1 = the path. Optional 'lines: A-B' window. Output is "
                      "numbered so your edits can anchor precisely."),
        },
    },
    'edit_file': {
        'description': (
            "Surgically edit a file by EXACT anchor match: old_text must match the "
            "current file content exactly and uniquely (read the file first!). Part of "
            "your work-mode file subsystem — never breaks your work-mode state. "
            "Fallback: give start_line/end_line (and no old_text) to replace a line range."
        ),
        'param': ('path', 'Absolute (or working-dir-relative) path of the file to edit.'),
        'extra_params': [
            ('old_text', "The EXACT current text to replace (copy it verbatim from a "
                         "read_file window — whitespace matters).", False),
            ('new_text', "The replacement text.", True),
            ('replace_all', "Replace every occurrence of old_text (default: the match "
                            "must be unique).", False),
            ('start_line', "Line-range fallback: first line to replace (1-based), used "
                           "with end_line when old_text is omitted.", False),
            ('end_line', "Line-range fallback: last line to replace (inclusive).", False),
            ('annotation',
             "A short 3-6 word label (e.g. 'Fixing import path'). ALWAYS include it — "
             "shown to the user as this step's title.", False),
        ],
        'exec': True,
        'to_fence': _edit_file_body,
        'compat': {
            'table_row': "edit_file: Replace exact text in a file (anchor edit)",
            'fence_example': (
                "```edit_file: [Fixing the timeout value]\n"
                "D:/project/config.py\n"
                "<<<<<<< OLD\n"
                "TIMEOUT = 5\n"
                "=======\n"
                "TIMEOUT = 30\n"
                ">>>>>>> NEW\n"
                "```"
            ),
            'usage': ("Line 1 = the path, then the OLD/NEW marker block. The OLD text "
                      "must match the file exactly and uniquely. Add a 'flags: all' "
                      "line after the path to replace every occurrence. Range fallback: "
                      "'lines: 40-52' after the path plus ONLY the new text (no markers)."),
        },
    },
    'write_file': {
        'description': (
            "Create or overwrite a file with verbatim content. Part of your work-mode "
            "file subsystem — never breaks your work-mode state. Prefer edit_file for "
            "changing existing files; write_file is for NEW files or full rewrites."
        ),
        'param': ('path', 'Absolute (or working-dir-relative) path of the file to write.'),
        'extra_params': [
            ('content', "The complete file content, verbatim.", True),
            ('annotation',
             "A short 3-6 word label (e.g. 'Creating report script'). ALWAYS include "
             "it — shown to the user as this step's title.", False),
        ],
        'exec': True,
        'to_fence': _write_file_body,
        'compat': {
            'table_row': "write_file: Create or overwrite a file with content",
            'fence_example': (
                "```write_file: [Creating the report script]\n"
                "D:/project/report.py\n"
                "import json\n"
                "print('hello')\n"
                "```"
            ),
            'usage': ("Line 1 = the path; EVERYTHING from line 2 on is the file "
                      "content, verbatim."),
        },
    },
    'grep': {
        'description': (
            "Search file CONTENTS for a regular-expression pattern (ripgrep-style). "
            "Your fast code/text search — use it to LOCATE things across a tree before "
            "reading or editing. Read-only; part of the work-mode file subsystem, never "
            "breaks work-mode state. Three output modes; skips build/VCS dirs by default."
        ),
        'param': ('pattern', 'The regular expression to search for (full regex syntax).'),
        'extra_params': [
            ('path', "File or directory to search under. Default: the working dir.", False),
            ('glob', "Glob to filter which files are searched, e.g. '**/*.py' or '*.{ts,tsx}'.", False),
            ('type', "File-type filter, e.g. 'py', 'js', 'kotlin', 'go', 'rust'.", False),
            ('output_mode', "'files_with_matches' (default — matching file paths), 'content' "
                            "(matching lines), or 'count' (per-file match counts).", False,
                            ('files_with_matches', 'content', 'count')),
            ('case_insensitive', "Case-insensitive match (default false).", False),
            ('line_numbers', "Show line numbers in content mode (default true).", False),
            ('before', "Lines of context BEFORE each match (content mode).", False),
            ('after', "Lines of context AFTER each match (content mode).", False),
            ('context', "Lines of context BOTH before and after each match (content mode).", False),
            ('only_matching', "Print only the matched part of each line (content mode).", False),
            ('multiline', "Let the pattern span multiple lines (. matches newlines).", False),
            ('head_limit', "Limit output to the first N results (default 250; 0 = unlimited).", False),
            ('ignore_common', "Skip common build/VCS dirs like .git / __pycache__ / node_modules "
                              "/ .venv (default true). Set false to search everything.", False),
            ('annotation',
             "A short 3-6 word label (e.g. 'Searching for callers'). ALWAYS include it — "
             "shown to the user as this step's title.", False),
        ],
        'exec': True,
        'to_fence': _grep_body,
        'compat': {
            'table_row': "grep: Search file contents by regex (ripgrep-style)",
            'fence_example': (
                "```grep: [Finding callers of send]\n"
                "\\bsend\\(\n"
                "glob: **/*.py\n"
                "output: content\n"
                "context: 1\n"
                "```"
            ),
            'usage': ("Line 1 = the regex pattern. Optional opt lines: 'path:', 'glob:', "
                      "'type:', 'output:' (files_with_matches|content|count), 'case: true', "
                      "'before:'/'after:'/'context:', 'only_matching: true', "
                      "'multiline: true', 'head_limit:', 'ignore_common: false'. "
                      "Default mode lists matching file paths."),
        },
    },
}

# Tools that form the file-editing subsystem (used for prompt grouping and the
# file-op UI cards). grep rides with them in prompts but is read-only and
# has no diff card, so it is NOT in FILE_TOOL_KEYS.
FILE_TOOL_KEYS = ('read_file', 'edit_file', 'write_file')

# Code-execution tools — subject to the single-exec policy and capability gates.
EXEC_TOOL_KEYS = frozenset(k for k, v in CANONICAL_TOOLS.items() if v.get('exec'))

# Retired tools: strip-only. Old sessions containing these fences still render
# cleanly, and an unclosed legacy fence still gets auto-closed — but they are
# never advertised, dispatched, or schema'd.
LEGACY_STRIP_KEYS = ('execute_code',)
