'''
systema/engine/prompts/native.py

Native function-calling renderings. HARD RULE: nothing in this module may
contain a code fence (three backticks) or the word "JSON" — weak native models
echo any format they see as literal text. Verified by the prompt assertions.

Session naming leads with PARALLEL tool calls (set_session_name alongside the
work call in one response) — the then_tool chaining arguments are only the
fallback for single-call dialects.
'''

from systema.execution.tool_registry import CANONICAL_TOOLS

# Frames a shared-core code example for native invocation (see shared.py).
INVOKE_HINT = "make a python_interpreter tool call whose code argument is:"


def native_header(include_session_naming: bool = True,
                  include_skills: bool = True) -> str:
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
    if include_session_naming:
        lines.append(
            "  - set_session_name(name, ...)          title the conversation.")
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

TALKING TO THE USER: native providers let you write normal text AND make a tool
call in the SAME turn. Just write your reply as normal text — it is shown to the
user alongside the action. Do NOT narrate that you're "about to" name the
session, load a skill, or run code — simply reply and make the call. Tools also
accept an OPTIONAL message_to_user, but you normally do NOT need it; use it only
as a fallback if you make a tool call while emitting no text at all. NEVER put
the same words in both normal text and message_to_user — that shows your reply
twice.

CALLING TWO TOOLS IN ONE RESPONSE: you may emit set_session_name (or load_skill)
ALONGSIDE your python_interpreter call as PARALLEL tool calls in the same
response — do exactly that whenever you need to name the session AND act. Only
if your provider limits you to a single tool call per response, use the fallback
chaining arguments on set_session_name / load_skill instead: set
then_tool='python_interpreter', put the Python in then_code, and pass
then_annotation (the short step label). Never emit two ACTION calls
(python_interpreter / read_file / edit_file / write_file) in one response.

Invoke every tool through your native function-calling mechanism. Everything
below — WHEN and WHY to use each tool, staying in work mode, directory safety,
memory rules, and session-naming timing — fully applies; only the invocation
happens as a native tool call.
"""


SESSION_NAMING_TAIL = """
HOW TO INVOKE: call the set_session_name tool with `name` (a few words). Write
your normal reply as text in the same turn — naming runs quietly; don't announce
it, and don't repeat your reply in message_to_user.

The critical case — the user's FIRST message asks for work: emit BOTH calls in
one response, set_session_name AND python_interpreter, as parallel tool calls.
Fallback only if your provider permits a single call per response: put the work
in set_session_name's chaining arguments (then_tool='python_interpreter',
then_code, then_annotation). Either way, never name the session INSTEAD of
doing the work.
"""


def skills_howto_tail() -> str:
    return (
        "\n"
        "HOW TO LOAD A SKILL:\n"
        "  Call the load_skill tool with the skill name, and just write your reply\n"
        "  as normal text this turn (message_to_user is an optional fallback for\n"
        "  when you emit no text).\n"
        "  To also act this turn, prefer a PARALLEL python_interpreter call; the\n"
        "  then_tool='python_interpreter' + then_code chaining arguments are the\n"
        "  fallback for single-call providers.\n"
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
- Never roleplay: if you say you'll do it, MAKE THE TOOL CALL in the SAME response.
- python_interpreter = your ONLY code tool; you SEE output; chain calls; print()
  what you need. ONE python_interpreter call per response, ALWAYS with the
  `annotation` argument (a short 3-6 word label shown to the user).
- Invoke tools as native function calls; to speak in the same turn, just write
  your reply as normal text alongside the call.
- set_session_name: call it ONCE, by your 4th reply — emitted ALONGSIDE the
  work call (parallel calls) when the first request is work.
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
                "set_session_name / load_skill / unload_skill). NEVER write a tool "
                "call as plain text — invoke it through your function-calling "
                "mechanism. NEVER roleplay execution. ONE code tool call per "
                "response maximum; set_session_name may be called alongside it. To "
                "say something while calling a tool, just write it as normal text "
                "alongside the call. If you say you will do something, do it in "
                "that SAME response."
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
                "written-out text. python_interpreter is my one execution tool: I run "
                "code, see the output, and chain calls until the task is fully "
                "complete, then finish with a normal reply containing my full "
                "report. I make at most ONE python_interpreter call per response, "
                "always with a short annotation label, and I may call "
                "set_session_name alongside it in the same response — which I do "
                "whenever the first request is work. I never roleplay execution; "
                "when I want to speak while acting, I just write it as normal text."
            ),
        },
    ]
}


EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE = """<SYSTEM_MESSAGE>
TOOL CALL POLICY VIOLATION DETECTED

Your previous response contained MORE THAN ONE python_interpreter call. Only the
FIRST call was executed. All subsequent code-execution calls were SILENTLY
DISCARDED — they did NOT run.

RULE (absolute):
  - You may make AT MOST ONE python_interpreter call per response.
  - set_session_name is exempt — it may be called alongside the work call.

WHY: multiple code blocks in a single turn create unpredictable state, confuse
the approval workflow, and make the conversation log ambiguous. Always wait for
the result of one execution before issuing the next.

WHAT YOU MUST DO NOW: if you still need to run the discarded code, make ONE
python_interpreter call in your next response — strictly one code-execution call
per response.
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
