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


def _web_search_body(args):
    out = [str(args.get('query', '') or args.get('url', '') or '').strip()]
    mode = str(args.get('mode', '') or '').strip().lower()
    if mode and mode != 'search':
        out.append(f"mode: {mode}")
    if args.get('max_results') not in (None, ''):
        out.append(f"max_results: {int(args['max_results'])}")
    if args.get('fetch_top') not in (None, '', 0):
        out.append(f"fetch_top: {int(args['fetch_top'])}")
    if args.get('offset') not in (None, '', 0):
        out.append(f"offset: {int(args['offset'])}")
    return "\n".join(out)


def _attach_image_body(args):
    paths = args.get('paths') or args.get('path') or ''
    if isinstance(paths, str):
        paths = [p for p in paths.splitlines() if p.strip()]
    return "\n".join(str(p).strip() for p in paths if str(p).strip())


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
    'load_skill': {
        'description': (
            "Load a skill's full instructions into your context. Use when the task matches an "
            "available skill that is not already loaded. May be emitted alongside other tool "
            "calls in the same response — the skill loads before they run."
        ),
        'param': ('skill_name', 'The exact name of the skill to load.'),
        'extra_params': [
            ('message_to_user',
             "Optional fallback. Normally just write your reply as normal text alongside this "
             "call — it is shown to the user. Only put words here if you won't emit any text "
             "this turn.",
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
    'web_search': {
        'description': (
            "Search the web and read pages — your built-in, no-API-key research tool. "
            "Modes: 'search' (a query -> ranked results), 'open' (a URL -> clean readable "
            "page text), 'links' (a URL -> its outgoing links). Use PRECISE queries and "
            "open only the pages you actually need — do calculated, structured exploration, "
            "never broad dumps, to keep your context lean. You MAY call it several times in "
            "one response (e.g. parallel searches). When you use information from a page, "
            "CITE it inline in your reply as [short label](https://the-url)."
        ),
        'param': ('query', "The search query (mode=search) OR the URL to open/extract "
                           "(mode=open / mode=links)."),
        'extra_params': [
            ('mode', "'search' (default), 'open', or 'links'.", False,
                     ('search', 'open', 'links')),
            ('max_results', "search mode: how many results to return (default 8).", False),
            ('fetch_top', "search mode: ALSO auto-open the top N result pages in this same "
                          "call (default 0 = just the list). Use sparingly — each opened page "
                          "adds a lot of context; prefer opening specific results deliberately.",
                          False),
            ('offset', "open mode: character position to start reading from (default 0). A long "
                       "page comes back in windows — when the result says more is available, "
                       "call again with the SAME url and the offset it gives you to read on.",
                       False),
            ('annotation',
             "A short 3-6 word label (e.g. 'Searching RTX 5090 benchmarks'). ALWAYS include "
             "it — shown to the user as this step's title.", False),
        ],
        'exec': False,
        'to_fence': _web_search_body,
        'compat': {
            'table_row': "web_search: Search the web / read a page (no API key)",
            'fence_example': (
                "```web_search: [Searching RTX 5090 benchmarks]\n"
                "rtx 5090 benchmarks\n"
                "mode: search\n"
                "max_results: 5\n"
                "```"
            ),
            'usage': ("Line 1 = the query (search) or the URL (open / links). Optional opt "
                      "lines: 'mode:' (search|open|links), 'max_results:', 'fetch_top:'. "
                      "Read-only, no approval. May be batched. Cite pages you use as "
                      "[label](url)."),
        },
    },
    'attach_image_to_chat': {
        'description': (
            "Show one or more images to the USER by pinning them to your reply — previews, "
            "screenshots, generated designs, charts. One absolute path per line. The files "
            "are NOT modified or deleted. Use this whenever you produced or found an image "
            "the user should actually see; do not tell them to open a file themselves."
        ),
        'param': ('paths', "Absolute image path(s) to show the user — one per line."),
        'extra_params': [
            ('annotation',
             "A short 3-6 word label (e.g. 'Showing the rendered preview'). ALWAYS include "
             "it — shown to the user as this step's title.", False),
        ],
        'exec': False,
        'to_fence': _attach_image_body,
        'compat': {
            'table_row': "attach_image_to_chat: Show image(s) to the user in chat",
            'fence_example': (
                "```attach_image_to_chat: [Showing the rendered preview]\n"
                "C:/Users/you/Desktop/preview.png\n"
                "```"
            ),
            'usage': ("One absolute image path per line. Read-only, no approval. The source "
                      "files are left untouched."),
        },
    },
    'ask_user': {
        'description': (
            "ASK THE USER a structured multiple-choice question and WAIT for their answer. "
            "Use this instead of guessing whenever the request is ambiguous, whenever two "
            "readings would lead to materially different work, or before starting anything "
            "large. Options are CHECKBOXES by default -- the user ticks as many as apply -- "
            "so within one question every option must be ADDITIVE: it has to make sense for "
            "them to tick all of them at once. If two options are genuine alternatives, that "
            "is not a question; decide it yourself and say why. Every question also gets a "
            "free-text 'Other' box automatically, so never add one as an option. Their "
            "answers come back to you as a Q:/A: block."
        ),
        'param': ('questions',
                  "The questions to ask. Either a JSON list of "
                  "{question, header, multiSelect, options:[{label, description}]} objects, "
                  "or the line format: a 'Q: <text>' line per question, optional 'header:' "
                  "and 'multi: true|false' lines under it, then one '- Label | description' "
                  "line per option. Blank line between questions. Max 4 questions, 2-8 "
                  "options each, and give every option a real description -- the user reads "
                  "those to choose."),
        'extra_params': [
            ('annotation',
             "A short 3-6 word label (e.g. 'Clarifying the deploy target'). ALWAYS include "
             "it -- shown to the user as this step's title.", False),
            ('message_to_user',
             "Optional fallback. Normally write any framing as normal text alongside this "
             "call. Only put words here if you won't emit any text this turn.", False),
        ],
        'exec': False,
        'compat': {
            'table_row': "ask_user: Ask the user a multiple-choice question and wait",
            'fence_example': (
                "```ask_user: [Clarifying the deploy target]\n"
                "Q: Which environments should the migration run against?\n"
                "header: Deploy target\n"
                "multi: true\n"
                "- Staging | Safe to break; mirrors the prod schema.\n"
                "- Production | Live data. Requires the backup step first.\n"
                "- Local docker | Fast iteration, no network calls.\n"
                "\n"
                "Q: What should happen if a migration fails halfway?\n"
                "- Roll back automatically | Safest; loses the partial progress.\n"
                "- Stop and wait | Leaves the DB mid-migration for inspection.\n"
                "```"
            ),
            'usage': ("One 'Q:' line per question, '- Label | description' per option, "
                      "blank line between questions. 'header:' and 'multi:' are optional "
                      "per question (multi defaults to true). An 'Other' free-text box is "
                      "added for you. Max 4 questions. The turn WAITS for the answer."),
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
# set_session_name retired 2026-07-19 (#31): naming is the background
# SessionNamerAgent's job now — controller.set_session_name() stays as the
# rename primitive it (and the sidebar) calls.
LEGACY_STRIP_KEYS = ('execute_code', 'set_session_name')
