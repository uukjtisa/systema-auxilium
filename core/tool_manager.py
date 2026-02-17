"""
Tool Manager - Simplified work environment and code execution system
FIXED: Code approval dialog now runs on main thread using Qt signals
UPDATED: GUI execution now uses subprocess with .generated folder
UPDATED: Captures and returns traceback for execute_code failures
"""

import json
import re
import threading
import subprocess
import os
import tempfile
from datetime import datetime
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import QApplication
from core.python_interpreter import PythonInterpreter


class ApprovalSignal(QObject):
    """Signal object for requesting code approval on main thread"""
    request_approval = pyqtSignal(str, str, object)  # code, execution_type, callback


class ToolManager:
    """Manages work environment and code execution with unified tool system"""

    def __init__(self, settings_callback=None, ai_engine=None):
        """
        Initialize ToolManager

        Args:
            settings_callback: Function to get settings dict
            ai_engine: Reference to AI engine for code explanations
        """
        # Available tools - easy to add more!
        self.tools = {
            'python': PythonInterpreter(),
        }

        # Work mode state
        self.in_work_mode = False
        self.last_work_output = None

        # Settings and AI engine
        self.settings_callback = settings_callback
        self.ai_engine = ai_engine

        # Approval signal for main thread communication
        self.approval_signal = ApprovalSignal()
        self.approval_signal.request_approval.connect(self._show_approval_dialog_on_main_thread)

        # Setup .generated folder (relative to working directory)
        self.generated_dir = os.path.join(os.getcwd(), '.generated')
        self._ensure_generated_dir()

        # Canonical tool keys and their normalised (no-underscore, lowercase) forms
        self._tool_keys = ['work_environment', 'execute_code', 'set_session_name']
        self._tool_keys_norm = {k.replace('_', '').lower(): k for k in self._tool_keys}

    def _ensure_generated_dir(self):
        """Create .generated directory if it doesn't exist"""
        try:
            if not os.path.exists(self.generated_dir):
                os.makedirs(self.generated_dir)
                print(f"Created .generated directory at: {self.generated_dir}")
        except Exception as e:
            print(f"Warning: Could not create .generated directory: {e}")

    def parse_work_environment(self, text):
        """
        Parse work_environment call from AI output

        Returns:
            tuple: (code, remaining_text) or None if not found
        """
        json_data = self._extract_json(text, tool_key='work_environment')

        if json_data and self._has_tool_key(json_data, 'work_environment'):
            code = (self._get_tool_value(json_data, 'input') or '').strip()
            remaining_text = self._remove_json_from_text(text, json_data, tool_key='work_environment')
            return code, remaining_text

        return None

    def parse_execute_code(self, text):
        """
        Parse execute_code call from AI output

        Returns:
            tuple: (code, remaining_text) or None if not found
        """
        json_data = self._extract_json(text, tool_key='execute_code')

        if json_data and self._has_tool_key(json_data, 'execute_code'):
            code = (self._get_tool_value(json_data, 'input') or '').strip()
            remaining_text = self._remove_json_from_text(text, json_data, tool_key='execute_code')
            return code, remaining_text

        return None

    def parse_set_session_name(self, text):
        """
        Parse set_session_name call from AI output

        Returns:
            tuple: (session_name, remaining_text) or None if not found
        """
        json_data = self._extract_json(text, tool_key='set_session_name')

        if json_data and self._has_tool_key(json_data, 'set_session_name'):
            session_name = (self._get_tool_value(json_data, 'set_session_name') or '').strip()
            remaining_text = self._remove_json_from_text(text, json_data, tool_key='set_session_name')
            return session_name, remaining_text

        return None

    def _check_supervised_execution(self, code, execution_type):
        """
        Check if supervised execution is enabled and get approval if needed
        FIXED: Now properly handles approval dialog on main thread

        Args:
            code: Code to execute
            execution_type: 'execute_code' or 'work_environment'

        Returns:
            tuple: (approved: bool, modified_code: str)
        """
        # Check if supervised execution is enabled
        if self.settings_callback:
            settings = self.settings_callback()
            supervised_enabled = settings.get('supervised_execution', True)  # Default ON

            if not supervised_enabled:
                # Auto-approve if supervision is disabled
                return True, code
        else:
            # No settings callback, assume supervised mode
            supervised_enabled = True

        # Show approval dialog on main thread
        try:
            # Create event to block worker thread until approval is received
            approval_event = threading.Event()
            approval_result = {'approved': True, 'modified_code': code}

            def callback(approved, modified_code):
                """Callback function executed on main thread after dialog closes"""
                approval_result['approved'] = approved
                approval_result['modified_code'] = modified_code
                approval_event.set()

            # Emit signal to main thread (will show dialog there)
            self.approval_signal.request_approval.emit(code, execution_type, callback)

            # Wait for approval (blocks worker thread, but not main thread)
            approval_event.wait()

            return approval_result['approved'], approval_result['modified_code']

        except Exception as e:
            print(f"Error showing approval dialog: {e}")
            import traceback
            traceback.print_exc()
            # If dialog fails, approve by default (but log error)
            return True, code

    def _show_approval_dialog_on_main_thread(self, code, execution_type, callback):
        """
        Show approval dialog on main thread (called via signal)

        Args:
            code: Code to execute
            execution_type: 'execute_code' or 'work_environment'
            callback: Function to call with (approved, modified_code)
        """
        try:
            from ui.code_approval_dialog import CodeApprovalDialog

            if not self.ai_engine:
                print("Warning: No AI engine available for code explanation")
                callback(True, code)
                return

            # Show dialog on main thread
            approved, modified_code = CodeApprovalDialog.get_approval(
                code, execution_type, self.ai_engine
            )

            # Call callback with results
            callback(approved, modified_code)

        except Exception as e:
            print(f"Error in approval dialog: {e}")
            import traceback
            traceback.print_exc()
            callback(True, code)

    def run_work_environment(self, code):
        """
        Execute code in work environment mode
        AI will see the output and can chain more executions

        Returns:
            str: Formatted output for AI
        """
        # Check for exit command
        if code.lower() == 'exit':
            self.in_work_mode = False
            return "EXITED_WORK_MODE"

        # Check supervised execution (now properly on main thread)
        approved, modified_code = self._check_supervised_execution(code, 'work_environment')

        if not approved:
            return "ERROR:\nCode execution rejected by user"

        # Use modified code if user edited it
        code = modified_code

        # Execute Python code
        result = self.tools['python'].execute(code)

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

        return "\n\n".join(output_parts)

    def run_execute_code(self, code, log_callback=None):
        """
        Execute code directly without returning output to AI
        Used for quick actions like opening apps, showing UI

        Returns:
            dict: {
                'success': bool,
                'message': str,  # Message to show user
                'error': str or None,  # Full traceback if failed
                'stdout': str,  # stdout output
                'stderr': str   # stderr output
            }
        """
        try:
            # Check supervised execution (now properly on main thread)
            approved, modified_code = self._check_supervised_execution(code, 'execute_code')

            if not approved:
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
            code = modified_code

            if log_callback:
                log_callback(f"Executing: {code[:100]}...", "INFO")

            # Check if it's a GUI app that needs subprocess
            result = self._execute_with_gui_support(code)

            # GUI app launched in subprocess
            if result.get('gui_launched'):
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
            if log_callback:
                log_callback(f"Error: {e}", "ERROR")
            return {
                'success': False,
                'message': f"Error: {str(e)}",
                'error': error_trace,
                'stdout': '',
                'stderr': ''
            }

    def get_work_mode_prompt(self):
        """Get the prompt for work mode continuation"""
        from core.global_instructions import WORK_MODE_PROMPT

        output = self.last_work_output or "No previous output"
        return WORK_MODE_PROMPT.format(work_output=output)

    def reset_python(self):
        """Reset Python interpreter state"""
        self.tools['python'].reset()

    def strip_tool_calls(self, text):
        """
        Remove all tool call JSON blocks from text for display purposes.
        Does NOT execute any code — purely for cleaning stored messages before rendering.
        """
        if not text:
            return text
        for key in self._tool_keys:
            text = self._remove_json_from_text(text, None, tool_key=key)
        return text

    # --- Internal helper methods ---

    def _norm_key(self, key):
        """Normalise a key for fuzzy comparison (strip underscores, lowercase)."""
        return key.replace('_', '').lower()

    def _find_canonical_tool_key(self, data):
        """
        Return the canonical tool key present in a parsed JSON dict, or None.
        Handles AI inconsistencies like 'setsessionname' vs 'set_session_name'.
        """
        for k in data:
            canonical = self._tool_keys_norm.get(self._norm_key(k))
            if canonical:
                return canonical
        return None

    def _has_tool_key(self, data, tool_key):
        """Check if data contains tool_key or any fuzzy variant of it."""
        target = self._norm_key(tool_key)
        return any(self._norm_key(k) == target for k in data)

    def _get_tool_value(self, data, tool_key):
        """
        Get value for a tool key using fuzzy matching so that e.g.
        'setsessionname' is found when looking up 'set_session_name'.
        """
        target = self._norm_key(tool_key)
        for k, v in data.items():
            if self._norm_key(k) == target:
                return v
        return None

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
        Extract all valid JSON blocks from text and return the one matching
        tool_key.  Supports both ```json code blocks and bare JSON, and handles
        AI inconsistencies like missing underscores in key names.

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
        candidates = []

        # Collect code-block JSONs: ```json {...} ```
        code_block_pattern = r'```(?:json)?\s*(\{[^`]+\})\s*```'
        for match in re.finditer(code_block_pattern, text, re.DOTALL):
            try:
                data = json.loads(match.group(1).strip())
                if self._find_canonical_tool_key(data):
                    candidates.append(data)
            except json.JSONDecodeError:
                pass

        # Collect bare JSONs — use brace-counting to handle nested braces/f-strings
        start = 0
        while True:
            start_pos = text.find('{', start)
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
                    data = json.loads(text[start_pos:end_pos])
                    if self._find_canonical_tool_key(data):
                        candidates.append(data)
                except json.JSONDecodeError:
                    pass
                start = end_pos
            else:
                break

        if not candidates:
            return None

        if tool_key:
            # Return first candidate that contains the requested key (fuzzy)
            for data in candidates:
                if self._has_tool_key(data, tool_key):
                    return data
            return None

        # No specific key requested — return first found (original behaviour)
        return candidates[0]

    def _remove_json_from_text(self, text, json_data, tool_key=None):
        """
        Remove the JSON block for the given tool_key from text, leaving all
        other tool blocks intact.  Handles both code-fence blocks and bare
        JSON, with fuzzy key matching to cover AI inconsistencies.

        Args:
            text: The original text
            json_data: The parsed JSON dict (used to verify the right block)
            tool_key: The specific tool key whose block should be removed

        Returns:
            str: Text with the matching JSON block removed
        """
        # --- Pattern 1: code-fence blocks ---
        code_block_pattern = r'```(?:json)?\s*(\{[^`]+\})\s*```'
        for match in re.finditer(code_block_pattern, text, re.DOTALL):
            try:
                found_json = json.loads(match.group(1))
                should_remove = (
                    (tool_key and self._has_tool_key(found_json, tool_key)) or
                    (not tool_key and self._find_canonical_tool_key(found_json))
                )
                if should_remove:
                    text = text[:match.start()] + text[match.end():]
                    return self._cleanup_orphaned_braces(text.strip())
            except Exception:
                pass

        # --- Pattern 2: bare JSON objects ---
        # Build key variants to search for (handles missing underscores)
        if tool_key:
            key_variants = [tool_key, tool_key.replace('_', '')]
        else:
            key_variants = self._tool_keys + [k.replace('_', '') for k in self._tool_keys]

        for kv in key_variants:
            pat = r'\{\s*"' + re.escape(kv) + r'"'
            start_match = re.search(pat, text)
            if not start_match:
                continue

            # Walk forward counting braces to find the matching closing brace
            start_pos = start_match.start()
            brace_count = 0
            in_string = False
            escape_next = False

            for i in range(start_pos, len(text)):
                char = text[i]

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
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end_pos = i + 1
                            text = text[:start_pos] + text[end_pos:]
                            return self._cleanup_orphaned_braces(text.strip())

            break  # found a matching start pattern but no end — stop trying

        return self._cleanup_orphaned_braces(text.strip())

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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"gui_exec_{timestamp}.py"
        filepath = os.path.join(self.generated_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(code)

        return filepath

    def _execute_with_gui_support(self, code):
        """
        Execute code with GUI app detection and subprocess support

        Returns:
            dict: Execution result with optional 'gui_launched' flag
        """
        # Detect if it's GUI code
        is_gui = self._detect_gui_code(code)

        if is_gui:
            # Use subprocess for GUI applications
            try:
                # Save code to file
                script_path = self._save_code_to_file(code)
                print(f"Saved GUI script to: {script_path}")

                # Launch in subprocess (non-blocking)
                import sys
                process = subprocess.Popen(
                    [sys.executable, script_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

                # Don't wait - let it run in background
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
                return {
                    'success': False,
                    'error': error_trace,
                    'stdout': '',
                    'stderr': ''
                }
        else:
            # Execute normally for non-GUI code
            result = self.tools['python'].execute(code)
            return result