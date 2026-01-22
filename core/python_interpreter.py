"""
Python Interpreter Tool - Full interactive Python interpreter using code module
FIXED: Now handles both single-line expressions AND multi-line code blocks
"""

import sys
import io
import traceback
import code
from contextlib import redirect_stdout, redirect_stderr


class CustomInterpreter(code.InteractiveInterpreter):
    """Custom interpreter that captures expression results"""

    def __init__(self, locals=None):
        super().__init__(locals)
        self.last_result = None  # Store results here!

    def runcode(self, code_obj):
        """
        Override runcode to capture expression results.
        This is called by runsource() after successful compilation.
        """
        # Save the current displayhook
        old_displayhook = sys.displayhook

        # Create custom displayhook that stores the result
        def capture_displayhook(value):
            self.last_result = value  # Store in instance!
            old_displayhook(value)  # Also print it

        sys.displayhook = capture_displayhook

        try:
            # Execute the code (this will trigger displayhook for expressions)
            exec(code_obj, self.locals)
        except SystemExit:
            raise
        except:
            self.showtraceback()
        finally:
            # Restore displayhook
            sys.displayhook = old_displayhook


class PythonInterpreter:
    """Full interactive Python interpreter with persistent state"""

    def __init__(self):
        # Create namespace
        self.namespace = {
            '__name__': '__main__',
            '__doc__': None,
        }

        # Create the interactive interpreter
        self.interpreter = CustomInterpreter(self.namespace)
        self.execution_count = 0

    def execute(self, code_str):
        """
        Execute Python code using the interactive interpreter

        Returns:
            dict: {
                'success': bool,
                'stdout': str,
                'stderr': str,
                'result': any,
                'error': str or None,
                'execution_count': int
            }
        """
        self.execution_count += 1

        # Capture stdout and stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        result = None
        error = None
        success = False

        # Reset last result
        self.interpreter.last_result = None

        try:
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                # Detect if code has multiple lines or multiple statements
                code_stripped = code_str.strip()
                has_newlines = '\n' in code_stripped

                # Try to detect multiple statements on same line
                # (This is a heuristic - not perfect but catches common cases)
                has_semicolons = ';' in code_stripped

                # Decide on compilation mode
                if has_newlines or has_semicolons:
                    # Multi-line or multiple statements - use 'exec' mode
                    mode = 'exec'

                    # For exec mode, we need to manually handle expression evaluation
                    # Split into lines and check if last line is an expression
                    lines = code_stripped.split('\n')
                    last_line = lines[-1].strip() if lines else ''

                    # Try to compile everything
                    try:
                        compiled = compile(code_str, '<input>', 'exec')
                        exec(compiled, self.interpreter.locals)
                        success = True

                        # Now try to evaluate the last line as an expression if it looks like one
                        if last_line and not any(last_line.startswith(kw) for kw in
                                                ['import', 'from', 'def', 'class', 'if', 'for',
                                                 'while', 'with', 'try', 'except', 'finally',
                                                 'return', 'break', 'continue', 'pass', 'raise',
                                                 'del', 'yield', 'assert', 'global', 'nonlocal']):
                            try:
                                # Try to evaluate last line as expression
                                result = eval(last_line, self.interpreter.locals)
                                self.interpreter.last_result = result
                            except:
                                # Last line wasn't an expression, that's fine
                                pass

                    except SyntaxError as e:
                        error = traceback.format_exc()
                        success = False

                else:
                    # Single line - use 'single' mode for REPL behavior
                    more = self.interpreter.runsource(code_str, '<input>', 'single')

                    if more:
                        # Code is incomplete
                        error = "Incomplete code block"
                        success = False
                    else:
                        # Code executed successfully
                        success = True
                        # Get the result that was captured by our displayhook
                        result = self.interpreter.last_result

        except Exception as e:
            error = traceback.format_exc()
            success = False

        return {
            'success': success,
            'stdout': stdout_capture.getvalue(),
            'stderr': stderr_capture.getvalue(),
            'result': result,
            'error': error,
            'execution_count': self.execution_count
        }

    def reset(self):
        """Reset the interpreter state"""
        self.namespace.clear()
        self.namespace.update({
            '__name__': '__main__',
            '__doc__': None,
        })
        self.interpreter = CustomInterpreter(self.namespace)
        self.execution_count = 0

    def get_namespace_info(self):
        """Get information about current namespace"""
        user_vars = {
            k: type(v).__name__
            for k, v in self.namespace.items()
            if not k.startswith('_')
        }
        return user_vars

    def get_available_vars(self):
        """Get list of available variables"""
        return [k for k in self.namespace.keys() if not k.startswith('_')]