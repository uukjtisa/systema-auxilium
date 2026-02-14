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
        json_data = self._extract_json(text)

        if json_data and 'work_environment' in json_data:
            code = json_data.get('input', '').strip()
            # Remove JSON block from text
            remaining_text = self._remove_json_from_text(text, json_data)
            return code, remaining_text

        return None

    def parse_execute_code(self, text):
        """
        Parse execute_code call from AI output

        Returns:
            tuple: (code, remaining_text) or None if not found
        """
        json_data = self._extract_json(text)

        if json_data and 'execute_code' in json_data:
            code = json_data.get('input', '').strip()
            # Remove JSON block from text
            remaining_text = self._remove_json_from_text(text, json_data)
            return code, remaining_text

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
            approval_result = {'approved': True, 'modified_code': code, 'dont_show_again': False}

            def callback(approved, modified_code, dont_show_again):
                """Callback function executed on main thread after dialog closes"""
                approval_result['approved'] = approved
                approval_result['modified_code'] = modified_code
                approval_result['dont_show_again'] = dont_show_again
                approval_event.set()

            # Emit signal to main thread (will show dialog there)
            self.approval_signal.request_approval.emit(code, execution_type, callback)

            # Wait for approval (blocks worker thread, but not main thread)
            approval_event.wait()

            # Update settings if user chose "don't show again"
            if approval_result['dont_show_again'] and self.settings_callback:
                settings = self.settings_callback()
                settings['supervised_execution'] = False
                # Note: Settings will be saved by controller

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
            callback: Function to call with (approved, modified_code, dont_show_again)
        """
        try:
            from ui.code_approval_dialog import CodeApprovalDialog

            if not self.ai_engine:
                print("Warning: No AI engine available for code explanation")
                callback(True, code, False)
                return

            # Show dialog on main thread
            approved, modified_code, dont_show_again = CodeApprovalDialog.get_approval(
                code, execution_type, self.ai_engine
            )

            # Call callback with results
            callback(approved, modified_code, dont_show_again)

        except Exception as e:
            print(f"Error in approval dialog: {e}")
            import traceback
            traceback.print_exc()
            callback(True, code, False)

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

    # --- Internal helper methods ---

    def _extract_json(self, text):
        """
        Extract first valid JSON block from text
        Supports both ```json code blocks and bare JSON

        Returns:
            dict or None
        """
        # Try code blocks first: ```json {...} ```
        code_block_pattern = r'```(?:json)?\s*(\{[^`]+\})\s*```'
        match = re.search(code_block_pattern, text, re.DOTALL)

        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try bare JSON: {...}
        bare_json_pattern = r'\{[^{}]*"(?:work_environment|execute_code)"[^{}]*\}'
        match = re.search(bare_json_pattern, text, re.DOTALL)

        if match:
            try:
                return json.loads(match.group(0).strip())
            except json.JSONDecodeError:
                pass

        return None

    def _remove_json_from_text(self, text, json_data):
        """Remove the JSON block from text to get remaining message"""
        # Strategy: Find and remove the original JSON block directly from text
        # This avoids formatting mismatches from json.dumps()

        # Pattern 1: Remove ```json ... ``` blocks
        code_block_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        match = re.search(code_block_pattern, text, re.DOTALL)
        if match:
            # Verify this is the right JSON by checking if it has the tool key
            try:
                found_json = json.loads(match.group(1))
                if 'work_environment' in found_json or 'execute_code' in found_json:
                    text = text[:match.start()] + text[match.end():]
                    return text.strip()
            except:
                pass

        # Pattern 2: Remove bare JSON objects
        # Use a more sophisticated approach - find the JSON and remove it
        # Look for the opening brace of a JSON with our tool keys
        start_pattern = r'\{\s*"(?:work_environment|execute_code)"'
        start_match = re.search(start_pattern, text)

        if start_match:
            # Find the matching closing brace by counting braces
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
                            # Found the end!
                            end_pos = i + 1
                            text = text[:start_pos] + text[end_pos:]
                            return text.strip()

        return text.strip()

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