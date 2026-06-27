"""
core/tool_manager.py
Tool Manager - Simplified work environment and code execution system
FIXED: Code approval dialog now runs on main thread using Qt signals
UPDATED: GUI execution now uses subprocess with .generated folder
UPDATED: Captures and returns traceback for execute_code failures
UPDATED: Unified tool format  {"tool": "tool_name", "input": "..."}
         Legacy format {"work_environment": true, "input": "..."} still supported
UPDATED: Single-exec policy enforced — only one code tool per AI turn allowed
UPDATED: self.exec_violations counter tracks policy violations
UPDATED: parse_set_session_name uses aggressive multi-strategy extraction —
         works anywhere in text, any format, even partially malformed JSON
"""

import json
import re
import threading
import subprocess
import os
from datetime import datetime
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from core.python_interpreter import PythonInterpreter
from core.logger import _make_logger, _NoOpLogger


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("ToolManager") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


class ApprovalSignal(QObject):
    """Signal object for requesting code approval on main thread"""
    request_approval = pyqtSignal(str, str, object)  # code, execution_type, callback
    system_message   = pyqtSignal(str)               # text → chat window, main thread only
    close_approval_dialog = pyqtSignal(bool, str)  # approved, modified_code — closes active dialog
    timeout_signal   = pyqtSignal(int, object, object, object)  # elapsed, done_event, result_holder, user_event — timeout prompt
    work_code_active = pyqtSignal(bool)                          # True when code execution starts, False when it finishes


class ToolManager:
    """Manages work environment and code execution with unified tool system.

    TOOL FORMAT (new, preferred):
        {"tool": "work_environment", "input": "python code here"}
        {"tool": "execute_code",     "input": "python code here"}
        {"tool": "set_session_name", "input": "My Session Title"}

    LEGACY FORMAT (still accepted for backward compatibility):
        {"work_environment": true,   "input": "python code here"}
        {"execute_code":     true,   "input": "python code here"}
        {"set_session_name": "My Session Title"}

    Adding a new tool:
        1. Add its canonical name to self._tool_keys list.
        2. Add a parse_<tool>() method (mirrors parse_work_environment).
        3. Add execution logic if needed.
        That's it — format detection, fuzzy matching, removal and stripping
        all work automatically.
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

        # Work mode state
        self.in_work_mode = False
        self.last_work_output = None
        self.last_work_annotation = None  # set by parse_work_environment when bracket present
        self.last_work_code = None        # set by run_work_environment before execution
        self.work_code_running = False
        self._pending_interrupt_notice = None  # appended to output when user interrupts running code
        log.debug("[ToolManager.__init__] Work mode state: in_work_mode=False | last_work_output=None")

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
        # Capability gates — enforced by run_work_environment/run_execute_code,
        # not just narrated in the system prompt. Set by TaskAIEngine from task dict.
        self.allow_workmode = True
        self.allow_execute_code = True

        # Approval signal for main thread communication
        log.debug("[ToolManager.__init__] Creating ApprovalSignal and connecting to main thread handler")
        self.approval_signal = ApprovalSignal()
        self.approval_signal.request_approval.connect(self._show_approval_dialog_on_main_thread)
        self.approval_signal.system_message.connect(self._deliver_system_message)
        self.approval_signal.close_approval_dialog.connect(self._close_active_approval_dialog)
        self.approval_signal.timeout_signal.connect(self._show_timeout_dialog_on_main_thread)
        self._active_approval_dialog = None
        self._get_android_bridge = None
        log.debug("[ToolManager.__init__] ApprovalSignal connected")

        # Setup .generated folder (relative to working directory)
        self.generated_dir = os.path.join(os.getcwd(), '.generated')
        log.debug(f"[ToolManager.__init__] .generated dir path: '{self.generated_dir}'")
        self._ensure_generated_dir()

        # ── Tool registry ─────────────────────────────────────────────────────
        # Canonical tool names.  To add a new tool, append its name here and
        # implement the corresponding parse_/run_ methods.
        self._tool_keys = ['work_environment', 'execute_code', 'set_session_name', 'load_skill', 'unload_skill']
        # Maps normalised (no-underscore, lowercase) form → canonical name
        self._tool_keys_norm = {k.replace('_', '').lower(): k for k in self._tool_keys}
        log.debug(f"[ToolManager.__init__] Registered tool keys: {self._tool_keys}")
        log.debug(f"[ToolManager.__init__] Normalised tool key map: {self._tool_keys_norm}")

        # ── Code-execution tools (subject to single-call policy) ──────────────
        # set_session_name is NOT in this list — it may coexist with a code tool.
        self._exec_tool_keys = {'work_environment', 'execute_code'}
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
    # Directory helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _ensure_generated_dir(self):
        """Create .generated directory if it doesn't exist"""
        log.debug(f"[ToolManager._ensure_generated_dir] Checking: '{self.generated_dir}'")
        try:
            if not os.path.exists(self.generated_dir):
                os.makedirs(self.generated_dir)
                log.info(f"[ToolManager._ensure_generated_dir] ✓ Created .generated directory: "
                         f"'{self.generated_dir}'")
            else:
                log.debug(f"[ToolManager._ensure_generated_dir] Directory already exists — no action")
        except Exception as e:
            log.error(f"[ToolManager._ensure_generated_dir] ✗ Could not create directory: "
                      f"{type(e).__name__}: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Canonical tool registry  (single source of truth for tool schemas)
    #
    # Every tool takes ONE string input (the fence content in compat mode, or a
    # single named argument in native mode). This registry maps each tool to a
    # description + that one parameter, so native_adapters can render it into the
    # OpenAI / Anthropic / Gemini function-calling formats. The compat fence docs
    # in global_instructions remain the prompt-side rendering of the same tools.
    # ─────────────────────────────────────────────────────────────────────────
    _CANONICAL_TOOLS = {
        'work_environment': {
            'description': (
                "Run Python in a persistent workspace and SEE its output (stdout + the last "
                "expression's value). Use for multi-step tasks where you must observe results "
                "before continuing. The namespace persists across calls within a work session."
            ),
            'param': ('code', 'The Python code to execute. You receive its stdout and return value.'),
            'extra_params': [
                ('annotation',
                 "A short 3-6 word label describing what this code does (e.g. 'Reading config "
                 "file', 'Counting desktop files'). ALWAYS include it — it is shown to the user "
                 "as this step's title.",
                 False),
            ],
        },
        'execute_code': {
            'description': (
                "Run Python fire-and-forget — you will NOT see the output. Use only for side "
                "effects (launch a process, write a file, send a notification)."
            ),
            'param': ('code', 'The Python code to execute. Output is not returned to you.'),
        },
        'set_session_name': {
            'description': "Set a short, descriptive title for the current conversation.",
            'param': ('name', 'A short title — just a few words.'),
        },
        'load_skill': {
            'description': (
                "Load a skill's full instructions into your context. Use when the task matches an "
                "available skill that is not already loaded."
            ),
            'param': ('skill_name', 'The exact name of the skill to load.'),
        },
        'unload_skill': {
            'description': "Remove a previously loaded skill from your context to free space.",
            'param': ('skill_name', 'The exact name of the skill to unload.'),
        },
    }

    def get_canonical_tools(self, include_session_naming: bool = True,
                            include_skills: bool = True) -> list:
        """Return the active tools as canonical schema dicts (name/description/
        parameters), gated by the same capability flags the prompt uses. Feed the
        result to core.native_adapters.to_<dialect>_tools() for native mode."""
        active = []
        if self.allow_workmode:
            active.append('work_environment')
        if self.allow_execute_code:
            active.append('execute_code')
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
            for (ename, edesc, ereq) in spec.get('extra_params', []):
                props[ename] = {'type': 'string', 'description': edesc}
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

    def tool_calls_to_fences(self, tool_calls: list) -> str:
        """Reconstruct canonical fence text from normalized native tool calls
        (each {"name","arguments"}). This lets native-mode responses flow through
        the exact same fence-parsing pipeline as compat mode — the engine never
        has to branch on tool format below _call_provider."""
        parts = []
        for call in (tool_calls or []):
            name = call.get('name')
            spec = self._CANONICAL_TOOLS.get(name)
            if not spec:
                log.warning(f"[ToolManager.tool_calls_to_fences] Unknown tool '{name}' — skipped")
                continue
            pname = spec['param'][0]
            args = call.get('arguments', {}) or {}
            body = args.get(pname, '')
            if not isinstance(body, str):
                body = str(body)
            # work_environment carries an optional annotation. Native tool calls
            # have no fence text to hold it, so fold it back into the annotated
            # fence form (```work_environment: [label]) — otherwise the UI step
            # title defaults to a generic "code executed".
            if name == 'work_environment':
                annotation = args.get('annotation', '')
                if isinstance(annotation, str) and annotation.strip():
                    parts.append(f"```{name}: [{annotation.strip()}]\n{body}\n```")
                    continue
            parts.append(f"```{name}\n{body}\n```")
        return "\n\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # Public parse methods - Fence-based parsing (new format)
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_fence(self, text, tool_name):
        """
        Extract content from a code fence whose language identifier matches tool_name.
        Fuzzy-matches the identifier (strips underscores, lowercases) so
        work_environment / workEnvironment / workenvironment all resolve correctly.
        Returns (content, remaining_text) or None.
        For work_environment, also supports ```work_environment: [annotation] format
        and returns (content, remaining_text, annotation).
        """
        norm_target = self._norm_key(tool_name)

        # ── Special handling for work_environment with inline annotation ──
        if norm_target == "workenvironment":
            # Format: ```work_environment: [annotation]\n<code>\n```
            # Colon is optional: ```work_environment [annotation]\n<code>\n``` also works
            pattern_we = r'```[ \t]*work_environment[ \t]*:?[ \t]*\[(.*?)\][ \t]*(?:\n|\r\n)(.*?)[ \t]*```'
            match = re.search(pattern_we, text, re.DOTALL)
            if match:
                annotation = match.group(1).strip()
                content = match.group(2).strip()
                remaining = (text[:match.start()] + text[match.end():]).strip()
                log.debug(
                    f"[ToolManager._parse_fence] ✓ Matched 'work_environment' "
                    f"annotation='{annotation}' | content_len={len(content)}"
                )
                return content, remaining, annotation
            # If annotated format not found, fall through to general parser

        # ── General case for all tools (including plain work_environment) ──
        pattern = r'```[ \t]*(\w+)(?:[ \t]*\n|[ \t]+)(.*?)[ \t]*```'
        for match in re.finditer(pattern, text, re.DOTALL):
            if self._norm_key(match.group(1)) == norm_target:
                content = match.group(2).strip()
                remaining = (text[:match.start()] + text[match.end():]).strip()
                log.debug(f"[ToolManager._parse_fence] ✓ Matched '{tool_name}' | content_len={len(content)}")
                return content, remaining
        return None

    def recover_unclosed_tool_fence(self, text):
        """Recover a tool call whose closing ``` fence the model forgot.

        The #1 weak-model failure: the model opens ```work_environment but never
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
        canonical = self._tool_keys_norm.get(self._norm_key(m.group(1)))
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

    def parse_work_environment(self, text):
        """Parse work_environment fence from AI output. Returns (code, remaining_text) or None."""
        log.debug(f"[ToolManager.parse_work_environment] Parsing {len(text)} chars")

        result = self._parse_fence(text, 'work_environment')

        if result:
            if len(result) == 3:
                code, remaining, annotation = result
                self.last_work_annotation = annotation
                log.info(
                    f"[ToolManager.parse_work_environment] ✓ Found with annotation='{annotation}' | code_len={len(code)}")
                if annotation:
                    # Emit via signal — safe from any thread
                    self.approval_signal.system_message.emit(
                        f"**Working:** ***{annotation}***"
                    )
                    log.debug(f"[ToolManager.parse_work_environment] | SENT SYSTEM MESSAGE TO CHAT: {annotation}")
            else:
                code, remaining = result
                self.last_work_annotation = None
                log.info(f"[ToolManager.parse_work_environment] ✓ Found (no annotation) | code_len={len(code)}")

            return code, remaining

        log.debug("[ToolManager.parse_work_environment] Not found")
        return None

    def parse_execute_code(self, text):
            """Parse execute_code fence from AI output. Returns (code, remaining_text) or None."""
            log.debug(f"[ToolManager.parse_execute_code] Parsing {len(text)} chars")
            result = self._parse_fence(text, 'execute_code')
            if result:
                code, remaining = result
                log.info(f"[ToolManager.parse_execute_code] ✓ Found | code_len={len(code)}")
                return code, remaining
            log.debug("[ToolManager.parse_execute_code] Not found")
            return None

    def parse_set_session_name(self, text):
            """Parse set_session_name fence from AI output. Returns (name, remaining_text) or None."""
            log.debug(f"[ToolManager.parse_set_session_name] Parsing {len(text)} chars")
            result = self._parse_fence(text, 'set_session_name')
            if result:
                name, remaining = result
                log.info(f"[ToolManager.parse_set_session_name] ✓ Found | name='{name}'")
                return name, remaining
            log.debug("[ToolManager.parse_set_session_name] Not found")
            return None

    def parse_load_skill(self, text):
            """Parse load_skill fence from AI output. Returns (skill_name, remaining_text) or None."""
            log.debug(f"[ToolManager.parse_load_skill] Parsing {len(text)} chars")
            result = self._parse_fence(text, 'load_skill')
            if result:
                skill_name, remaining = result
                log.info(f"[ToolManager.parse_load_skill] ✓ Found | skill='{skill_name}'")
                return skill_name, remaining
            log.debug("[ToolManager.parse_load_skill] Not found")
            return None

    def parse_unload_skill(self, text):
            """Parse unload_skill fence from AI output. Returns (skill_name, remaining_text) or None."""
            log.debug(f"[ToolManager.parse_unload_skill] Parsing {len(text)} chars")
            result = self._parse_fence(text, 'unload_skill')
            if result:
                skill_name, remaining = result
                log.info(f"[ToolManager.parse_unload_skill] ✓ Found | skill='{skill_name}'")
                return skill_name, remaining
            log.debug("[ToolManager.parse_unload_skill] Not found")
            return None


    # ─────────────────────────────────────────────────────────────────────────
    # Single-exec policy enforcement
    # ─────────────────────────────────────────────────────────────────────────

    def enforce_single_exec_policy(self, text):
        """
        Scan text for code-execution tool fences (work_environment / execute_code).
        If more than one is found, keep only the first and drop the rest.
        set_session_name is intentionally EXEMPT from this policy.

        Returns:
            tuple: (cleaned_text, violation_bool, violation_msg)
        """
        log.info(f"[ToolManager.enforce_single_exec_policy] Scanning {len(text)} chars for policy violations")
        pattern = r'```[ \t]*(\w+)[^\n]*\n.*?```'
        matches = []
        for match in re.finditer(pattern, text, re.DOTALL):
            canonical = self._tool_keys_norm.get(self._norm_key(match.group(1)))
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
            f"⚠️ **NOTICE:** LLM failed to follow instructions — it used {len(extras)} more "
            f"work environment and/or execute code blocks in one response ({', '.join(dropped_names)}). Only the "
            f"first call was kept and executed. "
            "To reduce how often this happens, consider using a "
            "more capable model."
            f"\nTotal violations this session: {_v}"
        )

        for match, _ in reversed(extras):
            text = text[:match.start()] + text[match.end():]

        log.info(f"[ToolManager.enforce_single_exec_policy] ✓ Policy enforced")
        return text.strip(), True, violation_msg

    # ─────────────────────────────────────────────────────────────────────────
    # Supervised execution helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _check_supervised_execution(self, code, execution_type):
        """
        Check if supervised execution is enabled and get approval if needed.
        FIXED: Now properly handles approval dialog on main thread.

        Args:
            code: Code to execute
            execution_type: 'execute_code' or 'work_environment'

        Returns:
            tuple: (approved: bool, modified_code: str)
        """
        log.info(f"[ToolManager._check_supervised_execution] execution_type='{execution_type}' | "
                 f"code_len={len(code)} | has_settings_callback={self.settings_callback is not None}")

        # Check if supervised execution is enabled
        # self.supervised_execution overrides the settings_callback when set
        if self.supervised_execution is not None:
            supervised_enabled = self.supervised_execution
            log.debug(f"[ToolManager._check_supervised_execution] supervised_execution (override)={supervised_enabled}")
        elif self.settings_callback:
            settings = self.settings_callback()
            supervised_enabled = settings.get('supervised_execution', True)  # Default ON
            log.debug(f"[ToolManager._check_supervised_execution] supervised_execution (from settings)={supervised_enabled}")
        else:
            # No settings callback, assume supervised mode
            supervised_enabled = True
            log.warning("[ToolManager._check_supervised_execution] No settings_callback — "
                        "assuming supervised=True")

        if not supervised_enabled:
            # Auto-approve if supervision is disabled
            log.debug("[ToolManager._check_supervised_execution] Supervision disabled — auto-approving")
            return True, code

        # Show approval dialog on main thread
        log.debug("[ToolManager._check_supervised_execution] Emitting request_approval signal to main thread")
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
                return False, code

            log.info(f"[ToolManager._check_supervised_execution] User decision received | "
                     f"approved={approval_result['approved']}")
            return approval_result['approved'], approval_result['modified_code']

        except Exception as e:
            log.error(f"[ToolManager._check_supervised_execution] ✗ Error showing approval dialog: "
                      f"{type(e).__name__}: {e}")
            # If dialog fails, approve by default (but log error)
            return True, code

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
            execution_type: 'execute_code' or 'work_environment'
            callback: Function to call with (approved, modified_code)
        """
        log.info(f"[ToolManager._show_approval_dialog_on_main_thread] execution_type='{execution_type}' | "
                 f"code_len={len(code)} | has_ai_engine={self.ai_engine is not None}")
        try:
            from ui.code_approval_dialog import CodeApprovalDialog

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

            # Show dialog manually so we keep a ref for Android to close it
            dialog = CodeApprovalDialog(code, execution_type, self.ai_engine)
            self._active_approval_dialog = dialog
            dialog.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
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
        from ui.timeout_dialog import TimeoutDialog

        parent = QApplication.activeWindow()

        # If execution already finished while the signal was queued, bail
        if exec_done_event.is_set():
            log.info("[ToolManager._show_timeout_dialog_on_main_thread] Execution already done — skipping dialog")
            result_holder.append(0)
            user_event.set()
            return

        dialog = TimeoutDialog(elapsed, parent)

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

    def run_work_environment(self, code):
        """
        Execute code in work environment mode.
        AI will see the output and can chain more executions.

        Wraps PythonInterpreter.execute() with work_code_running flag and
        last_work_code tracking for interrupt/UI state propagation.

        Returns:
            str: Formatted output for AI
        """
        code_preview = code.strip()[:80].replace('\n', '↵')
        log.info(f"[ToolManager.run_work_environment] ── Executing code | "
                 f"code_len={len(code)} | preview='{code_preview}' ──")

        # Check for exit command
        if code.lower() == 'exit':
            log.info("[ToolManager.run_work_environment] Exit command detected — returning EXITED_WORK_MODE")
            QTimer.singleShot(0, lambda: self._chat.set_session_list_locked(False)) # TODO: remove QTimer.singleShot workaround once session_list locking is on main thread
            self.in_work_mode = False
            return "EXITED_WORK_MODE"

        # Capability gate — real enforcement, not just prompt text
        if not self.allow_workmode:
            log.warning("[ToolManager.run_work_environment] Blocked — allow_workmode is False for this session")
            return "ERROR:\nwork_environment is disabled for this session."

        # Check supervised execution (now properly on main thread)
        log.debug("[ToolManager.run_work_environment] Checking supervised execution...")
        approved, modified_code = self._check_supervised_execution(code, 'work_environment')

        if not approved:
            log.warning("[ToolManager.run_work_environment] ✗ Execution rejected by user")
            return "ERROR:\nCode execution rejected by user"

        # Use modified code if user edited it
        if modified_code != code:
            log.info("[ToolManager.run_work_environment] Code was modified by user in approval dialog")
        code = modified_code

        # Pull literal #@FILE blocks out before execution so their content is
        # never parsed as Python. last_work_code keeps the full source (blocks
        # included) for the UI note / history; only exec_code runs.
        self.last_work_code = code
        exec_code, _file_blocks = self._extract_file_blocks(code)
        if _file_blocks:
            self.tools['python'].inject_vars(_file_blocks)

        # Execute Python code (with optional timeout)
        timeout = None
        if self.settings_callback:
            timeout = self.settings_callback().get('tool_execution_timeout_seconds', None)
        log.debug(f"[ToolManager.run_work_environment] → Executing via PythonInterpreter | "
                  f"timeout={timeout}s")
        self.work_code_running = True
        self.approval_signal.work_code_active.emit(True)
        try:
            result = self.tools['python'].execute(
                exec_code,
                timeout=timeout,
                timeout_callback=self._handle_execution_timeout if timeout else None
            )
        finally:
            self.work_code_running = False
            self.approval_signal.work_code_active.emit(False)
        log.debug(f"[ToolManager.run_work_environment] Python result: success={result['success']} | "
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
            log.debug("[ToolManager.run_work_environment] No output produced — sending recommendation")

        combined = "\n\n".join(output_parts)
        log.info(f"[ToolManager.run_work_environment] ✓ Work environment output ready | "
                 f"parts={len(output_parts)} | total_len={len(combined)}")

        # ── Mirror code + output to Android bridge ────────────────────────
        try:
            ab = self._get_android_bridge() if callable(self._get_android_bridge) else None
            if ab and ab.isVisible():
                ab.add_work_execution(code, combined, annotation=self.last_work_annotation or "")
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────

        return combined

    def run_execute_code(self, code, log_callback=None):
        """
        Execute code directly without returning output to AI.
        Used for quick actions like opening apps, showing UI.

        Returns:
            dict: {
                'success': bool,
                'message': str,  # Message to show user
                'error': str or None,  # Full traceback if failed
                'stdout': str,  # stdout output
                'stderr': str   # stderr output
            }
        """
        code_preview = code.strip()[:80].replace('\n', '↵')
        log.info(f"[ToolManager.run_execute_code] ── Executing code (no AI output) | "
                 f"code_len={len(code)} | preview='{code_preview}' ──")
        try:
            # Capability gate — real enforcement, not just prompt text
            if not self.allow_execute_code:
                log.warning("[ToolManager.run_execute_code] Blocked — allow_execute_code is False for this session")
                return {
                    'success': False,
                    'message': "Code execution is disabled for this session",
                    'error': None,
                    'stdout': '',
                    'stderr': ''
                }

            # Check supervised execution (now properly on main thread)
            log.debug("[ToolManager.run_execute_code] Checking supervised execution...")
            approved, modified_code = self._check_supervised_execution(code, 'execute_code')

            if not approved:
                log.warning("[ToolManager.run_execute_code] ✗ Code execution rejected by user")
                if log_callback:
                    log_callback("❌ Code execution rejected by user", "WARNING")
                return {
                    'success': False,
                    'message': "Code execution was rejected",
                    'error': None,
                    'stdout': '',
                    'stderr': ''
                }

            # Use modified code if user edited it
            if modified_code != code:
                log.info("[ToolManager.run_execute_code] Code was modified by user in approval dialog")
            code = modified_code

            if log_callback:
                log_callback(f"Executing: {code[:100]}...", "INFO")

            # Check if it's a GUI app that needs subprocess
            log.debug("[ToolManager.run_execute_code] → _execute_with_gui_support()")
            result = self._execute_with_gui_support(code)
            log.debug(f"[ToolManager.run_execute_code] Execution result: "
                      f"success={result.get('success')} | "
                      f"gui_launched={result.get('gui_launched', False)} | "
                      f"has_error={bool(result.get('error'))}")

            # GUI app launched in subprocess
            if result.get('gui_launched'):
                log.info(f"[ToolManager.run_execute_code] ✓ GUI app launched | "
                         f"pid={result.get('pid')} | script='{result.get('script_path')}'")
                if log_callback:
                    log_callback("✓ GUI application launched", "SUCCESS")
                return {
                    'success': True,
                    'message': "GUI launched. Did it appear on your screen?",
                    'error': None,
                    'stdout': result.get('stdout', ''),
                    'stderr': result.get('stderr', '')
                }

            # Normal execution completed
            if result['success']:
                log.info("[ToolManager.run_execute_code] ✓ Code executed successfully")
                if log_callback:
                    log_callback("✓ Code executed successfully", "SUCCESS")
                return {
                    'success': True,
                    'message': "Code executed. Did it work as expected?",
                    'error': None,
                    'stdout': result.get('stdout', ''),
                    'stderr': result.get('stderr', '')
                }

            # Execution failed - include full error
            error_msg = result.get('error', 'Unknown error')
            log.error(f"[ToolManager.run_execute_code] ✗ Execution failed: {error_msg[:200]}")
            if log_callback:
                log_callback(f"✗ Execution failed: {error_msg}", "ERROR")
            return {
                'success': False,
                'message': f"Execution failed: {error_msg}",
                'error': error_msg,  # Full traceback
                'stdout': result.get('stdout', ''),
                'stderr': result.get('stderr', '')
            }

        except Exception as e:
            error_trace = f"Exception: {str(e)}"
            log.error(f"[ToolManager.run_execute_code] ✗ Outer exception: {type(e).__name__}: {e}")
            if log_callback:
                log_callback(f"Error: {e}", "ERROR")
            return {
                'success': False,
                'message': f"Error: {str(e)}",
                'error': error_trace,
                'stdout': '',
                'stderr': ''
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Prompt helpers
    # ─────────────────────────────────────────────────────────────────────────

    def get_work_mode_prompt(self):
        """Get the prompt for work mode continuation. In native tool-calling mode
        the variant that instructs native tool calls (not fences) is used."""
        log.debug(f"[ToolManager.get_work_mode_prompt] Building work mode prompt | "
                  f"has_last_output={self.last_work_output is not None}")
        from core.global_instructions import WORK_MODE_PROMPT, WORK_MODE_PROMPT_NATIVE

        native = False
        try:
            s = self.settings_callback() if self.settings_callback else None
            native = (s or {}).get('tool_calling_mode', 'compat') == 'native'
        except Exception:
            native = False

        template = WORK_MODE_PROMPT_NATIVE if native else WORK_MODE_PROMPT
        output = self.last_work_output or "No previous output"
        prompt = template.format(work_output=output)
        log.debug(f"[ToolManager.get_work_mode_prompt] Prompt built | native={native} | length={len(prompt)}")
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
        for key in self._tool_keys:
            result = self._parse_fence(text, key)
            while result is not None:
                # _parse_fence returns 3-tuple for annotated work_environment,
                # 2-tuple for everything else (including plain work_environment)
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
        spaces, then lowercase. So work_environment / work-environment /
        'work environment' / WorkEnvironment all resolve to the same canonical key."""
        return key.replace('_', '').replace('-', '').replace(' ', '').lower()

    def _find_canonical_tool_key(self, data):
        """
        Return the canonical tool key present in a parsed JSON dict, or None.

        Handles two formats:
          NEW:    {"tool": "work_environment", "input": "..."}
          LEGACY: {"work_environment": true,   "input": "..."}
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

        Handles new format {"tool": "work_environment"} and
        legacy format     {"work_environment": true}.
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


    # ─────────────────────────────────────────────────────────────────────────
    # GUI execution helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_gui_code(self, code):
        """Detect if code is likely a GUI application"""

        gui_keywords = [

            # tkinter
            'tkinter',
            'tk.Tk(',
            'Tk(',
            'mainloop(',
            '.mainloop(',
            'tk.Frame',
            'tk.Button',
            'tk.Label',
            'tk.Canvas',
            'ttk.',
            'tkinter.ttk',

            # PyQt5 / PyQt6
            'PyQt5',
            'PyQt6',
            'QApplication',
            'QMainWindow',
            'QWidget',
            'QDialog',
            'exec()',
            'exec_()',
            '.show()',
            'QtWidgets',
            'QtCore',
            'QtGui',

            # PySide2 / PySide6
            'PySide2',
            'PySide6',

            # wxPython
            'wx',
            'wx.App',
            'wx.Frame',
            'wx.Panel',
            'wxPython',
            'MainLoop()',

            # pygame
            'pygame',
            'pygame.init',
            'pygame.display',
            'pygame.event',
            'pygame.Surface',
            'pygame.screen',

            # Kivy
            'kivy',
            'App(',
            'build(',
            'run()',
            'kivy.app',
            'kivy.uix',

            # CustomTkinter
            'customtkinter',
            'CTk(',
            'CTkButton',
            'CTkLabel',
            'CTkFrame',

            # Dear PyGui
            'dearpygui',
            'dpg.create_context',
            'dpg.create_viewport',
            'dpg.start_dearpygui',

            # PySimpleGUI
            'PySimpleGUI',
            'sg.Window',
            'sg.theme',
            'window.read',

            # Flet
            'flet',
            'ft.app',
            'ft.Page',

            # Gooey
            'Gooey',
            '@Gooey',

            # PyForms
            'pyforms',

            # Toga
            'toga.App',

            # Qt generic
            '.exec(',
            '.exec_(',

            # Matplotlib interactive GUI
            'plt.show(',
            'matplotlib.use',

            # OpenCV GUI
            'cv2.imshow',
            'cv2.waitKey',
            'cv2.namedWindow',

            # Arcade
            'arcade.Window',

            # Panda3D
            'ShowBase',

            # PyGame Zero
            'pgzrun.go',

            # Generic window creation hints
            'create_window',
            'set_window_title',
            'window =',
            'root =',
            'app = QApplication',
            'app.mainloop',

            # Event loop patterns
            'event_loop',
            'processEvents',
            'bind(',

            # Modern GUI frameworks
            'nicegui',
            'textual.app',
            'textual.App',
            'textual.run',
            'textual.widgets',

            # PyWebView
            'webview.create_window',
            'pywebview',

        ]

        code_lower = code.lower()

        return any(keyword.lower() in code_lower for keyword in gui_keywords)

    def _save_code_to_file(self, code):
        """
        Save code to a temporary file in .generated folder

        Returns:
            str: Path to the saved file
        """
        log.debug(f"[ToolManager._save_code_to_file] Saving {len(code)} chars to .generated/")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"gui_exec_{timestamp}.py"
        filepath = os.path.join(self.generated_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(code)

        log.info(f"[ToolManager._save_code_to_file] ✓ Saved to '{filepath}'")
        return filepath

    def _execute_with_gui_support(self, code):
        """
        Execute code with GUI app detection and subprocess support

        Returns:
            dict: Execution result with optional 'gui_launched' flag
        """
        log.debug(f"[ToolManager._execute_with_gui_support] code_len={len(code)} — detecting GUI usage")
        # Detect if it's GUI code
        is_gui = self._detect_gui_code(code)
        log.info(f"[ToolManager._execute_with_gui_support] GUI detection result: is_gui={is_gui}")

        if is_gui:
            # Use subprocess for GUI applications
            try:
                # Save code to file
                script_path = self._save_code_to_file(code)
                log.debug(f"[ToolManager._execute_with_gui_support] GUI script saved: '{script_path}'")

                # Launch in subprocess (non-blocking)
                import sys
                log.debug(f"[ToolManager._execute_with_gui_support] Launching subprocess: "
                          f"'{sys.executable}' '{script_path}'")
                process = subprocess.Popen(
                    [sys.executable, script_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

                # Don't wait - let it run in background
                log.info(f"[ToolManager._execute_with_gui_support] ✓ GUI process launched | "
                         f"pid={process.pid} | script='{script_path}'")

                # ── GUI subprocess timeout warning ────────────────────────────
                gui_timeout = None
                if self.settings_callback:
                    gui_timeout = self.settings_callback().get('tool_execution_timeout_seconds', None)
                if gui_timeout:
                    def _warn_gui_timeout(pid=process.pid, path=script_path, t=gui_timeout):
                        log.warning(f"[ToolManager._execute_with_gui_support] GUI app (PID {pid}) "
                                    f"still running after {t}s — script='{path}'")
                        self.approval_signal.system_message.emit(
                            f"⚠️ **GUI App Still Running**\n"
                            f"The GUI application launched from `.generated/{os.path.basename(path)}` "
                            f"(PID {pid}) is still running after {t} seconds.\n"
                            f"Press **Stop** if you need to kill it, or leave it to run naturally."
                        )
                    threading.Timer(gui_timeout, _warn_gui_timeout, daemon=True).start()
                    log.debug(f"[ToolManager._execute_with_gui_support] GUI timeout warning "
                              f"set for {gui_timeout}s")
                # ─────────────────────────────────────────────────────────────

                return {
                    'success': True,
                    'gui_launched': True,
                    'script_path': script_path,
                    'pid': process.pid,
                    'stdout': '',
                    'stderr': ''
                }

            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                log.error(f"[ToolManager._execute_with_gui_support] ✗ GUI launch failed: "
                          f"{type(e).__name__}: {e}")
                return {
                    'success': False,
                    'error': error_trace,
                    'stdout': '',
                    'stderr': ''
                }
        else:
            # Execute normally for non-GUI code
            log.debug("[ToolManager._execute_with_gui_support] Non-GUI code — using PythonInterpreter")
            result = self.tools['python'].execute(code)
            log.debug(f"[ToolManager._execute_with_gui_support] PythonInterpreter result: "
                      f"success={result['success']}")
            return result


