"""
Tool Manager - Unified tool and command system
IMPROVED: Better parsing, double-execution prevention, GUI threading support
"""
import textwrap

from core.python_interpreter import PythonInterpreter
from core.global_instructions import TOOL_MODE_PROMPT
import json
import re
import threading
import hashlib


class ToolManager:
    """Manages all available tools and commands with unified execution"""

    def __init__(self):
        self.tools = {
            'python_interpreter': PythonInterpreter(),
        }
        self.in_tool_mode = False
        self.last_tool_output = None

    def _extract_all_json_blocks(self, text):
        """
        Extract ALL JSON blocks from text (code blocks and bare JSON)
        Returns list of (json_data, start_pos, end_pos, json_string)
        """
        found_blocks = []

        # Pattern 1: Code blocks with ```json or ```
        code_block_pattern = r'```(?:json)?\s*(\{[^`]+\})\s*```'
        for match in re.finditer(code_block_pattern, text, re.MULTILINE | re.DOTALL):
            json_str = match.group(1).strip()
            try:
                data = json.loads(json_str)
                found_blocks.append((data, match.start(), match.end(), json_str))
            except json.JSONDecodeError:
                continue

        # Pattern 2: Bare JSON objects (not inside code blocks)
        # Only look for JSON outside already-found code blocks
        covered_ranges = [(start, end) for _, start, end, _ in found_blocks]

        bare_json_pattern = r'\{[^{}]*"(?:tool|command)"[^{}]*\}'
        for match in re.finditer(bare_json_pattern, text, re.MULTILINE | re.DOTALL):
            # Check if this position is already covered by a code block
            pos = match.start()
            is_covered = any(start <= pos <= end for start, end in covered_ranges)

            if not is_covered:
                json_str = match.group(0).strip()
                try:
                    data = json.loads(json_str)
                    found_blocks.append((data, match.start(), match.end(), json_str))
                except json.JSONDecodeError:
                    continue

        # Sort by position to maintain order
        found_blocks.sort(key=lambda x: x[1])
        return found_blocks

    def _is_valid_tool_call(self, json_data):
        """
        Check if JSON is a valid tool call
        Simple check: has "tool" key with non-empty string value
        """
        if not isinstance(json_data, dict):
            return False

        if 'tool' not in json_data:
            return False

        tool_name = json_data.get('tool')
        if not isinstance(tool_name, str) or not tool_name.strip():
            return False

        return True

    def _is_valid_command_call(self, json_data):
        """
        Check if JSON is a valid command call
        Simple check: has "command" key with non-empty string value
        """
        if not isinstance(json_data, dict):
            return False

        if 'command' not in json_data:
            return False

        command_name = json_data.get('command')
        if not isinstance(command_name, str) or not command_name.strip():
            return False

        return True

    def _get_execution_hash(self, call_type, name, input_code):
        """Generate hash for execution tracking"""
        hash_str = f"{call_type}:{name}:{input_code}"
        return hashlib.md5(hash_str.encode()).hexdigest()

    def parse_tool_call(self, text):
        """
        Parse tool calls from AI output using JSON format

        Returns:
            tuple: (tool_name, tool_input, remaining_text)
            or None if no tool call found
        """
        # Extract all JSON blocks
        json_blocks = self._extract_all_json_blocks(text)

        # Find first valid tool call
        for data, start_pos, end_pos, json_str in json_blocks:
            if self._is_valid_tool_call(data):
                tool_name = data['tool'].strip()
                tool_input = data.get('input', '').strip()
                remaining_text = text[:start_pos] + text[end_pos:]
                remaining_text = remaining_text.strip()

                return tool_name, tool_input, remaining_text

        return None

    def parse_command_call(self, text):
        """
        Parse command calls from AI output using JSON format

        Returns:
            tuple: (command_name, command_input, remaining_text)
            or None if no command call found
        """
        # Extract all JSON blocks
        json_blocks = self._extract_all_json_blocks(text)

        # Find first valid command call
        for data, start_pos, end_pos, json_str in json_blocks:
            if self._is_valid_command_call(data):
                command_name = data['command'].strip()
                command_input = data.get('input', '').strip()
                remaining_text = text[:start_pos] + text[end_pos:]
                remaining_text = remaining_text.strip()

                return command_name, command_input, remaining_text

        return None

    def execute_tool(self, tool_name, tool_input):
        """
        Execute a tool and return formatted output
        Tools enter tool mode and return values for AI to analyze

        Returns:
            str: Formatted tool output for AI
        """
        # Exit detection
        if tool_name.lower() == 'exit_from_tools':
            self.in_tool_mode = False
            return "EXITED_TOOL_MODE"

        if tool_name not in self.tools:
            return f"ERROR: Unknown tool '{tool_name}'"

        if tool_name == 'python_interpreter':
            result = self.tools['python_interpreter'].execute(tool_input)

            # Format output for AI
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
                print(output_parts)
                output_parts.append("Code executed successfully (no output)")

            return "\n\n".join(output_parts)

        return f"ERROR: Tool '{tool_name}' not implemented"

    def _execute_in_thread(self, code_str):
        """
        Execute code in a separate thread for GUI apps
        Detects tkinter/GUI code and runs appropriately
        """
        result_container = {}

        def run_code():
            try:
                result_container['result'] = self.tools['python_interpreter'].execute(code_str)
            except Exception as e:
                result_container['result'] = {
                    'success': False,
                    'stdout': '',
                    'stderr': '',
                    'result': None,
                    'error': str(e),
                    'execution_count': 0
                }

        # Check if code contains GUI keywords
        gui_keywords = ['tkinter', 'tk.Tk()', 'mainloop()', 'pygame', 'wx.App', 'PyQt', 'PySide']
        needs_thread = any(keyword.lower() in code_str.lower() for keyword in gui_keywords)

        if needs_thread:
            # Run in separate thread for GUI apps
            thread = threading.Thread(target=run_code, daemon=True)
            thread.start()
            thread.join(timeout=0.5)  # Quick timeout - GUI apps start fast

            # If thread is still running, it's likely a GUI with mainloop
            if thread.is_alive():
                return {
                    'success': True,
                    'stdout': '',
                    'stderr': '',
                    'result': None,
                    'error': None,
                    'execution_count': 0,
                    'gui_launched': True
                }
        else:
            # Run normally for non-GUI code
            run_code()

        return result_container.get('result', {
            'success': False,
            'stdout': '',
            'stderr': '',
            'result': None,
            'error': 'Execution failed',
            'execution_count': 0
        })

    def execute_command(self, command_name, command_input, log_callback=None):
        """
        Execute a command - works exactly like tools but doesn't return to AI
        Commands don't enter tool mode and don't give output back to AI

        Returns:
            dict: {
                'success': bool,
                'visible_message': str  # Message to show user
            }
        """
        if command_name == 'python_interpreter':
            try:
                if log_callback:
                    log_callback(f"Executing command: {command_input[:100]}...", "INFO")

                # Execute using threading support for GUI apps
                result = self._execute_in_thread(command_input)

                # Check if it's a GUI app that launched
                if result.get('gui_launched'):
                    if log_callback:
                        log_callback("✓ GUI application launched", "SUCCESS")
                    return {
                        'success': True,
                        'visible_message': "GUI launched. Please confirm if it appeared on your screen."
                    }

                # Check if execution was successful
                if result['success']:
                    if log_callback:
                        log_callback("✓ Command executed successfully", "SUCCESS")

                    return {
                        'success': True,
                        'visible_message': "Command executed. Please confirm if it worked as expected."
                    }
                else:
                    # Execution failed
                    error_msg = result.get('error', 'Unknown error')
                    if log_callback:
                        log_callback(f"✗ Command failed: {error_msg}", "ERROR")

                    return {
                        'success': False,
                        'visible_message': f"Command failed: {error_msg}"
                    }

            except Exception as e:
                if log_callback:
                    log_callback(f"Command error: {e}", "ERROR")

                return {
                    'success': False,
                    'visible_message': f"Command error: {str(e)}"
                }

        return {
            'success': False,
            'visible_message': f"Unknown command: {command_name}"
        }

    def get_tool_mode_prompt(self):
        """Get the prompt for tool mode"""
        if self.last_tool_output:
            return TOOL_MODE_PROMPT.format(tool_output=self.last_tool_output)
        return TOOL_MODE_PROMPT.format(tool_output="No previous output")

    def reset_python_interpreter(self):
        """Reset the Python interpreter state"""
        self.tools['python_interpreter'].reset()