"""
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
from PyQt6.QtCore import QObject, pyqtSignal
from core.python_interpreter import PythonInterpreter
from core.logger import _make_logger, _NoOpLogger

# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("ToolManager") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


class ApprovalSignal(QObject):
    """Signal object for requesting code approval on main thread"""
    request_approval = pyqtSignal(str, str, object)  # code, execution_type, callback


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
        log.debug("[ToolManager.__init__] Work mode state: in_work_mode=False | last_work_output=None")

        # ── Violation tracking ────────────────────────────────────────────────
        # Incremented each time the AI emits more than one code-execution tool
        # call in a single response (set_session_name is exempt).
        self.exec_violations = 0
        log.debug("[ToolManager.__init__] exec_violations counter initialised to 0")

        # Settings and AI engine
        self.settings_callback = settings_callback
        self.ai_engine = ai_engine

        # Approval signal for main thread communication
        log.debug("[ToolManager.__init__] Creating ApprovalSignal and connecting to main thread handler")
        self.approval_signal = ApprovalSignal()
        self.approval_signal.request_approval.connect(self._show_approval_dialog_on_main_thread)
        log.debug("[ToolManager.__init__] ApprovalSignal connected")

        # Setup .generated folder (relative to working directory)
        self.generated_dir = os.path.join(os.getcwd(), '.generated')
        log.debug(f"[ToolManager.__init__] .generated dir path: '{self.generated_dir}'")
        self._ensure_generated_dir()

        # ── Tool registry ─────────────────────────────────────────────────────
        # Canonical tool names.  To add a new tool, append its name here and
        # implement the corresponding parse_/run_ methods.
        self._tool_keys = ['work_environment', 'execute_code', 'set_session_name']
        # Maps normalised (no-underscore, lowercase) form → canonical name
        self._tool_keys_norm = {k.replace('_', '').lower(): k for k in self._tool_keys}
        log.debug(f"[ToolManager.__init__] Registered tool keys: {self._tool_keys}")
        log.debug(f"[ToolManager.__init__] Normalised tool key map: {self._tool_keys_norm}")

        # ── Code-execution tools (subject to single-call policy) ──────────────
        # set_session_name is NOT in this list — it may coexist with a code tool.
        self._exec_tool_keys = {'work_environment', 'execute_code'}
        log.debug(f"[ToolManager.__init__] Exec tool keys (policy-restricted): {self._exec_tool_keys}")

        log.info("[ToolManager.__init__] ✓ ToolManager initialization complete")

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
                print(f"Created .generated directory at: {self.generated_dir}")
            else:
                log.debug(f"[ToolManager._ensure_generated_dir] Directory already exists — no action")
        except Exception as e:
            log.error(f"[ToolManager._ensure_generated_dir] ✗ Could not create directory: "
                      f"{type(e).__name__}: {e}")
            print(f"Warning: Could not create .generated directory: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Public parse methods
    # ─────────────────────────────────────────────────────────────────────────

    def parse_work_environment(self, text):
        """
        Parse work_environment call from AI output.
        Supports both new format  {"tool": "work_environment", "input": "..."}
        and legacy format         {"work_environment": true,   "input": "..."}.

        Returns:
            tuple: (code, remaining_text) or None if not found
        """
        log.debug(f"[ToolManager.parse_work_environment] Parsing {len(text)} char text for work_environment")
        json_data = self._extract_json(text, tool_key='work_environment')

        if json_data and self._has_tool_key(json_data, 'work_environment'):
            code = (self._get_tool_value(json_data, 'input') or '').strip()
            remaining_text = self._remove_json_from_text(text, json_data, tool_key='work_environment')
            log.info(f"[ToolManager.parse_work_environment] ✓ Found work_environment call | "
                     f"format={'new' if 'tool' in json_data else 'legacy'} | "
                     f"code_len={len(code)} | remaining_text_len={len(remaining_text)}")
            log.debug(f"[ToolManager.parse_work_environment] code_preview='{code[:80].replace(chr(10), '↵')}'")
            return code, remaining_text

        log.debug("[ToolManager.parse_work_environment] No work_environment call found")
        return None

    def parse_execute_code(self, text):
        """
        Parse execute_code call from AI output.
        Supports both new format  {"tool": "execute_code", "input": "..."}
        and legacy format         {"execute_code": true,   "input": "..."}.

        Returns:
            tuple: (code, remaining_text) or None if not found
        """
        log.debug(f"[ToolManager.parse_execute_code] Parsing {len(text)} char text for execute_code")
        json_data = self._extract_json(text, tool_key='execute_code')

        if json_data and self._has_tool_key(json_data, 'execute_code'):
            code = (self._get_tool_value(json_data, 'input') or '').strip()
            remaining_text = self._remove_json_from_text(text, json_data, tool_key='execute_code')
            log.info(f"[ToolManager.parse_execute_code] ✓ Found execute_code call | "
                     f"format={'new' if 'tool' in json_data else 'legacy'} | "
                     f"code_len={len(code)} | remaining_text_len={len(remaining_text)}")
            log.debug(f"[ToolManager.parse_execute_code] code_preview='{code[:80].replace(chr(10), '↵')}'")
            return code, remaining_text

        log.debug("[ToolManager.parse_execute_code] No execute_code call found")
        return None

    def parse_set_session_name(self, text):
        """
        Parse set_session_name from AI output using multi-strategy aggressive extraction.

        Works regardless of:
          - Position in text (start, middle, end, after other tool calls)
          - Whether JSON is valid or slightly malformed
          - Whether the AI is in work mode or normal mode
          - Format (new unified, legacy, raw key-value)

        Strategy order (most-specific → most-permissive):
          1. Valid JSON via _extract_json — handles well-formed code-fence and bare blocks
          2. Regex on code-fence block — catches new format even if outer JSON is borderline
          3. Regex on code-fence block — catches legacy format in fences
          4. Regex anywhere in raw text for "set_session_name": "value" key-value pair
          5. Regex anywhere for  "tool": "set_session_name" ... "input": "value"

        After the name is found, the surrounding JSON/fence block is removed from text.
        If only a raw key-value was found (no enclosing block) the key-value pair itself
        is removed.

        Returns:
            tuple: (session_name, remaining_text) or None if not found
        """
        log.debug(f"[ToolManager.parse_set_session_name] ── Parsing {len(text)} char text "
                  f"(aggressive multi-strategy) ──")

        # ── Strategy 1: well-formed JSON (existing robust path) ──────────────
        json_data = self._extract_json(text, tool_key='set_session_name')

        if json_data and self._has_tool_key(json_data, 'set_session_name'):
            is_new_format = 'tool' in json_data
            if is_new_format:
                session_name = (self._get_tool_value(json_data, 'input') or '').strip()
                log.debug(f"[ToolManager.parse_set_session_name] Strategy 1 — NEW format | "
                          f"reading 'input' key | name='{session_name}'")
            else:
                session_name = (self._get_tool_value(json_data, 'set_session_name') or '').strip()
                log.debug(f"[ToolManager.parse_set_session_name] Strategy 1 — LEGACY format | "
                          f"reading 'set_session_name' key | name='{session_name}'")

            # Fallback: new format but "input" was empty → try key directly
            if not session_name and is_new_format:
                session_name = (self._get_tool_value(json_data, 'set_session_name') or '').strip()
                log.warning(f"[ToolManager.parse_set_session_name] Strategy 1 — new format 'input' empty; "
                            f"fell back to direct key | name='{session_name}'")

            if session_name:
                remaining_text = self._remove_json_from_text(text, json_data, tool_key='set_session_name')
                log.info(f"[ToolManager.parse_set_session_name] ✓ Strategy 1 success | "
                         f"name='{session_name}' | remaining_len={len(remaining_text)}")
                return session_name, remaining_text
            else:
                log.warning("[ToolManager.parse_set_session_name] Strategy 1 found JSON but name was empty — "
                            "falling through to aggressive strategies")

        # ── Strategies 2-5: regex-based fallback ──────────────────────────────
        log.debug("[ToolManager.parse_set_session_name] Strategy 1 failed — trying regex strategies")
        result = self._extract_session_name_aggressive(text)

        if result:
            strategy, session_name, span_start, span_end = result
            log.info(f"[ToolManager.parse_set_session_name] ✓ Strategy '{strategy}' success | "
                     f"name='{session_name}' | span=({span_start}, {span_end})")
            # Remove the matched span from text
            remaining_text = self._cleanup_orphaned_braces(
                (text[:span_start] + text[span_end:]).strip()
            )
            log.debug(f"[ToolManager.parse_set_session_name] remaining_len={len(remaining_text)}")
            return session_name, remaining_text

        log.debug("[ToolManager.parse_set_session_name] All strategies failed — no set_session_name found")
        return None

    def _extract_session_name_aggressive(self, text):
        """
        Regex-based fallback for finding a session name anywhere in *text*.

        Tries strategies in order and returns the one found earliest in the text.
        Each result is a tuple: (strategy_label, name, span_start, span_end)
        where [span_start:span_end] is the range in text to remove.

        Returns the first (earliest) match or None.
        """
        results = []

        # ── Strategy 2: new format inside a ```json fence ────────────────────
        # Matches: ```json { ... "tool": "set_session_name" ... "input": "value" ... } ```
        fence_new = re.search(
            r'```(?:json)?\s*\{[^`]*"tool"\s*:\s*"set_?session_?name"[^`]*"input"\s*:\s*"([^"]+)"[^`]*\}\s*```',
            text, re.DOTALL | re.IGNORECASE
        )
        if fence_new:
            name = fence_new.group(1).strip()
            log.debug(f"[ToolManager._extract_session_name_aggressive] Strategy 2 (fence new) "
                      f"hit | name='{name}' | span=({fence_new.start()}, {fence_new.end()})")
            results.append(('fence_new_format', name, fence_new.start(), fence_new.end()))

        # ── Strategy 3: legacy format inside a ```json fence ────────────────
        # Matches: ```json { ... "set_session_name": "value" ... } ```
        # Excludes blocks that also have "tool": (those are Strategy 2)
        for m in re.finditer(
            r'```(?:json)?\s*(\{[^`]*\})\s*```',
            text, re.DOTALL
        ):
            block = m.group(1)
            if re.search(r'"tool"\s*:', block, re.IGNORECASE):
                continue  # new format — already handled
            ssn_match = re.search(r'"set_?session_?name"\s*:\s*"([^"]+)"', block, re.IGNORECASE)
            if ssn_match:
                name = ssn_match.group(1).strip()
                log.debug(f"[ToolManager._extract_session_name_aggressive] Strategy 3 (fence legacy) "
                          f"hit | name='{name}' | span=({m.start()}, {m.end()})")
                results.append(('fence_legacy_format', name, m.start(), m.end()))
                break  # take first

        # ── Strategy 4: raw "set_session_name": "value" anywhere in text ────
        # This catches bare key-value pairs, slightly broken JSON, and any other
        # occurrence of the key no matter where it is.
        raw_kv = re.search(
            r'"set_?session_?name"\s*:\s*"([^"]+)"',
            text, re.IGNORECASE
        )
        if raw_kv:
            name = raw_kv.group(1).strip()
            # Try to extend the span to cover the surrounding {} block if any
            # (so we remove the whole bare JSON, not just the key-value pair)
            block_span = self._find_enclosing_json_span(text, raw_kv.start())
            if block_span:
                span = block_span
            else:
                span = (raw_kv.start(), raw_kv.end())
            log.debug(f"[ToolManager._extract_session_name_aggressive] Strategy 4 (raw kv) "
                      f"hit | name='{name}' | span={span}")
            results.append(('raw_key_value', name, span[0], span[1]))

        # ── Strategy 5: "tool": "set_session_name" + nearby "input": "value" ─
        # Handles new format where the JSON is slightly malformed/unenclosed.
        tool_m = re.search(r'"tool"\s*:\s*"set_?session_?name"', text, re.IGNORECASE)
        if tool_m:
            # Search for "input": "value" within 300 chars forward
            window_end = min(len(text), tool_m.start() + 300)
            window = text[tool_m.start():window_end]
            input_m = re.search(r'"input"\s*:\s*"([^"]+)"', window, re.IGNORECASE)
            if input_m:
                name = input_m.group(1).strip()
                abs_end = tool_m.start() + input_m.end()
                # Try to cover enclosing {} block
                block_span = self._find_enclosing_json_span(text, tool_m.start())
                span = block_span if block_span else (tool_m.start(), abs_end)
                log.debug(f"[ToolManager._extract_session_name_aggressive] Strategy 5 (bare new) "
                          f"hit | name='{name}' | span={span}")
                results.append(('bare_new_format', name, span[0], span[1]))

        if not results:
            log.debug("[ToolManager._extract_session_name_aggressive] No match found by any strategy")
            return None

        # Return earliest match by position in text
        results.sort(key=lambda r: r[2])
        best = results[0]
        log.debug(f"[ToolManager._extract_session_name_aggressive] Best result: "
                  f"strategy='{best[0]}' | name='{best[1]}' | span=({best[2]}, {best[3]})")
        return best

    def _find_enclosing_json_span(self, text, pos):
        """
        Given a position *pos* inside *text*, walk backwards to find the
        opening { of the enclosing JSON object, then forward to its matching }.

        Returns (start, end) or None if no enclosing object is found.
        Used by the aggressive session name extractor to clean up whole blocks.
        """
        log.debug(f"[ToolManager._find_enclosing_json_span] Looking for enclosing {{ at pos={pos}}}")

        # Walk back to find the nearest unmatched opening brace
        brace_depth = 0
        in_string = False
        escape_next = False
        open_pos = None

        for i in range(pos, -1, -1):
            char = text[i]
            # Simplified reverse scan (no escape tracking backwards is perfect,
            # but sufficient for our purpose of locating the object start)
            if char == '}' and not in_string:
                brace_depth += 1
            elif char == '{' and not in_string:
                if brace_depth == 0:
                    open_pos = i
                    break
                brace_depth -= 1

        if open_pos is None:
            log.debug("[ToolManager._find_enclosing_json_span] No opening { found")
            return None

        # Walk forward from open_pos to find the matching closing brace
        brace_count = 0
        in_string = False
        escape_next = False
        for i in range(open_pos, len(text)):
            char = text[i]
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        log.debug(f"[ToolManager._find_enclosing_json_span] Found enclosing span: "
                                  f"({open_pos}, {i + 1})")
                        return open_pos, i + 1

        log.debug("[ToolManager._find_enclosing_json_span] Could not find matching closing }")
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Single-exec policy enforcement
    # ─────────────────────────────────────────────────────────────────────────

    def enforce_single_exec_policy(self, text):
        """
        Scan *text* for code-execution tool calls (work_environment / execute_code).
        If more than one is found:
          - The first one is left in place (will be executed normally).
          - All subsequent ones are stripped from the text.
          - self.exec_violations is incremented.

        set_session_name is intentionally EXEMPT from this policy and is never
        counted or removed by this method.

        Args:
            text (str): Raw AI response text (with JSON tool calls embedded).

        Returns:
            tuple:
                cleaned_text (str)  — text with extra exec tool calls removed.
                violation (bool)    — True if at least one extra call was dropped.
                violation_msg (str) — Human-readable note about what was dropped
                                      (empty string when no violation).
        """
        log.info(f"[ToolManager.enforce_single_exec_policy] Scanning {len(text)} chars for policy violations")

        found_exec_calls = []  # list of (canonical_tool_name, json_data, matched_text_span)

        # ── Collect all code-fence JSON blocks ──────────────────────────────
        code_block_pattern = r'```(?:json)?\s*(\{[^`]+\})\s*```'
        for match in re.finditer(code_block_pattern, text, re.DOTALL):
            try:
                data = json.loads(match.group(1).strip())
                canonical = self._find_canonical_tool_key(data)
                if canonical and canonical in self._exec_tool_keys:
                    found_exec_calls.append({
                        'canonical': canonical,
                        'data': data,
                        'span_start': match.start(),
                        'span_end': match.end(),
                        'raw': match.group(0),
                        'source': 'code_fence',
                    })
                    log.debug(f"[ToolManager.enforce_single_exec_policy] Found exec tool in code-fence | "
                              f"tool='{canonical}' | span=({match.start()}, {match.end()})")
            except json.JSONDecodeError:
                pass

        # ── Collect all bare JSON blocks ─────────────────────────────────────
        search_start = 0
        while True:
            pos = text.find('{', search_start)
            if pos == -1:
                break

            brace_count = 0
            in_string = False
            escape_next = False
            end_pos = None

            for i in range(pos, len(text)):
                char = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\':
                    escape_next = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_pos = i + 1
                            break

            if end_pos:
                raw_json = text[pos:end_pos]
                # Skip if this range overlaps with an already-found code-fence block
                overlaps = any(
                    c['source'] == 'code_fence' and c['span_start'] <= pos < c['span_end']
                    for c in found_exec_calls
                )
                if not overlaps:
                    try:
                        data = json.loads(raw_json)
                        canonical = self._find_canonical_tool_key(data)
                        if canonical and canonical in self._exec_tool_keys:
                            found_exec_calls.append({
                                'canonical': canonical,
                                'data': data,
                                'span_start': pos,
                                'span_end': end_pos,
                                'raw': raw_json,
                                'source': 'bare',
                            })
                            log.debug(f"[ToolManager.enforce_single_exec_policy] Found exec tool in bare JSON | "
                                      f"tool='{canonical}' | span=({pos}, {end_pos})")
                    except json.JSONDecodeError:
                        pass
                search_start = end_pos
            else:
                break

        # Sort by position in text so we always keep the FIRST one
        found_exec_calls.sort(key=lambda c: c['span_start'])

        log.info(f"[ToolManager.enforce_single_exec_policy] Total exec tool calls found: {len(found_exec_calls)}")

        if len(found_exec_calls) <= 1:
            log.debug("[ToolManager.enforce_single_exec_policy] No violation — returning text unchanged")
            return text, False, ""

        # ── Violation: drop all but the first ───────────────────────────────
        extras = found_exec_calls[1:]
        self.exec_violations += 1
        log.warning(f"[ToolManager.enforce_single_exec_policy] ✗ POLICY VIOLATION #{self.exec_violations} — "
                    f"{len(extras)} extra exec tool call(s) dropped | "
                    f"keeping='{found_exec_calls[0]['canonical']}' at pos {found_exec_calls[0]['span_start']} | "
                    f"dropping={[e['canonical'] for e in extras]}")

        dropped_names = [e['canonical'] for e in extras]
        violation_msg = (
            f"[POLICY] {len(extras)} extra code-execution tool call(s) were dropped "
            f"({', '.join(dropped_names)}). Only the first call was kept. "
            f"Total violations this session: {self.exec_violations}."
        )

        # Remove extras from text (work backwards to preserve offsets)
        for extra in reversed(extras):
            start = extra['span_start']
            end = extra['span_end']

            # For code-fence blocks we need to also remove the fence markers that
            # surround the bare JSON; the span already covers the full fence.
            text = text[:start] + text[end:]
            log.debug(f"[ToolManager.enforce_single_exec_policy] Removed extra '{extra['canonical']}' "
                      f"from span ({start}, {end}) | text now {len(text)} chars")

        cleaned = self._cleanup_orphaned_braces(text.strip())
        log.info(f"[ToolManager.enforce_single_exec_policy] ✓ Policy enforced | "
                 f"cleaned_len={len(cleaned)} | violation_msg='{violation_msg}'")
        return cleaned, True, violation_msg

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
        if self.settings_callback:
            settings = self.settings_callback()
            supervised_enabled = settings.get('supervised_execution', True)  # Default ON
            log.debug(f"[ToolManager._check_supervised_execution] supervised_execution={supervised_enabled}")

            if not supervised_enabled:
                # Auto-approve if supervision is disabled
                log.debug("[ToolManager._check_supervised_execution] Supervision disabled — auto-approving")
                return True, code
        else:
            # No settings callback, assume supervised mode
            supervised_enabled = True
            log.warning("[ToolManager._check_supervised_execution] No settings_callback — "
                        "assuming supervised=True")

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
            approval_event.wait()

            log.info(f"[ToolManager._check_supervised_execution] User decision received | "
                     f"approved={approval_result['approved']}")
            return approval_result['approved'], approval_result['modified_code']

        except Exception as e:
            log.error(f"[ToolManager._check_supervised_execution] ✗ Error showing approval dialog: "
                      f"{type(e).__name__}: {e}")
            print(f"Error showing approval dialog: {e}")
            import traceback
            traceback.print_exc()
            # If dialog fails, approve by default (but log error)
            return True, code

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
                print("Warning: No AI engine available for code explanation")
                callback(True, code)
                return

            log.debug("[ToolManager._show_approval_dialog_on_main_thread] Showing CodeApprovalDialog...")
            # Show dialog on main thread
            approved, modified_code = CodeApprovalDialog.get_approval(
                code, execution_type, self.ai_engine
            )
            log.info(f"[ToolManager._show_approval_dialog_on_main_thread] Dialog closed | "
                     f"approved={approved} | code_was_modified={modified_code != code}")

            # Call callback with results
            callback(approved, modified_code)

        except Exception as e:
            log.error(f"[ToolManager._show_approval_dialog_on_main_thread] ✗ Exception: "
                      f"{type(e).__name__}: {e}")
            print(f"Error in approval dialog: {e}")
            import traceback
            traceback.print_exc()
            callback(True, code)

    # ─────────────────────────────────────────────────────────────────────────
    # Run methods
    # ─────────────────────────────────────────────────────────────────────────

    def run_work_environment(self, code):
        """
        Execute code in work environment mode.
        AI will see the output and can chain more executions.

        Returns:
            str: Formatted output for AI
        """
        code_preview = code.strip()[:80].replace('\n', '↵')
        log.info(f"[ToolManager.run_work_environment] ── Executing code | "
                 f"code_len={len(code)} | preview='{code_preview}' ──")

        # Check for exit command
        if code.lower() == 'exit':
            log.info("[ToolManager.run_work_environment] Exit command detected — returning EXITED_WORK_MODE")
            self.in_work_mode = False
            return "EXITED_WORK_MODE"

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

        # Execute Python code
        log.debug("[ToolManager.run_work_environment] → Executing via PythonInterpreter")
        result = self.tools['python'].execute(code)
        log.debug(f"[ToolManager.run_work_environment] Python result: success={result['success']} | "
                  f"stdout_len={len(result['stdout'])} | stderr_len={len(result['stderr'])} | "
                  f"has_error={bool(result['error'])}")

        # Format output for AI to analyze
        output_parts = []

        if result['stdout']:
            output_parts.append(f"STDOUT:\n{result['stdout']}")

        if result['stderr']:
            output_parts.append(f"STDERR:\n{result['stderr']}")

        if result['result'] is not None:
            output_parts.append(f"RESULT:\n{result['result']}")

        if result['error']:
            output_parts.append(f"ERROR:\n{result['error']}")

        if not output_parts:
            output_parts.append(
                "Code executed but produced no output. "
                "RECOMMENDATION: Use print() or return values to see results!"
            )
            log.debug("[ToolManager.run_work_environment] No output produced — sending recommendation")

        combined = "\n\n".join(output_parts)
        log.info(f"[ToolManager.run_work_environment] ✓ Work environment output ready | "
                 f"parts={len(output_parts)} | total_len={len(combined)}")
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
        """Get the prompt for work mode continuation"""
        log.debug(f"[ToolManager.get_work_mode_prompt] Building work mode prompt | "
                  f"has_last_output={self.last_work_output is not None}")
        from core.global_instructions import WORK_MODE_PROMPT

        output = self.last_work_output or "No previous output"
        prompt = WORK_MODE_PROMPT.format(work_output=output)
        log.debug(f"[ToolManager.get_work_mode_prompt] Prompt built | length={len(prompt)}")
        return prompt

    def reset_python(self):
        """Reset Python interpreter state"""
        log.info("[ToolManager.reset_python] Resetting Python interpreter state")
        self.tools['python'].reset()
        log.info("[ToolManager.reset_python] ✓ Python interpreter reset complete")

    def strip_tool_calls(self, text):
        """
        Remove all tool call JSON blocks from text for display purposes.
        Does NOT execute any code — purely for cleaning stored messages before rendering.
        Handles both new format {"tool": "...", "input": "..."} and legacy formats.
        """
        if not text:
            log.debug("[ToolManager.strip_tool_calls] Empty text — returning as-is")
            return text
        log.debug(f"[ToolManager.strip_tool_calls] Stripping tool calls from {len(text)} char text | "
                  f"keys={self._tool_keys}")
        for key in self._tool_keys:
            text = self._remove_json_from_text(text, None, tool_key=key)
        log.debug(f"[ToolManager.strip_tool_calls] ✓ Stripped | resulting_len={len(text)}")
        return text

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helper methods — key normalisation & fuzzy matching
    # ─────────────────────────────────────────────────────────────────────────

    def _norm_key(self, key):
        """Normalise a key for fuzzy comparison (strip underscores, lowercase)."""
        return key.replace('_', '').lower()

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
    # JSON extraction helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _count_unmatched_closing_braces(self, text):
        """
        Count top-level unmatched closing braces in text (outside strings and
        code fences).  Returns how many orphaned } characters exist.
        """
        # Strip code fences so their braces are not counted
        stripped = re.sub(r'```[^`]*```', '', text, flags=re.DOTALL)
        depth = 0
        in_string = False
        escape_next = False
        for char in stripped:
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            if not in_string:
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
        return max(0, -depth)

    def _cleanup_orphaned_braces(self, text):
        """
        Strip lone } lines from the END of text, but only as many as are
        actually unmatched.  Removes AI formatting artifacts (e.g. a stray }
        after a code block) without touching valid JSON that must remain for
        sibling parse calls.
        """
        unmatched = self._count_unmatched_closing_braces(text)
        if unmatched == 0:
            return text.strip()
        lines = text.splitlines()
        removed = 0
        while removed < unmatched and lines:
            if lines[-1].strip() == '}':
                lines.pop()
                removed += 1
            else:
                break  # last line is not a lone } — stop
        return '\n'.join(lines).strip()

    def _extract_json(self, text, tool_key=None):
        """
        Extract all valid JSON tool blocks from text and return the one matching
        tool_key.  Supports both ```json code blocks and bare JSON, and handles
        AI inconsistencies like missing underscores in key names.

        Also supports the new unified format {"tool": "tool_name", "input": "..."}
        as well as the legacy format {"tool_name": true, "input": "..."}.

        Scans ALL occurrences of both patterns so mixed responses (e.g. one
        bare JSON + one code-block JSON) are handled correctly.

        Args:
            text: The text to search
            tool_key: Optional canonical key (e.g. 'execute_code').  When
                      provided, returns the first JSON containing this key
                      (fuzzy-matched).  When None, returns the first tool JSON
                      found (preserves original behaviour).

        Returns:
            dict or None
        """
        log.debug(f"[ToolManager._extract_json] text_len={len(text)} | tool_key='{tool_key}'")
        candidates = []
        seen_spans = []  # track (start, end) of code-fence spans to avoid double-counting

        # ── Collect code-block JSONs: ```json {...} ``` ──────────────────────
        code_block_pattern = r'```(?:json)?\s*(\{[^`]+\})\s*```'
        for match in re.finditer(code_block_pattern, text, re.DOTALL):
            try:
                data = json.loads(match.group(1).strip())
                if self._find_canonical_tool_key(data):
                    candidates.append(data)
                    seen_spans.append((match.start(), match.end()))
                    log.debug(f"[ToolManager._extract_json] Code-fence JSON found at ({match.start()}, "
                              f"{match.end()}) | keys={list(data.keys())}")
            except json.JSONDecodeError as e:
                log.debug(f"[ToolManager._extract_json] Code-fence JSON parse error: {e}")

        # ── Collect bare JSONs — brace-counting ─────────────────────────────
        start = 0
        while True:
            start_pos = text.find('{', start)
            if start_pos == -1:
                break

            # Skip if this position is inside a code-fence span
            in_fence = any(fs <= start_pos < fe for fs, fe in seen_spans)
            if in_fence:
                start = start_pos + 1
                continue

            brace_count = 0
            in_string = False
            escape_next = False
            end_pos = None

            for i in range(start_pos, len(text)):
                char = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\':
                    escape_next = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_pos = i + 1
                            break

            if end_pos:
                try:
                    data = json.loads(text[start_pos:end_pos])
                    if self._find_canonical_tool_key(data):
                        candidates.append(data)
                        log.debug(f"[ToolManager._extract_json] Bare JSON found at ({start_pos}, "
                                  f"{end_pos}) | keys={list(data.keys())}")
                except json.JSONDecodeError as e:
                    log.debug(f"[ToolManager._extract_json] Bare JSON parse error at ({start_pos}, "
                              f"{end_pos}): {e}")
                start = end_pos
            else:
                break

        log.debug(f"[ToolManager._extract_json] Total candidates found: {len(candidates)}")

        if not candidates:
            log.debug("[ToolManager._extract_json] No tool JSON found in text")
            return None

        if tool_key:
            for data in candidates:
                if self._has_tool_key(data, tool_key):
                    log.debug(f"[ToolManager._extract_json] ✓ Returning first candidate matching '{tool_key}'")
                    return data
            log.debug(f"[ToolManager._extract_json] No candidate matches tool_key='{tool_key}'")
            return None

        # No specific key requested — return first found
        log.debug("[ToolManager._extract_json] No tool_key filter — returning first candidate")
        return candidates[0]

    def _remove_json_from_text(self, text, json_data, tool_key=None):
        """
        Remove the JSON block for the given tool_key from text, leaving all
        other tool blocks intact.

        Uses brace-counting for both code-fence AND bare JSON blocks, so new
        format {"tool": "...", "input": "..."} and legacy format are handled
        identically — the block is located by parsing and using _has_tool_key,
        not by fragile key-name regex.

        Args:
            text: The original text
            json_data: The parsed JSON dict (used to verify the right block) — may be None
            tool_key: The specific tool key whose block should be removed

        Returns:
            str: Text with the matching JSON block removed
        """
        log.debug(f"[ToolManager._remove_json_from_text] text_len={len(text)} | tool_key='{tool_key}'")

        # ── Pattern 1: code-fence blocks ────────────────────────────────────
        code_block_pattern = r'```(?:json)?\s*(\{[^`]+\})\s*```'
        for match in re.finditer(code_block_pattern, text, re.DOTALL):
            try:
                found_json = json.loads(match.group(1))
                should_remove = (
                    (tool_key and self._has_tool_key(found_json, tool_key)) or
                    (not tool_key and self._find_canonical_tool_key(found_json))
                )
                if should_remove:
                    log.debug(f"[ToolManager._remove_json_from_text] Removing code-fence block "
                              f"at ({match.start()}, {match.end()}) | tool_key='{tool_key}'")
                    text = text[:match.start()] + text[match.end():]
                    return self._cleanup_orphaned_braces(text.strip())
            except Exception as e:
                log.debug(f"[ToolManager._remove_json_from_text] Code-fence JSON error: {e}")

        # ── Pattern 2: bare JSON — brace counting ────────────────────────────
        search_start = 0
        while True:
            start_pos = text.find('{', search_start)
            if start_pos == -1:
                break

            brace_count = 0
            in_string = False
            escape_next = False
            end_pos = None

            for i in range(start_pos, len(text)):
                char = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\':
                    escape_next = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_pos = i + 1
                            break

            if end_pos:
                try:
                    candidate = json.loads(text[start_pos:end_pos])
                    should_remove = (
                        (tool_key and self._has_tool_key(candidate, tool_key)) or
                        (not tool_key and self._find_canonical_tool_key(candidate))
                    )
                    if should_remove:
                        log.debug(f"[ToolManager._remove_json_from_text] Removing bare JSON block "
                                  f"at ({start_pos}, {end_pos}) | tool_key='{tool_key}'")
                        text = text[:start_pos] + text[end_pos:]
                        return self._cleanup_orphaned_braces(text.strip())
                except json.JSONDecodeError:
                    pass
                search_start = end_pos
            else:
                break

        log.debug(f"[ToolManager._remove_json_from_text] No matching block found for tool_key='{tool_key}'")
        return self._cleanup_orphaned_braces(text.strip())

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
                print(f"Saved GUI script to: {script_path}")

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
                print(f"GUI process started with PID: {process.pid}")

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
