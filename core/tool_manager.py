"""
Tool Manager - Simplified work environment and code execution system
REVAMPED: Clean architecture, easy to extend with new tools
"""

import json
import re
import threading
from core.python_interpreter import PythonInterpreter


class ToolManager:
    """Manages work environment and code execution with unified tool system"""

    def __init__(self):
        # Available tools - easy to add more!
        self.tools = {
            'python': PythonInterpreter(),
        }

        # Work mode state
        self.in_work_mode = False
        self.last_work_output = None

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
                'message': str  # Message to show user
            }
        """
        try:
            if log_callback:
                log_callback(f"Executing: {code[:100]}...", "INFO")

            # Check if it's a GUI app that needs threading
            result = self._execute_with_gui_support(code)

            # GUI app launched in background
            if result.get('gui_launched'):
                if log_callback:
                    log_callback("✓ GUI application launched", "SUCCESS")
                return {
                    'success': True,
                    'message': "GUI launched. Did it appear on your screen?"
                }

            # Normal execution completed
            if result['success']:
                if log_callback:
                    log_callback("✓ Code executed successfully", "SUCCESS")
                return {
                    'success': True,
                    'message': "Code executed. Did it work as expected?"
                }

            # Execution failed
            error_msg = result.get('error', 'Unknown error')
            if log_callback:
                log_callback(f"✗ Execution failed: {error_msg}", "ERROR")
            return {
                'success': False,
                'message': f"Execution failed: {error_msg}"
            }

        except Exception as e:
            if log_callback:
                log_callback(f"Error: {e}", "ERROR")
            return {
                'success': False,
                'message': f"Error: {str(e)}"
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
        json_str = json.dumps(json_data, indent=2)

        # Try to find and remove the JSON (with or without code block markers)
        patterns = [
            rf'```json\s*{re.escape(json_str)}\s*```',
            rf'```\s*{re.escape(json_str)}\s*```',
            re.escape(json_str)
        ]

        for pattern in patterns:
            text = re.sub(pattern, '', text, flags=re.DOTALL)

        return text.strip()

    def _execute_with_gui_support(self, code):
        """
        Execute code with GUI app detection and threading support

        Returns:
            dict: Execution result with optional 'gui_launched' flag
        """
        result_container = {}

        def run():
            try:
                result_container['result'] = self.tools['python'].execute(code)
            except Exception as e:
                result_container['result'] = {
                    'success': False,
                    'stdout': '',
                    'stderr': '',
                    'result': None,
                    'error': str(e),
                    'execution_count': 0
                }

        # Detect GUI keywords
        gui_keywords = ['tkinter', 'tk.Tk()', 'mainloop()', 'pygame',
                       'wx.App', 'PyQt', 'PySide']
        needs_thread = any(kw.lower() in code.lower() for kw in gui_keywords)

        if needs_thread:
            # Run in separate thread for GUI apps
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            thread.join(timeout=0.5)

            # If still running, it's probably a GUI with mainloop
            if thread.is_alive():
                return {
                    'success': True,
                    'gui_launched': True
                }
        else:
            # Run normally for non-GUI code
            run()

        return result_container.get('result', {
            'success': False,
            'error': 'Execution failed'
        })