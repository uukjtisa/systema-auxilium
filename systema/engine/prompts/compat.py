'''
systema/engine/prompts/compat.py

Compat (fence) tool-calling renderings: the fence format section, the per-mode
HOW-TO tails appended to shared cores, the compat prefill primer, and the
compat injected prompts. Tool docs render FROM systema.execution.tool_registry
— adding a tool there updates the table and examples automatically.

The assembled compat prompt contains EXACTLY ONE "NEVER use JSON" line (here in
the format section). PREFILLING keeps its own reminder intentionally — it is a
separate per-request primer channel, not part of the system prompt.
'''

from systema.execution.tool_registry import CANONICAL_TOOLS

# Frames a shared-core code example for fence invocation (see shared.py).
INVOKE_HINT = "run this in a python_interpreter fence — its code being:"


def tool_format_section(include_workmode: bool = True,
                        include_session_naming: bool = False,
                        include_skills: bool = False) -> str:
    """The ONE compat format section: fence rule, tool table + examples from
    the registry, the one-fence policy. (The finish rule is NOT here — the
    work section owns it.)"""
    active = []
    if include_workmode:
        # The file subsystem rides with execution capability (default-on).
        active += ['python_interpreter', 'read_file', 'edit_file', 'write_file',
                   'grep']
    if include_session_naming:
        active.append('set_session_name')
    if include_skills:
        active += ['load_skill', 'unload_skill']

    if not active:
        table = "(no interactive tools available in this context)"
        examples = ""
    else:
        rows = "\n".join(f"- {CANONICAL_TOOLS[n]['compat']['table_row']}" for n in active)
        table = "(format is  - tool name: what the fence content contains)\n" + rows
        examples = "\n\n".join(
            f"{CANONICAL_TOOLS[n]['compat']['fence_example']}" for n in active)

    rules = ""
    if include_workmode:
        rules = """
RULES:
- ONE action fence per response — never two. python_interpreter, read_file,
  edit_file and write_file all count as actions; each is one turn — wait for
  its output.
- Put the action fence at the END of your message.
- Never roleplay: if you say you'll do it, put the fence in the SAME response.
"""

    return f"""
TOOL CALL FORMAT  (CRITICAL — READ CAREFULLY)

ALL tool calls are a code fence whose language is the tool name — the content
goes directly inside. NEVER use JSON — no curly braces, no other format is
accepted.

AVAILABLE TOOLS

{table}

{examples}
{rules}"""


# Naming moved to the background SessionNamerAgent — empty tail (referenced by
# the prompt assembly + tests).
SESSION_NAMING_TAIL = ""


def skills_howto_tail() -> str:
    load_ex = CANONICAL_TOOLS['load_skill']['compat']['fence_example']
    unload_ex = CANONICAL_TOOLS['unload_skill']['compat']['fence_example']
    ind_load = "\n".join("  " + ln for ln in load_ex.splitlines())
    ind_unload = "\n".join("  " + ln for ln in unload_ex.splitlines())
    return (
        "\n"
        "HOW TO LOAD A SKILL:\n"
        "  Use the load_skill fence:\n"
        f"{ind_load}\n"
        "  The skill's full instructions will be injected into your system context.\n"
        "  Works inside AND outside python_interpreter.\n"
        "  Do NOT load a skill that already shows [LOADED] — it's already active!\n"
        "\n"
        "HOW TO UNLOAD A SKILL:\n"
        "  Use the unload_skill fence:\n"
        f"{ind_unload}\n"
        "  Removes the skill from your active context.\n"
        "  Works inside AND outside python_interpreter.\n"
        "  Do NOT unload a skill that is not [LOADED] — it isn't loaded!\n"
    )


MUST_REMEMBER = """
MUST REMEMBER (quick recall of the rules above):
- No tool needed? Just reply. Poems, explanations, chat, and anything you can
  answer from your own knowledge get a plain response — never run print() to
  "produce" text you could simply type.
- Never roleplay: if you say you'll do it, emit the fence in the SAME response.
- python_interpreter = your ONLY code tool; you SEE output; chain turns; print()
  what you need. ONE python_interpreter fence per response, at the END, ALWAYS
  with an annotation: `python_interpreter: [short label]`.
- Tool calls are code fences (tool name = fence language, content inside).
- Be friendly and descriptive.
"""


# Options tail for the injected work-continuation prompt. The finish rule is
# stated once here (this is a separate injected channel, not the system prompt).
WORK_OPTIONS = """\
Options:
- More code:
  ```python_interpreter: [Brief Description]
  your_python_code
  ```
- Finish: reply normally with your COMPLETE report and NO fence. That reply
  ends work mode and IS your final answer — its visible text must contain the
  full findings/result (never just your private reasoning), and there is no
  next turn.
- Load skill (only if not [LOADED]):
  ```load_skill
  skill_name
  ```
- Unload skill (only if [LOADED]):
  ```unload_skill
  skill_name
  ```"""


PREFILLING = {
    # Injected in order at the start of every API call; never saved to session
    # JSON. system = rule reminder; user/assistant = a fake priming exchange.
    "messages": [
        {
            "role": "system",
            "content": (
                "REMINDER: WHENEVER you call a tool, it must be a code-fence tool "
                "call — NEVER JSON. But do not call a tool when none is needed: "
                "answer simple requests you can handle from your own knowledge or "
                "creativity (writing, explaining, chat) with a normal reply. "
                "NEVER roleplay execution. ONE code tool per response maximum. "
                "If you say you will do something, do it in that SAME response."
            ),
        },
        {
            "role": "user",
            "content": (
                "Before we start, can you confirm how you handle tool calls and "
                "how you execute code?"
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Of course. When I DO call a tool it is always a code-fence tool "
                "call — never JSON, never plain text. But I only reach for a tool "
                "when a request actually needs one; things I can answer from my own "
                "knowledge or write myself (a poem, an explanation, ordinary chat) I "
                "just reply to directly, with no fence. python_interpreter is my one "
                "execution tool: I run code, see the output, and chain executions "
                "until the task is fully complete, then finish with a normal reply "
                "containing my full report. I emit at most ONE python_interpreter "
                "fence per response, always with a short annotation label, and I "
                "never roleplay execution — if I say I'll do something, I do it in "
                "that same response with the actual fence. When the first request is "
                "work, I name the session in that same response too."
            ),
        },
    ]
}


EXEC_CODE_TOOLCALL_VIOLATION_PROMPT = """<SYSTEM_MESSAGE>
TOOL CALL POLICY VIOLATION DETECTED

Your previous response contained MORE THAN ONE python_interpreter call. Only the
FIRST call was executed. All subsequent code-execution calls were SILENTLY
DISCARDED — they did NOT run.

RULE (absolute):
  - You may emit AT MOST ONE python_interpreter call per response.
  - set_session_name is exempt — it may coexist with the work call.

WHY: multiple code blocks in a single turn create unpredictable state, confuse
the approval workflow, and make the conversation log ambiguous. Always wait for
the result of one execution before issuing the next.

WHAT YOU MUST DO NOW: if you still need to run the discarded code, re-issue it
in your next response — strictly one code-execution call per response.

Reminder of the correct format:
```python_interpreter: [Brief Description]
your_code_here
```
</SYSTEM_MESSAGE>"""


EXECUTE_CODE_RETIRED_PROMPT = """<SYSTEM_MESSAGE>
THE execute_code TOOL NO LONGER EXISTS — YOUR CALL WAS NOT RUN.

python_interpreter is the ONLY way to run code, and you SEE its output. Re-issue
the code now as a python_interpreter fence:

```python_interpreter: [Brief label of what this does]
your_code_here
```

For launching apps or blocking programs, start them detached inside
python_interpreter (subprocess.Popen / os.startfile) so the call returns and you
can confirm the result.
</SYSTEM_MESSAGE>"""


# Appended to conversation history as a role:system entry when the fence
# opener had to be auto-fixed (bracketless annotation, tool-name typo, ...) —
# teaches the correct syntax mid-session so repeats fade. {details} = short
# list of what was corrected. (Delivered via history, NOT inside the tool
# result — weak models ignore instructions embedded in observations.)
FORMAT_AUTOFIX_REMINDER = """[FORMAT REMINDER] Your previous tool call was malformed and had to be auto-corrected ({details}). It DID run this time, but use the exact form from now on:
```python_interpreter: [Brief label of what this does]
your_code_here
```
The annotation goes in [brackets] on the opener line; the code starts on the NEXT line."""


# Injected when a mid-work-mode reply is code-shaped but not a valid tool call
# (e.g. a ```python fence, or bare Python with no fence). Nothing was run.
MALFORMED_WORK_STEP_PROMPT = """<SYSTEM_MESSAGE>
YOUR LAST RESPONSE LOOKED LIKE A CODE STEP BUT WAS NOT A VALID TOOL CALL — NOTHING WAS RUN.

You are still in work mode. Choose ONE:
- If that code was your next step, re-issue it now as a python_interpreter fence:
```python_interpreter: [Brief label of what this does]
your_code_here
```
- If the task is FINISHED, reply with your complete report as normal prose. Do
not reply with a bare code dump — short snippets inside an explanation are fine.
</SYSTEM_MESSAGE>"""
