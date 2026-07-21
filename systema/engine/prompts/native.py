'''
systema/engine/prompts/native.py

Native function-calling renderings. HARD RULE: nothing in this module may
contain a code fence (three backticks) or the word "JSON" — weak native models
echo any format they see as literal text. Verified by the prompt assertions.

Parallel tool calls are the norm (2026-07 revamp): a response may carry several
tool calls, executed sequentially in emitted order with ONE batched result
turn; only python_interpreter is capped at one call per response.
'''

from systema.execution.tool_registry import CANONICAL_TOOLS

# Frames a shared-core code example for native invocation (see shared.py).
INVOKE_HINT = "make a python_interpreter tool call whose code argument is:"


def native_header(include_skills: bool = True) -> str:
    lines = [
        "  - python_interpreter(code, annotation)   run Python and SEE its output — your",
        "                                         code-execution tool. annotation = a",
        "                                         short 3-6 word label of what the code",
        "                                         does (ALWAYS provide it).",
        "  - read_file(path, start_line, ...)     read a file as NUMBERED lines (windowed).",
        "  - edit_file(path, old_text, new_text)  surgical anchor edit — old_text must",
        "                                         match the file exactly and uniquely.",
        "                                         OR edit_file(path, lines='A-B', new_text)",
        "                                         to replace an entire line span A..B.",
        "  - write_file(path, content)            create or fully rewrite a file.",
        "  - grep(pattern, path, glob, type, output_mode, ...) ripgrep-style content",
        "                                         search. output_mode: files_with_matches",
        "                                         (default) / content / count. Regex,",
        "                                         context lines, skips build/VCS dirs.",
    ]
    if include_skills:
        lines.append(
            "  - load_skill(skill_name, ...)          load a skill's instructions.")
        lines.append(
            "  - unload_skill(skill_name)             unload a skill.")

    tool_list = "\n".join(lines)
    return f"""
NATIVE TOOL CALLING IS ACTIVE — READ FIRST

Your tools are provided to you as NATIVE function-calling tools:
{tool_list}
  (each tool except unload_skill ALSO accepts an OPTIONAL message_to_user — see below.)

TALKING TO THE USER: native providers let you write normal text AND make tool
calls in the SAME turn. Just write your reply as normal text — it is shown to
the user alongside the actions. Do NOT narrate that you're "about to" load a
skill or run code — simply reply and make the call. Tools also accept an
OPTIONAL message_to_user, but you normally do NOT need it; use it only as a
fallback if you make a tool call while emitting no text at all. NEVER put the
same words in both normal text and message_to_user — that shows your reply
twice.

CALLING SEVERAL TOOLS IN ONE RESPONSE: you may emit MULTIPLE tool calls in a
single response. They run one after another in the order you emit them, and ALL
their results come back together in one observation. Combine freely (read_file
+ edit_file + load_skill, several file ops, multiple python_interpreter calls,
web_search, ...). Results arrive only after the WHOLE batch has run — if a later
call depends on an earlier call's OUTPUT, stop after the producing call and
continue next turn.

Invoke every tool through your native function-calling mechanism. Everything
below — WHEN and WHY to use each tool, staying in work mode, directory safety,
and memory rules — fully applies; only the invocation happens as a native tool
call.
"""


def skills_howto_tail() -> str:
    return (
        "\n"
        "HOW TO LOAD A SKILL:\n"
        "  Call the load_skill tool with the skill name, and just write your reply\n"
        "  as normal text this turn (message_to_user is an optional fallback for\n"
        "  when you emit no text).\n"
        "  To also act this turn, emit a PARALLEL python_interpreter call alongside\n"
        "  it in the same response — the skill loads before the code runs.\n"
        "  The skill's full instructions will be injected into your system context.\n"
        "  Works inside AND outside python_interpreter.\n"
        "  Do NOT load a skill that already shows [LOADED] — it's already active!\n"
        "\n"
        "HOW TO UNLOAD A SKILL:\n"
        "  Call the unload_skill tool with the skill name.\n"
        "  Removes the skill from your active context.\n"
        "  Works inside AND outside python_interpreter.\n"
        "  Do NOT unload a skill that is not [LOADED] — it isn't loaded!\n"
    )


MUST_REMEMBER = """
MUST REMEMBER (quick recall of the rules above):
- No tool needed? Just reply. Poems, explanations, chat, and anything you can
  answer from your own knowledge get a plain response — never run print() to
  "produce" text you could simply type.
- Never roleplay: if you say you'll do it, MAKE THE TOOL CALL in the SAME response.
- python_interpreter = your ONLY code tool; you SEE output; chain calls; print()
  what you need. One or more python_interpreter calls per response are allowed,
  ALWAYS each with the `annotation` argument (a short 3-6 word label).
- Several tool calls may share one response — they run in order and all results
  return together; keep steps that depend on each other's OUTPUT in separate turns.
- Invoke tools as native function calls; to speak in the same turn, just write
  your reply as normal text alongside the calls.
- Be friendly and descriptive.
"""


# Options tail for the injected work-continuation prompt (finish rule stated
# once here for this injected channel).
WORK_OPTIONS = """\
Options:
- More code:   call the python_interpreter tool with your Python in the `code`
               argument (plus a short `annotation`).
- Finish:      reply with NO tool call — just normal reply text containing your
               COMPLETE report. That reply ends work mode and IS your final
               answer: its visible text must contain the full findings/result.
               Do NOT leave the summary in your private reasoning — the user
               never sees your reasoning — and never end with "I'll summarize"
               and nothing else; the summary must BE the reply.
- Load skill:  call the load_skill tool with the skill name (only if not loaded).
- Unload skill: call the unload_skill tool with the skill name (only if loaded)."""


PREFILLING_NATIVE = {
    "messages": [
        {
            "role": "system",
            "content": (
                "REMINDER: Invoke tools as NATIVE function calls (python_interpreter / "
                "read_file / edit_file / write_file / grep / load_skill / "
                "unload_skill). NEVER write a tool call as plain text — invoke it "
                "through your function-calling mechanism. But do not call a tool "
                "when none is needed: answer simple requests you can handle from "
                "your own knowledge or creativity (writing, explaining, chat) with "
                "a normal reply and no tool call. NEVER roleplay execution. You may "
                "emit several tool calls per response (they run in order), "
                "including multiple python_interpreter calls. To say something while "
                "calling tools, just write it as normal text alongside the calls. "
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
                "Of course. I invoke my tools as native function calls — never as "
                "written-out text. But I only reach for a tool when a request truly "
                "needs one; things I can answer from my own knowledge or write myself "
                "(a poem, an explanation, ordinary chat) I just reply to directly, "
                "with no tool call. python_interpreter is my one execution tool: I run "
                "code, see the output, and chain calls until the task is fully "
                "complete, then finish with a normal reply containing my full "
                "report. I may emit several tool calls in one response — they run "
                "in the order emitted and I get all their results together, and "
                "that can include multiple python_interpreter calls, each with a "
                "short annotation label. I never roleplay execution; when I want "
                "to speak while acting, I just write it as normal text."
            ),
        },
    ]
}


EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE = """<SYSTEM_MESSAGE>
TOOL CALL POLICY VIOLATION DETECTED

Your previous response contained MORE THAN ONE python_interpreter call. Only the
FIRST python_interpreter call was executed. The extra python_interpreter calls
were SILENTLY DISCARDED — they did NOT run.

RULE (absolute):
  - At most ONE python_interpreter call per response.
  - Other tools (read_file / edit_file / write_file / grep / skills) may freely
    accompany it as parallel calls — only python_interpreter is capped.

WHY: multiple code executions in a single turn create unpredictable state and
make the conversation log ambiguous. Observe one execution's output before
issuing the next.

WHAT YOU MUST DO NOW: if you still need to run the discarded code, make ONE
python_interpreter call in your next response — one code execution at a time.
</SYSTEM_MESSAGE>"""


EXECUTE_CODE_RETIRED_PROMPT_NATIVE = """<SYSTEM_MESSAGE>
THE execute_code TOOL NO LONGER EXISTS — YOUR CALL WAS NOT RUN.

python_interpreter is the ONLY way to run code, and you SEE its output. Re-issue
the code now as a python_interpreter tool call (Python in the `code` argument,
plus a short `annotation` label).

For launching apps or blocking programs, start them detached inside
python_interpreter (subprocess.Popen / os.startfile) so the call returns and you
can confirm the result.
</SYSTEM_MESSAGE>"""


# Injected when a mid-work-mode reply is code-shaped but not a valid tool call.
# Nothing was run. Parity twin of compat.MALFORMED_WORK_STEP_PROMPT.
MALFORMED_WORK_STEP_PROMPT_NATIVE = """<SYSTEM_MESSAGE>
YOUR LAST RESPONSE LOOKED LIKE A CODE STEP BUT WAS NOT A VALID TOOL CALL — NOTHING WAS RUN.

You are still in work mode. Choose ONE:
- If that code was your next step, re-issue it now as a python_interpreter tool
call with the Python in its code argument (plus a short annotation label).
- If the task is FINISHED, reply with your complete report as normal prose —
that visible reply IS the report the user sees. Do not reply with a bare code
dump; short snippets inside an explanation are fine.
</SYSTEM_MESSAGE>"""
