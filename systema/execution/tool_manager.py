"""
systema/execution/tool_manager.py
Tool Manager — the python interpreter execution system and compat fence parser.

python_interpreter is the ONLY code-execution tool (execute_code was retired in
2026-07; see systema.execution.tool_registry.LEGACY_STRIP_KEYS). The code
approval dialog runs on the main thread via Qt signals; a single-exec policy
allows one code tool per AI turn (set_session_name exempt).
"""

import json
import keyword
import re
import threading
import os
from dataclasses import dataclass, field
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from systema.execution.python_interpreter import PythonInterpreter
from systema.execution import tool_registry
from systema.execution import file_journal
from systema.common.logger import _make_logger, _NoOpLogger


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("ToolManager") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class InterpreterState:
    """python_interpreter's own runtime state — only the interpreter runs code,
    so this stays nested under WorkState.interpreter rather than flattened."""
    is_running: bool = False              # python_interpreter executing code RIGHT NOW
    last_code: str | None = None          # last source run (UI note / interrupt)
    last_annotation: str | None = None    # last banner label


@dataclass
class WorkState:
    """Consolidated observe/act loop state. Every tool (python_interpreter,
    read_file/edit_file/write_file, load_skill/unload_skill) drives it — the
    loop is not owned by any single tool. Owned by ToolManager as `.work`."""
    is_working: bool = False              # in the loop (any tool)
    last_output: str | None = None        # the latest step's observation (feeds next ping)
    last_tool: str | None = None          # what produced it: interpreter/read_file/edit_file/write_file/skill
    interpreter: InterpreterState = field(default_factory=InterpreterState)

    def reset(self):
        self.is_working = False
        self.last_output = None
        self.last_tool = None
        self.interpreter = InterpreterState()


# Tools whose fence opener carries a ": [annotation]" label (parsed by the
# tolerant annotated branch of _parse_fence, returning a 3-tuple).
_ANNOTATED_TOOL_NORMS = frozenset(
    {'pythoninterpreter', 'readfile', 'editfile', 'writefile', 'grep'})


# ──────────────── Malformed-opener tolerance helpers (weak models) ───────────
# Word starting a line that means "this is code, not an annotation".
_CODE_KEYWORDS = frozenset(k.lower() for k in keyword.kwlist) | {'print', 'self'}
# Any of these characters in a line means it is code-shaped, never an annotation.
_ANNOT_FORBIDDEN = set('=(){}<>[]`"\'')


def _edit_distance_le1(a, b):
    """True when a and b are at most one insert/delete/substitute apart."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = 0
    used_edit = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        if used_edit:
            return False
        used_edit = True
        if len(a) == len(b):
            i += 1  # substitution
        j += 1      # else: insertion into the longer string
    return True     # any trailing char fits the remaining <=1 budget


def _is_prose_annotation(line):
    """Heuristic: is this line a human-readable label rather than Python code?
    Used to decide whether junk after the tool name (or a stray first fence
    line) is a bracketless annotation the model meant to write."""
    line = line.strip()
    if not line or len(line) > 100 or line[0] in '#@':
        return False
    if any(ch in _ANNOT_FORBIDDEN for ch in line):
        return False
    tokens = line.replace(':', ' ').split()
    if len(tokens) < 2:
        return False
    return tokens[0].lower() not in _CODE_KEYWORDS


def _clean_annotation(raw):
    """Strip annotation decorations: leading colon/dash, wrapping [] () quotes."""
    s = raw.strip().lstrip(':-—– ').strip()
    for opener, closer in (('[', ']'), ('(', ')'), ('"', '"'), ("'", "'")):
        if len(s) >= 2 and s.startswith(opener) and s.endswith(closer):
            s = s[1:-1].strip()
    return s


def _looks_like_path(s):
    """Heuristic: is this short single-line string a file path? Used to recover
    a path the model misplaced onto the fence opener line."""
    s = (s or '').strip().strip('"').strip("'")
    if not s or '\n' in s or len(s) > 400:
        return False
    if '/' in s or '\\' in s:
        return True
    return bool(re.match(r'^[\w.\- ]+\.\w{1,8}$', s))


def _lift_leading_annotation(content):
    """If the first line INSIDE a work fence is an annotation the model misplaced
    ('[Fetch news]' or bare prose), lift it out so it doesn't poison the code.
    Returns (annotation, content, lifted)."""
    parts = content.split('\n', 1)
    if len(parts) < 2:
        return '', content, False
    first, rest = parts[0].strip(), parts[1].strip()
    if not rest:
        return '', content, False
    if (first.startswith('[') and first.endswith(']')) or _is_prose_annotation(first):
        return _clean_annotation(first), rest, True
    return '', content, False
# ─────────────────────────────────────────────────────────────────────────────


class ApprovalSignal(QObject):
    """Signal object for requesting code approval on main thread"""
    request_approval = pyqtSignal(str, str, object)  # code, execution_type, callback
    system_message   = pyqtSignal(str)               # text → chat window, main thread only
    file_op          = pyqtSignal(dict)              # structured file-op card → chat window
    close_approval_dialog = pyqtSignal(bool, str)  # approved, modified_code — closes active dialog
    timeout_signal   = pyqtSignal(int, object, object, object)  # elapsed, done_event, result_holder, user_event — timeout prompt
    work_code_active = pyqtSignal(bool)                          # True when code execution starts, False when it finishes


class ToolManager:
    """Manages the python interpreter and compat fence parsing.

    ADDING A TOOL is a 3-touch change (see tool_registry.py):
        1. Add its entry to systema/execution/tool_registry.py — the native
           schema AND the compat prompt docs render from it automatically.
        2. Add a parse_<tool>() method here (mirrors parse_python_interpreter).
        3. Add one dispatch branch in AIEngine._process_ai_response.
    Format detection, fuzzy matching, stripping and unclosed-fence recovery
    all work automatically off the registry keys.
    """

    def __init__(self, settings_callback=None, ai_engine=None):
        """
        Initialize ToolManager

        Args:
            settings_callback: Function to get settings dict
            ai_engine: Reference to AI engine for code explanations
        """
        log.info("[ToolManager.__init__] ── Initializing ToolManager ──────────────────────")
        log.debug(f"[ToolManager.__init__] has_settings_callback={settings_callback is not None} | "
                  f"has_ai_engine={ai_engine is not None}")

        # Available tools - easy to add more!
        self.tools = {
            'python': PythonInterpreter(),
        }
        log.debug(f"[ToolManager.__init__] Tools initialized: {list(self.tools.keys())}")

        # Work-mode state — the observe/act loop, consolidated. Any tool drives it.
        self.work = WorkState()
        self._pending_interrupt_notice = None  # appended to output when user interrupts running code
        log.debug("[ToolManager.__init__] work state initialised (WorkState)")

        # ── Malformed-call auto-correction (weak-model guard rails) ──────────
        # _parse_fence(record=True) appends one short description per auto-fix;
        # _flush_parse_corrections() logs them, shows ONE chat NOTICE per
        # session (further fixes are silent — no NOTICE spam), and queues a
        # format reminder that the ENGINE delivers as a role:system history
        # entry (weak models ignore text placed inside tool results).
        self._parse_corrections = []
        self._pending_format_reminder = None
        self._autofix_notice_shown = False

        # ── File-editing subsystem state ──────────────────────────────────────
        # _pending_file_edit: payload popped by the approval-dialog builder so
        # the dialog opens in file-edit (per-hunk diff) mode for this one run.
        self._pending_file_edit = None
        self.last_file_op_journal_id = None
        # Prune the undo journal once per app start (off the GUI thread).
        try:
            _s = settings_callback() if settings_callback else {}
            if _s.get('file_history_enabled', True):
                threading.Thread(
                    target=file_journal.prune,
                    args=(int(_s.get('file_history_keep_days', 14)),),
                    daemon=True).start()
        except Exception:
            pass

        # ── Violation tracking ────────────────────────────────────────────────
        # Incremented each time the AI emits more than one code-execution tool
        # call in a single response (set_session_name is exempt).
        self._exec_violations_lock = threading.Lock()
        self.exec_violations = 0
        log.debug("[ToolManager.__init__] exec_violations counter initialised to 0")

        # Settings and AI engine
        self.settings_callback = settings_callback
        self.ai_engine = ai_engine
        # Override: None = defer to settings_callback; True/False = force on/off
        self.supervised_execution = None
        # When True, skip the ENTIRE gate (scan/policy/dialog) and auto-run.
        # Used by background scheduled tasks that opt into bypassing supervision.
        self.bypass_security = False
        # Background-task security override. None = normal (interactive) gating.
        # A dict {category: 'allow'|'deny'} (a single '*' key means all-categories)
        # switches the gate into non-interactive mode: a risky op whose category
        # is denied is BLOCKED with an actionable observation (never a dialog);
        # everything else runs. Set by TaskAIEngine for scheduled background tasks.
        self.background_policy = None
        # Optional callback(category:str, detail:str) invoked when background_policy
        # blocks an op, so the task layer can alert the user's main chat.
        self.background_alert = None
        # Tag-based "don't ask again for this session" — operation categories the
        # user session-approved from the code-approval dialog. Ephemeral: never
        # persisted, wiped on restart. The gate auto-approves a run whose risky
        # categories are all in this set.
        self.session_allowed_categories = set()
        # Optional free-text reason the user typed when rejecting a run in the
        # approval dialog; surfaced to the AI so it knows WHY. Set on the main
        # thread before the approval callback fires (happens-before the worker
        # read via the approval Event).
        self._last_reject_reason = ""
        # Capability gate — enforced by run_python_interpreter, not just narrated
        # in the system prompt. Set by TaskAIEngine from the task dict.
        self.allow_workmode = True

        # Approval signal for main thread communication
        log.debug("[ToolManager.__init__] Creating ApprovalSignal and connecting to main thread handler")
        self.approval_signal = ApprovalSignal()
        self.approval_signal.request_approval.connect(self._show_approval_dialog_on_main_thread)
        self.approval_signal.system_message.connect(self._deliver_system_message)
        self.approval_signal.file_op.connect(self._deliver_file_op)
        self.approval_signal.close_approval_dialog.connect(self._close_active_approval_dialog)
        self.approval_signal.timeout_signal.connect(self._show_timeout_dialog_on_main_thread)
        self._active_approval_dialog = None
        self._get_android_bridge = None
        log.debug("[ToolManager.__init__] ApprovalSignal connected")

        # ── Tool registry (single source of truth: tool_registry.py) ─────────
        self._tool_keys = list(tool_registry.CANONICAL_TOOLS)
        # Maps normalised (no-underscore, lowercase) form → canonical name
        self._tool_keys_norm = {k.replace('_', '').lower(): k for k in self._tool_keys}
        # Retired tools: strip-only (old sessions render clean), never dispatchable.
        self._legacy_keys_norm = {k.replace('_', '').lower(): k
                                  for k in tool_registry.LEGACY_STRIP_KEYS}
        log.debug(f"[ToolManager.__init__] Registered tool keys: {self._tool_keys}")

        # ── Code-execution tools (subject to single-call policy) ──────────────
        # set_session_name is NOT in this set — it may coexist with a code tool.
        self._exec_tool_keys = set(tool_registry.EXEC_TOOL_KEYS)
        log.debug(f"[ToolManager.__init__] Exec tool keys (policy-restricted): {self._exec_tool_keys}")

        # ── Chat window bridge ────────────────────────────────────────────────
        # Injected by the controller after construction via:
        #   self.ai.tool_manager._get_chat = lambda: self._chat
        self._get_chat = None

        log.info("[ToolManager.__init__] ✓ ToolManager initialization complete")

    # ── Chat window bridge ────────────────────────────────────────────────────

    @property
    def _chat(self):
        """
        Returns the live ChatWindow instance, or None if not yet created
        or if no bridge has been injected.
        """

        if callable(self._get_chat):
            chat = self._get_chat()
        else:
            chat = None

        if chat is None:
            log.debug("[ToolManager._chat] | [chat_window.py] NONE")
        else:
            is_active = getattr(chat, "isVisible", lambda: True)()
            log.debug(f"[ToolManager._chat] | [chat_window.py] ACTIVE={is_active} | {chat}")

        return chat

    def _deliver_file_op(self, info: dict):
        """Slot — main thread. Renders a file-op card in the chat window and
        mirrors it to the Android client (if connected)."""
        chat = self._chat
        if chat is not None and hasattr(chat, 'add_file_op_card'):
            try:
                chat.add_file_op_card(info)
            except Exception as e:
                log.error(f"[ToolManager._deliver_file_op] card failed: {e}")
        ab = self._get_android_bridge() if callable(self._get_android_bridge) else None
        if ab is not None and getattr(ab, '_conn', None) is not None:
            try:
                ab.add_file_op(info)
            except Exception as e:
                log.debug(f"[ToolManager._deliver_file_op] android mirror skipped: {e}")

    def _deliver_system_message(self, text: str):
        """
        Slot — always runs on the main thread (connected via signal).
        Forwards a system message to the chat window and Android bridge.
        Working: annotations only update the banner — they are NOT added as chat messages
        (the annotation is shown inside the code execution note instead).
        """
        import re as _re
        is_working_annotation = "**Working:**" in text or text.startswith("Working:")

        chat = self._chat
        if chat and not is_working_annotation:
            # Normal system messages go to chat as usual
            chat.add_system_message(text)
        elif chat and is_working_annotation:
            # Only update the work banner — no chat widget
            clean = _re.sub(r'\*+', '', text).replace("Working:", "").strip()
            if hasattr(chat, '_work_banner'):
                chat._work_banner.setText(f"⚙ Working: {clean}")
                chat._work_banner.show()
            # Narrate the annotation, then a short beat. The commentary text is
            # enqueued later (show_ai_message), so FIFO order gives
            # annotation -> pause -> commentary; annotation-only steps still
            # get spoken.
            if clean and getattr(chat, 'voice_enabled', False):
                try:
                    chat.speak_ai_response(clean)
                    vh = getattr(getattr(chat, 'controller', None),
                                 'voice_handler', None)
                    if vh is not None:
                        vh.speak_pause()
                except Exception as e:
                    log.warning(f"[ToolManager._deliver_system_message] "
                                f"annotation narration failed: "
                                f"{type(e).__name__}: {e}")

        # Mirror to Android bridge
        try:
            ab = self._get_android_bridge() if callable(self._get_android_bridge) else None
            if ab and ab.isVisible():
                if is_working_annotation:
                    clean = _re.sub(r'\*+', '', text).replace("Working:", "").strip()
                    ab.show_work_banner(clean)
                else:
                    ab.add_system_message(text)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Canonical tool registry — the data now lives in
    # systema/execution/tool_registry.py (single source of truth for schemas
    # AND compat prompt docs). This alias keeps existing references working.
    # ─────────────────────────────────────────────────────────────────────────
    _CANONICAL_TOOLS = tool_registry.CANONICAL_TOOLS

    def get_canonical_tools(self, include_session_naming: bool = True,
                            include_skills: bool = True) -> list:
        """Return the active tools as canonical schema dicts (name/description/
        parameters), gated by the same capability flags the prompt uses. Feed the
        result to systema.engine.native_adapters.to_<dialect>_tools() for native mode."""
        active = []
        if self.allow_workmode:
            active.append('python_interpreter')
        if include_session_naming:
            active.append('set_session_name')
        if include_skills:
            active += ['load_skill', 'unload_skill']

        out = []
        for name in active:
            spec = self._CANONICAL_TOOLS[name]
            pname, pdesc = spec['param']
            props = {pname: {'type': 'string', 'description': pdesc}}
            required = [pname]
            for ep in spec.get('extra_params', []):
                ename, edesc, ereq = ep[0], ep[1], ep[2]
                prop = {'type': 'string', 'description': edesc}
                # Optional 4th element = allowed values (rendered as a JSON-schema
                # enum so native providers constrain the argument).
                if len(ep) >= 4 and ep[3]:
                    prop['enum'] = list(ep[3])
                props[ename] = prop
                if ereq:
                    required.append(ename)
            out.append({
                'name': name,
                'description': spec['description'],
                'parameters': {
                    'type': 'object',
                    'properties': props,
                    'required': required,
                },
            })
        return out

    def _code_fence(self, tool_name: str, code, annotation: str = "") -> str:
        """Render one tool fence. Annotated tools (python_interpreter + the file
        subsystem) fold their annotation into the ```tool: [label] form;
        everything else is a plain fence."""
        if not isinstance(code, str):
            code = str(code)
        code = code.strip()
        if (self._norm_key(tool_name) in _ANNOTATED_TOOL_NORMS
                and isinstance(annotation, str) and annotation.strip()):
            return f"```{tool_name}: [{annotation.strip()}]\n{code}\n```"
        return f"```{tool_name}\n{code}\n```"

    def tool_calls_to_fences(self, tool_calls: list, include_messages: bool = True) -> str:
        """Reconstruct canonical fence text from normalized native tool calls
        (each {"name","arguments"}). This lets native-mode responses flow through
        the exact same fence-parsing pipeline as compat mode — the engine never
        has to branch on tool format below _call_provider.

        ``include_messages`` controls whether each call's ``message_to_user`` is
        re-emitted as a text prefix. The caller passes False when the model
        already produced free assistant text this turn (that text is then the
        single user-facing reply) so the narration isn't rendered twice."""
        parts = []
        for call in (tool_calls or []):
            name = call.get('name')
            spec = self._CANONICAL_TOOLS.get(name)
            if not spec:
                if self._legacy_keys_norm.get(self._norm_key(str(name or ''))):
                    # A hallucinated call to a RETIRED tool: reconstruct its
                    # fence anyway so the engine's retired-tool handling fires
                    # (strip + corrective prompt) instead of the call silently
                    # vanishing.
                    _body = (call.get('arguments') or {}).get('code', '') or ''
                    parts.append(f"```{self._legacy_keys_norm[self._norm_key(str(name))]}\n{_body}\n```")
                    log.warning(f"[ToolManager.tool_calls_to_fences] RETIRED tool "
                                f"'{name}' called natively — reconstructed for "
                                f"the engine's retired-tool handling")
                    continue
                log.warning(f"[ToolManager.tool_calls_to_fences] Unknown tool '{name}' — skipped")
                continue
            pname = spec['param'][0]
            args = call.get('arguments', {}) or {}
            # Multi-arg tools (the file subsystem) recompose their compat body
            # via the registry's to_fence builder; single-param tools take the
            # main argument directly.
            if callable(spec.get('to_fence')):
                body = spec['to_fence'](args)
            else:
                body = args.get(pname, '')
            if not isinstance(body, str):
                body = str(body)
            # Optional user-facing message, emitted as plain text BEFORE the fence
            # (exactly like text-before-a-fence in compat mode) — but ONLY as a
            # fallback when the model wrote no free text this turn. When it did,
            # include_messages is False so we don't render the reply twice.
            msg = args.get('message_to_user', '') if include_messages else ''
            prefix = (msg.strip() + "\n\n") if isinstance(msg, str) and msg.strip() else ""
            # Annotated tools carry an optional annotation, folded into the
            # annotated fence form so the UI step title isn't a generic default.
            primary = self._code_fence(name, body, args.get('annotation', ''))
            parts.append(prefix + primary)

            # ── Sub-tool chaining (FALLBACK path) ────────────────────────────
            # Parallel tool calls are the primary way to name-and-act in one
            # native turn (this method serializes every call in the list). For
            # single-call dialects, set_session_name / load_skill also accept
            # ONE chained code tool (then_tool + then_code); expand it into its
            # own fence so the standard pipeline executes it in the same turn.
            if name in ('set_session_name', 'load_skill'):
                then_raw = args.get('then_tool', '')
                then_name = self._tool_keys_norm.get(self._norm_key(str(then_raw))) if then_raw else None
                if then_name in self._exec_tool_keys:
                    then_code = args.get('then_code', '')
                    if isinstance(then_code, str) and then_code.strip():
                        parts.append(self._code_fence(then_name, then_code,
                                                      args.get('then_annotation', '')))
                        log.info(f"[ToolManager.tool_calls_to_fences] '{name}' chained a "
                                 f"'{then_name}' sub-tool call")
                    else:
                        log.warning(f"[ToolManager.tool_calls_to_fences] '{name}' set then_tool="
                                    f"'{then_raw}' but then_code was empty — chained call skipped")
                elif then_raw:
                    log.warning(f"[ToolManager.tool_calls_to_fences] '{name}' then_tool='{then_raw}' "
                                f"is not a chainable code tool — ignored")
        return "\n\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # Public parse methods - Fence-based parsing (new format)
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_fence(self, text, tool_name, record=False):
        """
        Extract content from a code fence whose language identifier matches tool_name.
        Returns (content, remaining_text) or None.
        For python_interpreter, returns (content, remaining_text, annotation).

        Tolerant opener parsing (weak-model guard rails, 2026-07):
          - tag near-misses resolve via _resolve_tool_tag (python_interpeter,
            python interpreter, pythonInterpreter, 4+ backtick fences)
          - the python_interpreter annotation is accepted bracketless in any of
            `tool: text` / bare prose / "quoted" / (parens) / - dash form, and a
            misplaced prose/[bracketed] FIRST LINE inside the fence is lifted
            out so it can't poison the code with a SyntaxError
        When record=True (live parses only — never strip/render paths) each
        auto-fix appends a short description to self._parse_corrections; the
        parse_* wrappers flush them into a NOTICE + model format reminder.
        """
        norm_target = self._norm_key(tool_name)

        # ── Annotated tools: tolerant opener with optional annotation ────────
        # python_interpreter + the file subsystem all take ": [label]" openers.
        if norm_target in _ANNOTATED_TOOL_NORMS:
            is_we = norm_target == "pythoninterpreter"
            # ```tool[:] [annotation-any-decoration]\n<body>\n```
            pattern_we = r'(`{3,})[ \t]*([A-Za-z][\w-]*)[ \t]*(:?)[ \t]*([^\n]*?)[ \t]*\r?\n(.*?)[ \t]*`{3,}'
            for match in re.finditer(pattern_we, text, re.DOTALL):
                canonical, exact = self._resolve_tool_tag(match.group(2), include_legacy=True)
                if canonical is None or self._norm_key(canonical) != norm_target:
                    continue
                fixes = []
                if not exact:
                    fixes.append(f"tool name '{match.group(2)}'")
                raw = match.group(4).strip()
                had_colon = bool(match.group(3))
                content = match.group(5) if not is_we else match.group(5).strip()
                if not is_we:
                    content = content.strip('\n')
                annotation = ''
                if raw:
                    bracketed = raw.startswith('[') and raw.endswith(']')
                    if bracketed or had_colon or raw[0] in '-—("\'':
                        annotation = _clean_annotation(raw)
                        if not bracketed:
                            fixes.append("annotation without [brackets]")
                    elif not is_we or _is_prose_annotation(raw):
                        annotation = _clean_annotation(raw)
                        fixes.append("annotation without [brackets]")
                    else:
                        # Code crammed onto the opener line — keep it as code.
                        content = (raw + '\n' + content).strip()
                        fixes.append("code on the opener line")
                # Only python bodies get the misplaced-annotation lift — a file
                # tool's body starts with a PATH that must never be consumed.
                if is_we and not annotation and content:
                    annotation, content, lifted = _lift_leading_annotation(content)
                    if lifted:
                        fixes.append("annotation inside the fence body")
                if record and fixes:
                    self._parse_corrections.extend(fixes)
                remaining = (text[:match.start()] + text[match.end():]).strip()
                log.debug(
                    f"[ToolManager._parse_fence] ✓ Matched '{tool_name}' "
                    f"annotation='{annotation}' | content_len={len(content)}"
                    + (f" | auto-fixed: {fixes}" if fixes else "")
                )
                return content, remaining, annotation
            return None

        # ── General case for all other tools ─────────────────────────────────
        pattern = r'`{3,}[ \t]*([A-Za-z][\w-]*)(?:[ \t]*\r?\n|[ \t]+)(.*?)[ \t]*`{3,}'
        for match in re.finditer(pattern, text, re.DOTALL):
            norm_tag = self._norm_key(match.group(1))
            if norm_tag != norm_target:
                canonical, _exact = self._resolve_tool_tag(match.group(1), include_legacy=True)
                if canonical is None or self._norm_key(canonical) != norm_target:
                    continue
                if record:
                    self._parse_corrections.append(
                        f"tool name '{match.group(1)}' corrected to {tool_name}")
            content = match.group(2).strip()
            remaining = (text[:match.start()] + text[match.end():]).strip()
            log.debug(f"[ToolManager._parse_fence] ✓ Matched '{tool_name}' | content_len={len(content)}")
            return content, remaining
        return None

    def recover_unclosed_tool_fence(self, text):
        """Recover a tool call whose closing ``` fence the model forgot.

        The #1 weak-model failure: the model opens ```python_interpreter but never
        closes the fence, so the call is neither executed NOR stripped — the raw
        block leaks into chat. Here we detect an *unbalanced* fence whose opening
        tag is a known tool and auto-close it by appending a synthetic ```, so the
        normal parsers can extract and run it (turning a leak into a real call).

        Non-tool unclosed fences (e.g. ```python used for prose) are left alone —
        those are legitimate formatting, not leaked tool syntax.

        Returns (text, recovered_canonical_tool_name_or_None).
        """
        if not text or '```' not in text:
            return text, None

        fence_positions = [m.start() for m in re.finditer(r'```', text)]
        # Balanced fences pair up left-to-right → nothing dangling.
        if len(fence_positions) % 2 == 0:
            return text, None

        # Odd count → the last ``` is an unmatched opener. Is its tag a tool?
        last = fence_positions[-1]
        m = re.match(r'```[ \t]*([\w-]+)', text[last:])
        if not m:
            return text, None  # bare ``` with no tag — leave as prose
        # Legacy (retired) tools are auto-closed too, so the engine's
        # retired-tool handling can catch them instead of the raw block leaking.
        # Near-miss tags (work_enviroment) resolve as well — same leak otherwise.
        canonical, _exact = self._resolve_tool_tag(m.group(1), include_legacy=True)
        if not canonical:
            return text, None  # unclosed non-tool fence (e.g. ```python) — legit prose

        log.warning(f"[ToolManager.recover_unclosed_tool_fence] Auto-closing unclosed "
                    f"'{canonical}' fence — model omitted the closing ``` (would have leaked)")
        return (text.rstrip() + "\n```"), canonical

    # Literal data blocks: #@FILE <name> … #@ENDFILE  (content captured verbatim)
    _FILE_BLOCK_RE = re.compile(
        r'^[ \t]*#@FILE[ \t]+(\w+)[ \t]*\r?\n(.*?)\r?\n[ \t]*#@ENDFILE[ \t]*$',
        re.DOTALL | re.MULTILINE
    )

    def _extract_file_blocks(self, code):
        """Pull #@FILE <name> … #@ENDFILE literal blocks out of code BEFORE it is
        compiled, so their content is never parsed as Python. Each block's raw
        text is bound to a namespace string variable <name> (use it with
        write_file(path, <name>)). This is the fix for embedding source that
        contains backslashes, quotes or triple-quotes — which otherwise break the
        outer interpreter's parser with 'unterminated string literal' etc.

        Returns (stripped_code, {name: content})."""
        blocks = {}

        def _grab(m):
            name = m.group(1)
            if name.isidentifier():
                blocks[name] = m.group(2)
                return ''  # remove the literal block from the executed code
            return m.group(0)  # invalid identifier — leave untouched

        stripped = self._FILE_BLOCK_RE.sub(_grab, code)
        if blocks:
            log.info(f"[ToolManager._extract_file_blocks] Extracted {len(blocks)} literal "
                     f"block(s): {list(blocks.keys())}")
        return stripped, blocks

    def _flush_parse_corrections(self, tool_name):
        """Turn recorded parse auto-fixes into: a log warning, ONE chat NOTICE
        per session (weak models auto-fix often — no NOTICE bombing), and (for
        python_interpreter) a format reminder the engine appends to conversation
        history as a role:system entry so the model actually learns from it."""
        if not self._parse_corrections:
            return
        details = '; '.join(dict.fromkeys(self._parse_corrections))
        self._parse_corrections = []
        log.warning(f"[ToolManager._flush_parse_corrections] Auto-corrected malformed "
                    f"'{tool_name}' call: {details}")
        if not self._autofix_notice_shown:
            self._autofix_notice_shown = True
            self.approval_signal.system_message.emit(
                f"**NOTICE:** Auto-corrected a malformed `{tool_name}` call ({details}). "
                f"The call was run and the model is being reminded of the correct "
                f"format. Further auto-fixes this session will be silent (logged only).")
        if tool_name == 'python_interpreter':
            from systema.engine.prompts.global_instructions import FORMAT_AUTOFIX_REMINDER
            self._pending_format_reminder = FORMAT_AUTOFIX_REMINDER.format(details=details)

    def parse_python_interpreter(self, text):
        """Parse python_interpreter fence from AI output. Returns (code, remaining_text) or None."""
        log.debug(f"[ToolManager.parse_python_interpreter] Parsing {len(text)} chars")

        result = self._parse_fence(text, 'python_interpreter', record=True)

        if result:
            if len(result) == 3:
                code, remaining, annotation = result
                self.work.interpreter.last_annotation = annotation
                log.info(
                    f"[ToolManager.parse_python_interpreter] ✓ Found with annotation='{annotation}' | code_len={len(code)}")
                if annotation:
                    # Emit via signal — safe from any thread
                    self.approval_signal.system_message.emit(
                        f"**Working:** ***{annotation}***"
                    )
                    log.debug(f"[ToolManager.parse_python_interpreter] | SENT SYSTEM MESSAGE TO CHAT: {annotation}")
            else:
                code, remaining = result
                self.work.interpreter.last_annotation = None
                log.info(f"[ToolManager.parse_python_interpreter] ✓ Found (no annotation) | code_len={len(code)}")

            self._flush_parse_corrections('python_interpreter')
            return code, remaining

        log.debug("[ToolManager.parse_python_interpreter] Not found")
        return None

    def parse_set_session_name(self, text):
            """Parse set_session_name fence from AI output. Returns (name, remaining_text) or None."""
            log.debug(f"[ToolManager.parse_set_session_name] Parsing {len(text)} chars")
            result = self._parse_fence(text, 'set_session_name', record=True)
            if result:
                name, remaining = result
                log.info(f"[ToolManager.parse_set_session_name] ✓ Found | name='{name}'")
                self._flush_parse_corrections('set_session_name')
                return name, remaining
            log.debug("[ToolManager.parse_set_session_name] Not found")
            return None

    def parse_load_skill(self, text):
            """Parse load_skill fence from AI output. Returns (skill_name, remaining_text) or None."""
            log.debug(f"[ToolManager.parse_load_skill] Parsing {len(text)} chars")
            result = self._parse_fence(text, 'load_skill', record=True)
            if result:
                skill_name, remaining = result
                log.info(f"[ToolManager.parse_load_skill] ✓ Found | skill='{skill_name}'")
                self._flush_parse_corrections('load_skill')
                return skill_name, remaining
            log.debug("[ToolManager.parse_load_skill] Not found")
            return None

    def parse_unload_skill(self, text):
            """Parse unload_skill fence from AI output. Returns (skill_name, remaining_text) or None."""
            log.debug(f"[ToolManager.parse_unload_skill] Parsing {len(text)} chars")
            result = self._parse_fence(text, 'unload_skill', record=True)
            if result:
                skill_name, remaining = result
                log.info(f"[ToolManager.parse_unload_skill] ✓ Found | skill='{skill_name}'")
                self._flush_parse_corrections('unload_skill')
                return skill_name, remaining
            log.debug("[ToolManager.parse_unload_skill] Not found")
            return None


    # ─────────────────────────────────────────────────────────────────────────
    # File-editing subsystem: read_file / edit_file / write_file
    # ─────────────────────────────────────────────────────────────────────────

    _EDIT_MARKER_RE = re.compile(
        r'<{4,}[ \t]*(?:OLD|SEARCH)[^\n]*\r?\n(.*?)\r?\n={4,}[ \t]*\r?\n(.*?)'
        r'(?:\r?\n>{4,}[ \t]*(?:NEW|REPLACE)[^\n]*|\Z)',
        re.DOTALL)
    _OPT_LINE_RE = re.compile(r'^[ \t]*(flags|lines)[ \t]*:[ \t]*(\S[^\n]*?)[ \t]*$',
                              re.IGNORECASE)
    _GREP_OPT_RE = re.compile(
        r'^[ \t]*(path|glob|type|output|output_mode|case|case_insensitive|'
        r'line_numbers|before|after|context|only_matching|multiline|head_limit|'
        r'max|ignore_common)[ \t]*:[ \t]*(\S[^\n]*?)[ \t]*$', re.IGNORECASE)

    def _split_file_body(self, content, annotation, consume_options=True):
        """Common body head parsing for the file tools: line 1 = path, then
        optional 'flags:'/'lines:' option lines, then the payload. A path the
        model misplaced onto the OPENER line (instead of an annotation) is
        recovered."""
        lines = content.split('\n')
        # drop leading blank lines
        while lines and not lines[0].strip():
            lines.pop(0)
        path = lines.pop(0).strip() if lines else ''
        if not _looks_like_path(path) and _looks_like_path(annotation):
            # ```read_file: D:\proj\file.py  — opener carried the PATH
            if path:
                lines.insert(0, path)
            path = annotation.strip()
            annotation = ''
        opts = {}
        while consume_options and lines:
            m = self._OPT_LINE_RE.match(lines[0])
            if not m:
                break
            opts[m.group(1).lower()] = m.group(2).strip()
            lines.pop(0)
        return path, annotation, opts, '\n'.join(lines)

    @staticmethod
    def _parse_line_range(raw):
        """'40-160' / '40:160' / '40' -> (40, 160) or (40, None)."""
        m = re.match(r'^(\d+)[ \t]*[-:][ \t]*(\d+)$', str(raw or '').strip())
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.match(r'^(\d+)$', str(raw or '').strip())
        if m:
            return int(m.group(1)), None
        return None, None

    def parse_read_file(self, text):
        """Returns ({'path','start','count','annotation'}, remaining) or None."""
        result = self._parse_fence(text, 'read_file', record=True)
        if not result:
            return None
        content, remaining, annotation = result
        path, annotation, opts, _rest = self._split_file_body(content, annotation)
        start, end = self._parse_line_range(opts.get('lines'))
        spec = {'path': path, 'annotation': annotation,
                'start': start or 1,
                'count': (end - (start or 1) + 1) if (start and end) else 200}
        self.work.interpreter.last_annotation = annotation or None
        self._flush_parse_corrections('read_file')
        log.info(f"[ToolManager.parse_read_file] ✓ path='{path}' | lines={opts.get('lines')}")
        return spec, remaining

    def parse_edit_file(self, text):
        """Returns ({'path','old','new','replace_all','start','end','annotation',
        'error'}, remaining) or None."""
        result = self._parse_fence(text, 'edit_file', record=True)
        if not result:
            return None
        content, remaining, annotation = result
        path, annotation, opts, payload = self._split_file_body(content, annotation)
        spec = {'path': path, 'annotation': annotation, 'old': None, 'new': None,
                'replace_all': 'all' in (opts.get('flags', '').lower()),
                'start': None, 'end': None, 'error': None}
        m = self._EDIT_MARKER_RE.search(payload)
        if m:
            spec['old'], spec['new'] = m.group(1), m.group(2)
        elif opts.get('lines'):
            spec['start'], spec['end'] = self._parse_line_range(opts['lines'])
            if spec['end'] is None:
                spec['end'] = spec['start']
            spec['new'] = payload
        else:
            spec['error'] = ("edit_file needs an OLD/NEW marker block "
                             "(<<<<<<< OLD / ======= / >>>>>>> NEW) or a "
                             "'lines: A-B' range plus the new text")
        self.work.interpreter.last_annotation = annotation or None
        self._flush_parse_corrections('edit_file')
        log.info(f"[ToolManager.parse_edit_file] ✓ path='{path}' | "
                 f"mode={'range' if spec['start'] else 'anchor'} | err={spec['error']}")
        return spec, remaining

    def parse_write_file(self, text):
        """Returns ({'path','content','annotation'}, remaining) or None.
        EVERYTHING after the path line is content, verbatim."""
        result = self._parse_fence(text, 'write_file', record=True)
        if not result:
            return None
        content, remaining, annotation = result
        path, annotation, _opts, payload = self._split_file_body(
            content, annotation, consume_options=False)
        spec = {'path': path, 'annotation': annotation, 'content': payload}
        self.work.interpreter.last_annotation = annotation or None
        self._flush_parse_corrections('write_file')
        log.info(f"[ToolManager.parse_write_file] ✓ path='{path}' | "
                 f"content_len={len(payload)}")
        return spec, remaining

    def _emit_file_op_card(self, tool, path, added=None, removed=None,
                           detail="", created=False, read_range="", rejected=False):
        """Structured file-op UI event -> chat card (thread-safe signal)."""
        try:
            from systema.execution import file_tools as ft
            self.approval_signal.file_op.emit({
                'tool': tool,
                'path': str(path),
                'display': ft.short_display(path),
                'added': added, 'removed': removed,
                'created': created, 'read_range': read_range,
                'detail': detail, 'rejected': rejected,
            })
        except Exception as e:
            log.debug(f"[ToolManager._emit_file_op_card] skipped: {e}")

    def _session_id(self):
        try:
            eng = self.ai_engine
            return getattr(eng, 'current_session_id', '') or ''
        except Exception:
            return ''

    def _journal_settings(self):
        try:
            s = self.settings_callback() if self.settings_callback else {}
        except Exception:
            s = {}
        return (s.get('file_history_enabled', True),
                s.get('file_history_git', False))

    def run_read_file(self, spec):
        """Read-only — never gated. Returns the observation for the model."""
        from systema.execution import file_tools as ft
        if not spec.get('path'):
            return "ERROR:\nread_file needs a file path on the first fence line."
        obs = ft.read_file(spec['path'], spec.get('start', 1), spec.get('count', 200))
        if not obs.startswith("ERROR"):
            shown = obs.splitlines()[0].split('|')[1].strip() if '|' in obs.splitlines()[0] else ''
            self._emit_file_op_card('read_file', ft.resolve_path(spec['path']),
                                    read_range=shown, detail=obs)
        return obs

    def run_edit_file(self, spec):
        """Anchor / range edit with approval gate + journal. Returns observation."""
        from systema.execution import file_tools as ft
        if spec.get('error'):
            return "ERROR:\n" + spec['error']
        if not spec.get('path'):
            return "ERROR:\nedit_file needs a file path on the first fence line."
        if spec.get('old') is not None:
            new_content, err, stats = ft.prepare_edit(
                spec['path'], spec['old'], spec['new'] or '',
                replace_all=spec.get('replace_all', False))
        else:
            new_content, err, stats = ft.prepare_edit_lines(
                spec['path'], spec['start'], spec['end'], spec['new'] or '')
        if err:
            return "ERROR:\n" + err
        path = ft.resolve_path(spec['path'])
        old_content = ft.read_current(path)
        approved, final_content, reason = self._check_file_op_approval(
            path, old_content, new_content, 'edit_file')
        if not approved:
            msg = "File edit rejected by user" if not reason else reason
            self._emit_file_op_card('edit_file', path, rejected=True, detail=msg)
            return "ERROR:\n" + msg
        journal_on, git_on = self._journal_settings()
        jid = file_journal.record(path, 'edit_file', self._session_id()) if journal_on else None
        ok, _existed, werr = ft.apply_write(path, final_content)
        if not ok:
            return f"ERROR:\ncould not write {path}: {werr}"
        if git_on:
            file_journal.shadow_commit(path, 'edit_file')
        added, removed = ft.diff_stats(old_content, final_content)
        self._emit_file_op_card('edit_file', path, added=added, removed=removed,
                                detail=self._op_diff_text(old_content, final_content))
        self.last_file_op_journal_id = jid
        return (f"EDITED {path} | +{added} -{removed} lines"
                f" | verify with read_file if the change was delicate")

    def run_write_file(self, spec):
        """Create/overwrite with approval gate + journal. Returns observation."""
        from systema.execution import file_tools as ft
        if not spec.get('path'):
            return "ERROR:\nwrite_file needs a file path on the first fence line."
        path = ft.resolve_path(spec['path'])
        old_content = ft.read_current(path)
        new_content = spec.get('content', '')
        approved, final_content, reason = self._check_file_op_approval(
            path, old_content, new_content, 'write_file')
        if not approved:
            msg = "File write rejected by user" if not reason else reason
            self._emit_file_op_card('write_file', path, rejected=True, detail=msg)
            return "ERROR:\n" + msg
        journal_on, git_on = self._journal_settings()
        jid = file_journal.record(path, 'write_file', self._session_id()) if journal_on else None
        ok, existed, werr = ft.apply_write(path, final_content)
        if not ok:
            return f"ERROR:\ncould not write {path}: {werr}"
        if git_on:
            file_journal.shadow_commit(path, 'write_file')
        added, removed = ft.diff_stats(old_content, final_content)
        self._emit_file_op_card('write_file', path, added=added, removed=removed,
                                created=not existed,
                                detail=self._op_diff_text(old_content, final_content))
        self.last_file_op_journal_id = jid
        verb = "CREATED" if not existed else "OVERWROTE"
        return f"{verb} {path} | +{added} -{removed} lines"

    def parse_grep(self, text):
        """Returns (spec, remaining) or None. Line 1 = the regex pattern; the rest
        are 'key: value' opts (path/glob/type/output/context/...)."""
        result = self._parse_fence(text, 'grep', record=True)
        if not result:
            return None
        content, remaining, annotation = result
        lines = content.split('\n')
        while lines and not lines[0].strip():
            lines.pop(0)
        pattern = lines.pop(0) if lines else ''
        opts = {}
        for ln in lines:
            m = self._GREP_OPT_RE.match(ln)
            if m:
                opts[m.group(1).lower()] = m.group(2).strip()

        def _b(val, default):
            s = str(val).strip().lower() if val is not None else ''
            if s in ('true', '1', 'yes', 'on'):
                return True
            if s in ('false', '0', 'no', 'off'):
                return False
            return default

        def _i(val, default):
            try:
                return int(str(val).strip())
            except (TypeError, ValueError):
                return default

        om = (opts.get('output') or opts.get('output_mode')
              or 'files_with_matches').strip().lower()
        if om not in ('files_with_matches', 'content', 'count'):
            om = 'files_with_matches'
        spec = {
            'pattern': pattern,
            'path': opts.get('path', '').strip() or '.',
            'glob': opts.get('glob', '').strip() or None,
            'type': opts.get('type', '').strip() or None,
            'output_mode': om,
            'case_insensitive': _b(opts.get('case', opts.get('case_insensitive')), False),
            'line_numbers': _b(opts.get('line_numbers'), True),
            'before': _i(opts.get('before'), 0),
            'after': _i(opts.get('after'), 0),
            'context': _i(opts.get('context'), 0),
            'only_matching': _b(opts.get('only_matching'), False),
            'multiline': _b(opts.get('multiline'), False),
            'head_limit': _i(opts.get('head_limit', opts.get('max')), None),
            'ignore_common': _b(opts.get('ignore_common'), True),
            'annotation': annotation,
            'error': None,
        }
        if not pattern.strip():
            spec['error'] = "grep needs a regex pattern on the first line."
        self.work.interpreter.last_annotation = annotation or None
        self._flush_parse_corrections('grep')
        log.info(f"[ToolManager.parse_grep] ✓ pattern={pattern!r} | "
                 f"path='{spec['path']}' | mode='{om}'")
        return spec, remaining

    def run_grep(self, spec):
        """ripgrep-style search. Read-only (no approval gate). Returns observation."""
        from systema.execution import file_tools as ft
        if spec.get('error'):
            return "ERROR:\n" + spec['error']
        obs = ft.grep(
            spec['pattern'], path=spec.get('path', '.'),
            glob=spec.get('glob'), type=spec.get('type'),
            output_mode=spec.get('output_mode', 'files_with_matches'),
            case_insensitive=spec.get('case_insensitive', False),
            line_numbers=spec.get('line_numbers', True),
            before=spec.get('before', 0), after=spec.get('after', 0),
            context=spec.get('context', 0),
            only_matching=spec.get('only_matching', False),
            multiline=spec.get('multiline', False),
            head_limit=spec.get('head_limit'),
            ignore_common=spec.get('ignore_common', True))
        try:
            summary = obs.split('\n', 1)[0][:90]
            self._emit_file_op_card(
                'grep', ft.resolve_path(spec.get('path', '.')),
                read_range=summary, detail=obs)
        except Exception as e:
            log.debug(f"[ToolManager.run_grep] card skipped: {e}")
        return obs

    @staticmethod
    def _op_diff_text(old, new, limit=400):
        """Unified diff for the card's expandable body (capped)."""
        import difflib
        lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                          lineterm='', n=2))[2:]
        if len(lines) > limit:
            lines = lines[:limit] + [f"... ({len(lines) - limit} more diff lines)"]
        return "\n".join(lines)

    def _global_bypass(self):
        """True when the user has flipped the global 'Bypass all security' setting
        (Settings > Security). Overrides the whole policy — every op auto-approves.
        Distinct from self.bypass_security (a per-background-task opt-out)."""
        try:
            s = self.settings_callback() if self.settings_callback else {}
            return bool((s or {}).get('security_bypass_all', False))
        except Exception:
            return False

    def _check_file_op_approval(self, path, old_text, new_text, tool):
        """The supervised-execution ladder for a FILE op (same order as
        _check_supervised_execution, with a synthesized file_create/file_edit
        finding and the per-hunk diff approval dialog).

        Returns (approved, final_new_text, reject_reason)."""
        if self.bypass_security or self._global_bypass():
            self._audit_decision(f"{tool}: {path}", tool, 'auto',
                                 'task-bypass' if self.bypass_security else 'user-bypass')
            return True, new_text, ''

        # Background-task policy gate (non-interactive) — runs before the
        # supervised logic so a background task's file op is decided purely from
        # its allow/deny grid, never a dialog.
        if self.background_policy is not None:
            try:
                from systema.security import code_guard as guard
                category = guard.CAT_FILE_EDIT if old_text else guard.CAT_FILE_CREATE
            except Exception:
                category = 'file_edit' if old_text else 'file_create'
            if self._bg_policy_for(category) == 'deny':
                self._audit_decision(f"{tool}: {path}", tool, 'denied', 'task-policy')
                try:
                    if callable(self.background_alert):
                        self.background_alert(category, str(path))
                except Exception:
                    pass
                return (False, new_text,
                        f"blocked by this task's security policy ({category} is denied). "
                        f"Find another approach that does not need it.")
            self._audit_decision(f"{tool}: {path}", tool, 'auto', 'task-policy')
            return True, new_text, ''

        try:
            settings = self.settings_callback() if self.settings_callback else {}
        except Exception:
            settings = {}
        supervised = (self.supervised_execution
                      if self.supervised_execution is not None
                      else settings.get('supervised_execution', True))
        if not supervised:
            self._audit_decision(f"{tool}: {path}", tool, 'auto', 'unsupervised')
            return True, new_text, ''
        try:
            from systema.security import code_guard as guard
            category = guard.CAT_FILE_EDIT if old_text else guard.CAT_FILE_CREATE
            finding = guard.Finding(category=category, severity=guard.SEV_CAUTION,
                                    line=0, snippet=str(path),
                                    note=f"{tool} via the file subsystem")
            engine = guard.PolicyEngine(settings)
            decision, cats = engine.decide([finding])
            if decision == guard.POLICY_DENY:
                self._audit_decision(f"{tool}: {path}", tool, 'denied', 'policy')
                self.approval_signal.system_message.emit(
                    f"Blocked by your security policy ({category}). "
                    "Adjust it in Settings > Security to allow this.")
                return False, new_text, f"blocked by security policy ({category})"
            _sess = getattr(self, 'session_allowed_categories', None) or set()
            if category in _sess or decision == guard.POLICY_ALLOW:
                self._audit_decision(f"{tool}: {path}", tool, 'auto',
                                     'session-allow' if category in _sess else 'policy')
                return True, new_text, ''
            self._pending_scan_findings = [finding]
        except Exception as e:
            log.error(f"[ToolManager._check_file_op_approval] security layer error "
                      f"(falling back to prompt): {type(e).__name__}: {e}")

        # Prompt: the approval dialog opens in file-edit mode (per-hunk diff).
        self._last_reject_reason = ""
        try:
            approval_event = threading.Event()
            result = {'approved': False, 'modified': new_text}

            def callback(approved, modified_code):
                result['approved'] = approved
                result['modified'] = modified_code
                approval_event.set()

            self._pending_file_edit = {'path': str(path), 'old': old_text,
                                       'new': new_text, 'tool': tool}
            self.approval_signal.request_approval.emit(new_text, tool, callback)
            if not approval_event.wait(timeout=300):
                self._audit_decision(f"{tool}: {path}", tool, 'rejected', 'timeout')
                return False, new_text, 'approval timed out'
            self._audit_decision(f"{tool}: {path}", tool,
                                 'approved' if result['approved'] else 'rejected',
                                 'user')
            reason = '' if result['approved'] else (self._last_reject_reason or '')
            if reason:
                reason = "File change rejected by user\nREASON: " + reason
            return result['approved'], result['modified'], reason
        except Exception as e:
            log.error(f"[ToolManager._check_file_op_approval] dialog error: {e}")
            return True, new_text, ''

    # ─────────────────────────────────────────────────────────────────────────
    # Single-exec policy enforcement
    # ─────────────────────────────────────────────────────────────────────────

    def enforce_single_exec_policy(self, text):
        """
        Scan text for code-execution tool fences (python_interpreter). If more
        than one is found, keep only the first and drop the rest.
        set_session_name is intentionally EXEMPT from this policy.

        Returns:
            tuple: (cleaned_text, violation_bool, violation_msg)
        """
        log.info(f"[ToolManager.enforce_single_exec_policy] Scanning {len(text)} chars for policy violations")
        pattern = r'`{3,}[ \t]*([\w-]+)[^\n]*\n.*?`{3,}'
        matches = []
        for match in re.finditer(pattern, text, re.DOTALL):
            canonical, _exact = self._resolve_tool_tag(match.group(1))
            if canonical and canonical in self._exec_tool_keys:
                matches.append((match, canonical))

        if len(matches) <= 1:
            log.debug("[ToolManager.enforce_single_exec_policy] No violation — returning text unchanged")
            return text, False, ""

        extras = matches[1:]
        with self._exec_violations_lock:
            self.exec_violations += 1
            _v = self.exec_violations
        dropped_names = [name for _, name in extras]
        violation_msg = (
            f"[POLICY] {len(extras)} extra code-execution tool call(s) were dropped "
            f"({', '.join(dropped_names)}). Only the first call was kept. "
            f"Total violations this session: {_v}."
        )
        log.warning(f"[ToolManager.enforce_single_exec_policy] ✗ POLICY VIOLATION #{_v} — "
                    f"dropping {dropped_names}")

        # Notify the user via chat window (signal is thread-safe)
        self.approval_signal.system_message.emit(
            f"**NOTICE:** The model emitted {len(extras)} extra code-execution "
            f"call(s) in one response ({', '.join(dropped_names)}). Only the first "
            f"call was kept and executed — the rest were dropped so the observe/act "
            f"loop stays one step at a time.\n"
            f"To reduce how often this happens: use a more capable model, wire up a "
            f"provider script (Settings ▸ Providers) so you can reach one, and turn "
            f"on native tool-calling if your provider supports it — native mode makes "
            f"the model call one tool per turn instead of emitting several at once.\n"
            f"Total violations this session: {_v}"
        )

        for match, _ in reversed(extras):
            text = text[:match.start()] + text[match.end():]

        log.info(f"[ToolManager.enforce_single_exec_policy] ✓ Policy enforced")
        return text.strip(), True, violation_msg

    # ─────────────────────────────────────────────────────────────────────────
    # Supervised execution helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _bg_policy_for(self, category):
        """Resolve a single category against the background policy. A '*' key is
        the catch-all; an unlisted category defaults to 'deny' (fail-safe)."""
        pol = self.background_policy or {}
        if category in pol:
            return pol[category]
        return pol.get('*', 'deny')

    def _decide_background_policy(self, code, execution_type):
        """Non-interactive gate for background tasks. Scans the code, maps every
        risky operation's category to the task's allow/deny policy, and blocks on
        the FIRST denied category. Returns (approved, block_reason). On a block it
        also fires the background_alert callback so the main chat is notified.

        Defensive: any fault in the safety layer denies (a background task must
        never run unreviewed risky code because scanning hiccupped)."""
        try:
            from systema.security import code_guard as guard
            findings = guard.scan_code(code)
            try:
                _ns = self.tools['python'].namespace if 'python' in self.tools else None
                findings = guard.refine_file_ops(findings, code, _ns)
            except Exception:
                pass
            # Only real operations gate. INFO-level signals (bare import, chdir,
            # print) never block on their own — same rule as interactive mode.
            risky = [f for f in findings
                     if f.severity in (guard.SEV_CAUTION, guard.SEV_DANGER)]
            denied = sorted({f.category for f in risky
                             if self._bg_policy_for(f.category) == 'deny'})
            if denied:
                guard.AuditLog.record(code=code, execution_type=execution_type,
                                      decision='denied', source='task-policy',
                                      findings=findings, extra={'categories': denied})
                cat_list = ", ".join(denied)
                log.warning(f"[ToolManager._decide_background_policy] blocked — "
                            f"denied categories {denied}")
                try:
                    if callable(self.background_alert):
                        self.background_alert(cat_list, "")
                except Exception as _e:
                    log.error(f"[ToolManager._decide_background_policy] alert error: {_e}")
                reason = (f"blocked by this task's security policy "
                          f"({cat_list} {'is' if len(denied) == 1 else 'are'} denied). "
                          f"Find another approach that does not need it — do NOT retry "
                          f"the same operation.")
                return False, reason
            guard.AuditLog.record(code=code, execution_type=execution_type,
                                  decision='auto', source='task-policy', findings=findings)
            return True, ""
        except Exception as e:
            log.error(f"[ToolManager._decide_background_policy] security layer error — "
                      f"denying by default: {type(e).__name__}: {e}")
            return False, ("blocked — the task security layer could not verify this "
                           "operation. Try a simpler, clearly-safe approach.")

    def _check_supervised_execution(self, code, execution_type):
        """
        Check if supervised execution is enabled and get approval if needed.
        FIXED: Now properly handles approval dialog on main thread.

        Args:
            code: Code to execute
            execution_type: audit label (normally 'python_interpreter')

        Returns:
            tuple: (approved: bool, modified_code: str)
        """
        log.info(f"[ToolManager._check_supervised_execution] execution_type='{execution_type}' | "
                 f"code_len={len(code)} | has_settings_callback={self.settings_callback is not None}")

        # Full bypass — a background task explicitly opted out of supervision AND
        # the security gate. Auto-approve, but still leave an audit trail.
        if self.bypass_security or self._global_bypass():
            _why = 'task-bypass' if self.bypass_security else 'user-bypass'
            log.info(f"[ToolManager._check_supervised_execution] {_why} — auto-approving")
            self._audit_decision(code, execution_type, 'auto', _why)
            return True, code

        # Background-task policy gate — runs BEFORE the interactive supervised
        # logic (a background task pins supervised_execution=False, which would
        # otherwise auto-run everything). Non-interactive: deny -> block with an
        # actionable observation + alert the main chat; otherwise run.
        if self.background_policy is not None:
            approved, block_reason = self._decide_background_policy(code, execution_type)
            if not approved:
                self._last_policy_block_msg = block_reason
                return False, code
            return True, code

        # Resolve settings + whether supervised prompting is on.
        # self.supervised_execution overrides the settings_callback when set.
        try:
            settings = self.settings_callback() if self.settings_callback else {}
        except Exception:
            settings = {}
        if self.supervised_execution is not None:
            supervised_enabled = self.supervised_execution
        else:
            supervised_enabled = settings.get('supervised_execution', True)  # Default ON
        log.debug(f"[ToolManager._check_supervised_execution] supervised_enabled={supervised_enabled}")

        # ── Supervised Execution = master kill-switch ────────────────────────
        # When it is OFF the user has chosen to FULLY trust the AI: auto-approve
        # everything with no prompt, and do NOT apply the policy at all — not even
        # a 'deny' category blocks. The whole allow / ask / deny policy (and
        # review_safe_code) only governs behavior while Supervised is ON.
        if not supervised_enabled:
            log.info("[ToolManager._check_supervised_execution] Supervised OFF — "
                     "auto-approving (master kill-switch, policy not applied)")
            self._audit_decision(code, execution_type, 'auto', 'unsupervised')
            return True, code

        # ── Security layer: static scan -> policy -> session allow-list ──────
        # Layered like a mature agent harness. Defensive: a fault in the safety
        # layer must never itself block a run — fall back to plain supervision.
        self._pending_scan_findings = None
        try:
            from systema.security import code_guard as guard
            findings = guard.scan_code(code)
            # Namespace-aware refinement: resolve write targets to create vs edit
            # where possible (see code_guard.refine_file_ops); no-op if unavailable.
            try:
                _ns = self.tools['python'].namespace if 'python' in self.tools else None
                findings = guard.refine_file_ops(findings, code, _ns)
            except Exception:
                pass
            engine = guard.PolicyEngine(settings)

            # The gate is POLICY-authoritative, not severity-authoritative. A
            # category's rule (ask / allow / deny) decides what happens to the
            # real operations it covers — a write set to 'ask' prompts even
            # though a lone write is only a 'caution', which is exactly the hole
            # that let create/edit slip through before.

            # 1. Hard policy deny — blocks in either mode. Deny is absolute: it
            #    fires on ANY finding in a denied category, even an INFO-level
            #    signal like a bare `import`.
            decision, cats = engine.decide(findings)
            if decision == guard.POLICY_DENY:
                guard.AuditLog.record(code=code, execution_type=execution_type,
                                      decision='denied', source='policy',
                                      findings=findings, extra={'categories': cats})
                log.warning(f"[ToolManager._check_supervised_execution] policy DENY on {cats} — blocking")
                self.approval_signal.system_message.emit(
                    "Blocked by your security policy (" + ", ".join(cats) + "). "
                    "Adjust it in Settings > Security to allow this.")
                return False, code

            # 2. Gate on the REAL operations (caution/danger). INFO-level signals
            #    (a bare `import`, os.chdir, plain print) never force a prompt on
            #    their own — that keeps trivial code from nagging. Users who want
            #    to review absolutely everything set 'security_review_safe_code'.
            risky = [f for f in findings
                     if f.severity in (guard.SEV_CAUTION, guard.SEV_DANGER)]
            if not risky and not settings.get('security_review_safe_code', False):
                guard.AuditLog.record(code=code, execution_type=execution_type,
                                      decision='auto', source='safe', findings=findings)
                log.debug("[ToolManager._check_supervised_execution] no caution/danger ops — auto-approving safe code")
                return True, code

            # 3. Session allow-list — the user chose "don't ask again this session"
            #    for these operation types in an earlier approval (ephemeral, wiped
            #    on restart). Auto-approve when EVERY risky category of this run is
            #    session-allowed. Deny (step 1) already took precedence, so a denied
            #    category can never be silenced this way.
            _sess = getattr(self, 'session_allowed_categories', None) or set()
            _risky_cats = {f.category for f in risky}
            if risky and _risky_cats and _risky_cats.issubset(_sess):
                guard.AuditLog.record(code=code, execution_type=execution_type,
                                      decision='auto', source='session-allow',
                                      findings=findings, extra={'categories': sorted(_risky_cats)})
                log.info(f"[ToolManager._check_supervised_execution] session-allowed "
                         f"{sorted(_risky_cats)} — auto-approving")
                return True, code

            # 4. Among the real operations, the per-category policy decides.
            #    'allow' for every risky category => auto-approve (an explicit
            #    allow-list that overrides supervision). Otherwise a category set
            #    to 'ask' forces the prompt regardless of the Supervised toggle.
            risky_decision, risky_cats = engine.decide(risky)
            if risky and risky_decision == guard.POLICY_ALLOW:
                guard.AuditLog.record(code=code, execution_type=execution_type,
                                      decision='auto', source='policy',
                                      findings=findings, extra={'categories': risky_cats})
                log.info(f"[ToolManager._check_supervised_execution] allow-listed {risky_cats} — auto-approving")
                return True, code

            # 5. Prompt when Supervised is ON, or (Supervised OFF) whenever a
            #    risky category the code uses is set to 'ask'. review_safe_code
            #    also forces the prompt for otherwise-safe code that reached here.
            need_prompt = (supervised_enabled
                           or (risky and risky_decision == guard.POLICY_ASK)
                           or settings.get('security_review_safe_code', False))
            if not need_prompt:
                guard.AuditLog.record(code=code, execution_type=execution_type,
                                      decision='auto',
                                      source=('policy' if findings else 'unsupervised'),
                                      findings=findings)
                log.debug("[ToolManager._check_supervised_execution] auto-approved (no prompt required)")
                return True, code
            self._pending_scan_findings = findings   # reused for the post-dialog audit
        except Exception as e:
            log.error(f"[ToolManager._check_supervised_execution] security layer error "
                      f"(continuing with plain supervision): {type(e).__name__}: {e}")
            if not supervised_enabled:
                return True, code

        # Show approval dialog on main thread
        log.debug("[ToolManager._check_supervised_execution] Emitting request_approval signal to main thread")
        self._last_reject_reason = ""   # cleared per run; set by the dialog on Reject
        try:
            # Create event to block worker thread until approval is received
            approval_event = threading.Event()
            approval_result = {'approved': True, 'modified_code': code}

            def callback(approved, modified_code):
                """Callback function executed on main thread after dialog closes"""
                log.debug(f"[ToolManager._check_supervised_execution] Approval callback received | "
                          f"approved={approved} | code_modified={modified_code != code}")
                approval_result['approved'] = approved
                approval_result['modified_code'] = modified_code
                approval_event.set()

            # Emit signal to main thread (will show dialog there)
            self.approval_signal.request_approval.emit(code, execution_type, callback)

            # Wait for approval (blocks worker thread, but not main thread)
            log.debug("[ToolManager._check_supervised_execution] Waiting for user approval (blocking worker thread)...")
            approved = approval_event.wait(timeout=300)
            if not approved:
                log.error("[ToolManager._check_supervised_execution] Approval wait timed out after 300s — denying by default")
                self._audit_decision(code, execution_type, 'rejected', 'timeout')
                return False, code

            log.info(f"[ToolManager._check_supervised_execution] User decision received | "
                     f"approved={approval_result['approved']}")
            self._audit_decision(approval_result['modified_code'], execution_type,
                                 'approved' if approval_result['approved'] else 'rejected',
                                 'user')
            return approval_result['approved'], approval_result['modified_code']

        except Exception as e:
            log.error(f"[ToolManager._check_supervised_execution] ✗ Error showing approval dialog: "
                      f"{type(e).__name__}: {e}")
            # If dialog fails, approve by default (but log error)
            return True, code

    def _audit_decision(self, code, execution_type, decision, source):
        """Append one entry to the security audit log, reusing the scan from the
        pre-dialog phase when available. Never raises."""
        try:
            from systema.security import code_guard as guard
            guard.AuditLog.record(code=code, execution_type=execution_type,
                                  decision=decision, source=source,
                                  findings=getattr(self, '_pending_scan_findings', None))
        except Exception:
            pass

    def _close_active_approval_dialog(self, approved: bool, modified_code: str):
        """Slot — always runs on main thread. Closes PC dialog when Android decides first."""
        dialog = self._active_approval_dialog
        if dialog and dialog.isVisible():
            if approved:
                dialog.result = 'accept'
                dialog.modified_code = modified_code if modified_code else dialog.code_edit.toPlainText().strip()
                dialog.accept()
            else:
                dialog.result = 'reject'
                dialog.close()

    def _show_approval_dialog_on_main_thread(self, code, execution_type, callback):
        """
        Show approval dialog on main thread (called via signal).

        Args:
            code: Code to execute
            execution_type: audit label (normally 'python_interpreter')
            callback: Function to call with (approved, modified_code)
        """
        log.info(f"[ToolManager._show_approval_dialog_on_main_thread] execution_type='{execution_type}' | "
                 f"code_len={len(code)} | has_ai_engine={self.ai_engine is not None}")
        try:
            from systema.ui.dialogs.approval_window import CodeApprovalDialog

            if not self.ai_engine:
                log.warning("[ToolManager._show_approval_dialog_on_main_thread] No AI engine — "
                            "auto-approving (no explanation available)")
                callback(True, code)
                return

            log.debug("[ToolManager._show_approval_dialog_on_main_thread] Showing CodeApprovalDialog...")
            from PyQt6.QtCore import Qt

            # ── Notify Android if connected ───────────────────────────────────
            android_bridge = None
            if callable(self._get_android_bridge):
                android_bridge = self._get_android_bridge()
            if android_bridge and getattr(android_bridge, '_conn', None) is not None:
                android_bridge._dispatch({
                    "cmd": "show_code_approval",
                    "code": code,
                    "execution_type": execution_type
                })

            # Show dialog manually so we keep a ref for Android to close it.
            # A pending file-edit payload flips the dialog into file-edit mode
            # (per-hunk diff of the proposed change instead of raw code).
            _file_edit = self._pending_file_edit
            self._pending_file_edit = None
            dialog = CodeApprovalDialog(code, execution_type, self.ai_engine,
                                        file_edit=_file_edit)
            self._active_approval_dialog = dialog
            dialog.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
            ctrl = getattr(self.ai_engine, 'controller', None)
            # Spoken heads-up so the user can answer by voice without looking
            # (setting: voice_approval_announce, default off).
            try:
                if (ctrl is not None and getattr(ctrl, 'voice_mode_active', False)
                        and (ctrl.settings or {}).get('voice_approval_announce', False)
                        and (ctrl.settings or {}).get('voice_approval_enabled', True)):
                    dialog.announce_for_voice()
            except Exception:
                pass

            # ── Compact notification card when the chat window is closed or
            # minimized (setting: approval_mini_enabled, default on). Voice
            # commands still work — they act on the full dialog, which the card
            # polls. Expand / chat-opening swaps back to the full dialog.
            use_mini = False
            try:
                chat = self._chat
                chat_visible = (chat is not None and chat.isVisible()
                                and not chat.isMinimized())
                use_mini = (ctrl is not None
                            and (ctrl.settings or {}).get('approval_mini_enabled', True)
                            and not chat_visible)
            except Exception:
                use_mini = False

            if use_mini:
                from PyQt6.QtCore import QEventLoop
                from systema.ui.dialogs.approval_notif import ApprovalNotifCard
                card = ApprovalNotifCard(dialog, ctrl,
                                         chat_getter=lambda: self._chat)
                card.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
                dialog._mini_card = card
                log.info("[ToolManager._show_approval_dialog_on_main_thread] "
                         "Chat window hidden — showing compact approval card")
                # NON-modal + local loop (exec() would be application-modal and
                # block opening the chat window, killing the auto-expand path).
                loop = QEventLoop()
                card.finished.connect(loop.quit)
                card.show()
                card.raise_()
                loop.exec()
                dialog._mini_card = None
                if card.outcome == 'accept' and dialog.result is None:
                    dialog.on_accept()
                elif card.outcome == 'reject' and dialog.result is None:
                    dialog.on_reject()

            if dialog.result is None:
                # Full review: first show, or the card asked to expand.
                dialog._expand_requested = False
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
                dialog.exec()
            self._active_approval_dialog = None

            # ── Dismiss Android dialog (sync close regardless of who decided) ─
            if android_bridge and getattr(android_bridge, '_conn', None) is not None:
                android_bridge._dispatch({"cmd": "dismiss_code_approval"})

            approved = dialog.result == 'accept'
            modified_code = dialog.modified_code if approved else code
            if not approved:
                self._last_reject_reason = getattr(dialog, 'reject_reason', '') or ''
            log.info(f"[ToolManager._show_approval_dialog_on_main_thread] Dialog closed | "
                     f"approved={approved} | code_was_modified={modified_code != code}")

            # Call callback with results
            callback(approved, modified_code)

        except Exception as e:
            log.error(f"[ToolManager._show_approval_dialog_on_main_thread] ✗ Exception: "
                      f"{type(e).__name__}: {e}")
            callback(True, code)

    # ── Timeout handling ───────────────────────────────────────────────────────

    def _show_timeout_dialog_on_main_thread(self, elapsed, exec_done_event, result_holder, user_event):
        """
        Slot — runs on main thread. Shows the timeout dialog and writes the user's
        decision into result_holder, then sets user_event.
        While the dialog is up, polls exec_done_event every 200ms so the dialog
        auto-closes if execution finishes before the user responds.
        """
        log.info(f"[ToolManager._show_timeout_dialog_on_main_thread] elapsed={elapsed}s")
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
        from systema.ui.dialogs.timeout_dialog import TimeoutDialog

        parent = QApplication.activeWindow()

        # If execution already finished while the signal was queued, bail
        if exec_done_event.is_set():
            log.info("[ToolManager._show_timeout_dialog_on_main_thread] Execution already done — skipping dialog")
            result_holder.append(0)
            user_event.set()
            return

        dialog = TimeoutDialog(elapsed, parent)

        # ── Mirror to Android if a phone is connected ─────────────────────────
        android_bridge = self._get_android_bridge() if callable(self._get_android_bridge) else None
        if android_bridge and getattr(android_bridge, '_conn', None) is not None:
            def _on_phone_timeout(seconds):
                # Runs on the bridge recv thread — apply on the GUI thread.
                def _apply():
                    dialog._result = seconds
                    dialog.accept()
                android_bridge._run_on_main(_apply)
            android_bridge.request_timeout(elapsed, _on_phone_timeout)

        # Poll exec_done_event every 200ms to auto-close if execution finishes
        _poll = QTimer()
        _poll.setInterval(200)

        def _check():
            if exec_done_event.is_set():
                _poll.stop()
                log.info("[ToolManager._show_timeout_dialog_on_main_thread] Execution finished — closing dialog")
                dialog.close()

        _poll.timeout.connect(_check)
        _poll.start()

        try:
            dialog.exec()
        finally:
            _poll.stop()
            if android_bridge and getattr(android_bridge, '_conn', None) is not None:
                android_bridge.dismiss_timeout()

        decision = dialog.result_value
        log.info(f"[ToolManager._show_timeout_dialog_on_main_thread] User decision: {decision}")
        result_holder.append(decision)
        user_event.set()

    def _handle_execution_timeout(self, thread_ident, done_event):
        """
        Called by PythonInterpreter when execution times out.
        Blocks the worker thread until the user decides via the dialog on the main thread.
        Returns: int — seconds to extend (0 = kill).
        """
        elapsed = self.settings_callback().get('tool_execution_timeout_seconds', 300) if self.settings_callback else 300
        log.info(f"[ToolManager._handle_execution_timeout] Timeout after {elapsed}s")

        if self.supervised_execution is False:
            # Unattended context (background task) — nothing will ever answer
            # this dialog. Fail closed: kill the execution.
            log.warning(f"[ToolManager._handle_execution_timeout] Unattended context — "
                        f"killing execution (no dialog to show)")
            return 0

        result_holder = []       # main thread will append decision
        user_event = threading.Event()

        self.approval_signal.timeout_signal.emit(elapsed, done_event, result_holder, user_event)
        if not user_event.wait(timeout=120):
            log.error("[ToolManager._handle_execution_timeout] Dialog wait timed out — killing execution")
            return 0

        decision = result_holder[0] if result_holder else 0
        log.info(f"[ToolManager._handle_execution_timeout] Decision: {decision}")
        return decision

    # ─────────────────────────────────────────────────────────────────────────
    # Run methods
    # ─────────────────────────────────────────────────────────────────────────

    def interrupt_running_code(self, notice: str = None) -> bool:
        """Interrupt the work-mode code that is currently executing.

        Raises KeyboardInterrupt in the interpreter's exec thread so the run
        stops while keeping whatever it printed so far, and queues `notice`
        to be appended to that captured output (so the AI learns it was
        interrupted and why). The blocked worker then finishes its cycle
        naturally — no thread is orphaned and work_code_active(False) fires
        normally, tearing down the live console.

        Returns True if an interrupt was delivered to a running execution."""
        self._pending_interrupt_notice = notice
        interp = self.tools.get('python')
        if interp is not None and hasattr(interp, 'interrupt_current'):
            delivered = interp.interrupt_current()
            if not delivered:
                # Nothing was running — don't leave a stale notice queued.
                self._pending_interrupt_notice = None
            return delivered
        self._pending_interrupt_notice = None
        return False

    def run_python_interpreter(self, code):
        """
        Execute code in the python interpreter.
        AI will see the output and can chain more executions.

        Wraps PythonInterpreter.execute() with the work.interpreter.is_running flag
        and work.interpreter.last_code tracking for interrupt/UI state propagation.

        Returns:
            str: Formatted output for AI
        """
        code_preview = code.strip()[:80].replace('\n', '↵')
        log.info(f"[ToolManager.run_python_interpreter] ── Executing code | "
                 f"code_len={len(code)} | preview='{code_preview}' ──")

        # NOTE: the explicit `exit` sentinel was removed — work mode now ends when
        # the AI replies with no python_interpreter call (auto-exit, handled in
        # AIEngine). A bare `exit`/empty work-call is folded into that finish
        # path BEFORE this method is called, so it never reaches execution.

        # Capability gate — real enforcement, not just prompt text
        if not self.allow_workmode:
            log.warning("[ToolManager.run_python_interpreter] Blocked — allow_workmode is False for this session")
            return "ERROR:\npython_interpreter is disabled for this session."

        # Check supervised execution (now properly on main thread)
        log.debug("[ToolManager.run_python_interpreter] Checking supervised execution...")
        approved, modified_code = self._check_supervised_execution(code, 'python_interpreter')

        if not approved:
            # A background-policy block sets _last_policy_block_msg — surface that
            # actionable text directly (not the "rejected by user" wording, which
            # is wrong for an unattended task).
            policy_msg = getattr(self, '_last_policy_block_msg', None)
            if policy_msg:
                self._last_policy_block_msg = None
                log.warning("[ToolManager.run_python_interpreter] ✗ Blocked by task security policy")
                return "ERROR: " + policy_msg
            log.warning("[ToolManager.run_python_interpreter] ✗ Execution rejected by user")
            reason = getattr(self, '_last_reject_reason', '') or ''
            msg = "Code execution rejected by user"
            if reason:
                msg += f"\nREASON: {reason}"
            return "ERROR:\n" + msg

        # Use modified code if user edited it
        if modified_code != code:
            log.info("[ToolManager.run_python_interpreter] Code was modified by user in approval dialog")
        code = modified_code

        # Pull literal #@FILE blocks out before execution so their content is
        # never parsed as Python. work.interpreter.last_code keeps the full source
        # (blocks included) for the UI note / history; only exec_code runs.
        self.work.interpreter.last_code = code
        exec_code, _file_blocks = self._extract_file_blocks(code)
        if _file_blocks:
            self.tools['python'].inject_vars(_file_blocks)

        # Execute Python code (with optional timeout)
        timeout = None
        if self.settings_callback:
            timeout = self.settings_callback().get('tool_execution_timeout_seconds', None)
        log.debug(f"[ToolManager.run_python_interpreter] → Executing via PythonInterpreter | "
                  f"timeout={timeout}s")
        self.work.interpreter.is_running = True
        self.approval_signal.work_code_active.emit(True)
        try:
            result = self.tools['python'].execute(
                exec_code,
                timeout=timeout,
                timeout_callback=self._handle_execution_timeout if timeout else None
            )
        finally:
            self.work.interpreter.is_running = False
            self.approval_signal.work_code_active.emit(False)
        log.debug(f"[ToolManager.run_python_interpreter] Python result: success={result['success']} | "
                  f"stdout_len={len(result['stdout'])} | stderr_len={len(result['stderr'])} | "
                  f"has_error={bool(result['error'])} | timed_out={result.get('timed_out', False)}")

        # Format output for AI to analyze
        output_parts = []

        if result.get('timed_out'):
            output_parts.append(
                f"ERROR:\nCode execution was killed after exceeding the timeout of {timeout}s."
            )

        if result['stdout']:
            output_parts.append(f"STDOUT:\n{result['stdout']}")

        if result['stderr']:
            output_parts.append(f"STDERR:\n{result['stderr']}")

        if result['result'] is not None:
            output_parts.append(f"RESULT:\n{result['result']}")

        if result['error']:
            output_parts.append(f"ERROR:\n{result['error']}")

        # User interrupted the running code — append the notice so the partial
        # output above is preserved and the AI sees why it stopped.
        if self._pending_interrupt_notice:
            output_parts.append(self._pending_interrupt_notice)
            self._pending_interrupt_notice = None

        if not output_parts:
            output_parts.append(
                "Code executed but produced no output. "
                "RECOMMENDATION: Use print() or return values to see results!"
            )
            log.debug("[ToolManager.run_python_interpreter] No output produced — sending recommendation")

        combined = "\n\n".join(output_parts)
        log.info(f"[ToolManager.run_python_interpreter] ✓ Python interpreter output ready | "
                 f"parts={len(output_parts)} | total_len={len(combined)}")

        # ── Mirror code + output to Android bridge ────────────────────────
        try:
            ab = self._get_android_bridge() if callable(self._get_android_bridge) else None
            if ab and ab.isVisible():
                ab.add_work_execution(code, combined, annotation=self.work.interpreter.last_annotation or "")
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────

        return combined

    # ─────────────────────────────────────────────────────────────────────────
    # Prompt helpers
    # ─────────────────────────────────────────────────────────────────────────

    def get_work_prompt(self):
        """Get the prompt for work mode continuation. In native tool-calling mode
        the variant that instructs native tool calls (not fences) is used."""
        log.debug(f"[ToolManager.get_work_prompt] Building work mode prompt | "
                  f"has_last_output={self.work.last_output is not None}")
        from systema.engine.prompts.global_instructions import WORK_MODE_PROMPT, WORK_MODE_PROMPT_NATIVE

        native = False
        try:
            s = self.settings_callback() if self.settings_callback else None
            native = (s or {}).get('tool_calling_mode', 'compat') == 'native'
        except Exception:
            native = False

        template = WORK_MODE_PROMPT_NATIVE if native else WORK_MODE_PROMPT
        output = self.work.last_output or "No previous output"
        prompt = template.format(work_output=output)
        log.debug(f"[ToolManager.get_work_prompt] Prompt built | native={native} | length={len(prompt)}")
        return prompt

    def reset_python(self):
        """Reset Python interpreter state"""
        log.info("[ToolManager.reset_python] Resetting Python interpreter state")
        self.tools['python'].reset()
        log.info("[ToolManager.reset_python] ✓ Python interpreter reset complete")

    def strip_tool_calls(self, text):
        """
        Remove all tool call fences from text for display purposes.
        Does NOT execute any code — purely for cleaning stored messages before rendering.
        """
        if not text:
            log.debug("[ToolManager.strip_tool_calls] Empty text — returning as-is")
            return text
        log.debug(f"[ToolManager.strip_tool_calls] Stripping tool fences from {len(text)} char text")
        # Legacy keys included: OLD sessions containing retired-tool fences
        # (e.g. execute_code) must still render clean.
        for key in self._tool_keys + list(tool_registry.LEGACY_STRIP_KEYS):
            result = self._parse_fence(text, key)
            while result is not None:
                # _parse_fence returns 3-tuple for annotated python_interpreter,
                # 2-tuple for everything else (including plain python_interpreter)
                if len(result) == 3:
                    _, text, _ = result  # annotation discarded — not re-injected on session load
                else:
                    _, text = result
                result = self._parse_fence(text, key)
        log.debug(f"[ToolManager.strip_tool_calls] ✓ Stripped | resulting_len={len(text)}")
        return text

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helper methods — key normalisation & fuzzy matching
    # ─────────────────────────────────────────────────────────────────────────

    def _norm_key(self, key):
        """Normalise a key for fuzzy comparison — strip underscores, hyphens and
        spaces, then lowercase. So python_interpreter / 'python interpreter' /
        PythonInterpreter all resolve to the same canonical key."""
        return key.replace('_', '').replace('-', '').replace(' ', '').lower()

    def _resolve_tool_tag(self, tag, include_legacy=False):
        """Resolve a fence tag to a canonical tool key.

        Exact normalised lookup first; failing that, a UNIQUE edit-distance-1
        near-miss (weak models emit e.g. python_interpeter / load_skills). Short
        tags (<6 chars, e.g. 'python', 'bash') never fuzzy-match, so prose code
        fences can't be mistaken for tools.

        Returns (canonical_name, exact_bool); (None, False) when not a tool."""
        norm = self._norm_key(tag)
        pool = dict(self._tool_keys_norm)
        if include_legacy:
            pool.update(self._legacy_keys_norm)
        hit = pool.get(norm)
        if hit:
            return hit, True
        if len(norm) >= 6:
            cands = {c for n, c in pool.items() if _edit_distance_le1(norm, n)}
            if len(cands) == 1:
                return cands.pop(), False
        return None, False

    def _find_canonical_tool_key(self, data):
        """
        Return the canonical tool key present in a parsed JSON dict, or None.

        Handles two formats:
          NEW:    {"tool": "python_interpreter", "input": "..."}
          LEGACY: {"python_interpreter": true,   "input": "..."}
                  {"set_session_name": "Name"}

        Also handles AI inconsistencies like 'setsessionname' vs 'set_session_name'.
        """
        log.debug(f"[ToolManager._find_canonical_tool_key] Checking data keys: {list(data.keys())}")

        # ── New unified format: {"tool": "tool_name", ...} ──────────────────
        if 'tool' in data:
            tool_val = str(data.get('tool', '')).strip()
            canonical = self._tool_keys_norm.get(self._norm_key(tool_val))
            if canonical:
                log.debug(f"[ToolManager._find_canonical_tool_key] NEW format | "
                          f"'tool'='{tool_val}' → canonical='{canonical}'")
                return canonical
            else:
                log.debug(f"[ToolManager._find_canonical_tool_key] NEW format — 'tool' value '{tool_val}' "
                          f"not in registered keys — falling through to legacy check")

        # ── Legacy format: key IS the tool name ─────────────────────────────
        for k in data:
            if k == 'tool':
                # 'tool' itself is the format discriminator, not a tool name
                continue
            canonical = self._tool_keys_norm.get(self._norm_key(k))
            if canonical:
                log.debug(f"[ToolManager._find_canonical_tool_key] LEGACY format | "
                          f"key='{k}' → canonical='{canonical}'")
                return canonical

        log.debug("[ToolManager._find_canonical_tool_key] No canonical tool key found in data")
        return None

    def _has_tool_key(self, data, tool_key):
        """
        Check if data contains tool_key (or a fuzzy variant).

        Handles new format {"tool": "python_interpreter"} and
        legacy format     {"python_interpreter": true}.
        """
        target = self._norm_key(tool_key)

        # New format
        if 'tool' in data:
            result = self._norm_key(str(data['tool'])) == target
            log.debug(f"[ToolManager._has_tool_key] NEW format check | "
                      f"target='{target}' | tool_val='{data['tool']}' | match={result}")
            return result

        # Legacy format
        result = any(self._norm_key(k) == target for k in data if k != 'tool')
        log.debug(f"[ToolManager._has_tool_key] LEGACY format check | "
                  f"target='{target}' | data_keys={list(data.keys())} | match={result}")
        return result

    def _get_tool_value(self, data, field_key):
        """
        Get value for a field key using fuzzy matching.

        Works for both new and legacy formats since it simply does a
        normalised key lookup in the dict.  For legacy set_session_name the
        value lives directly under 'set_session_name'; for new format it lives
        under 'input'.  The caller decides which field_key to request.
        """
        target = self._norm_key(field_key)
        for k, v in data.items():
            if self._norm_key(k) == target:
                log.debug(f"[ToolManager._get_tool_value] Found '{field_key}' (matched key='{k}') | "
                          f"value_type={type(v).__name__} | value_preview='{str(v)[:60]}'")
                return v
        log.debug(f"[ToolManager._get_tool_value] Field '{field_key}' not found in data keys {list(data.keys())}")
        return None


