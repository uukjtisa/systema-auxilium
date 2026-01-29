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
                has_semicolons = ';' in code_stripped

                if has_newlines or has_semicolons:
                    # Multi-line or multiple statements - use 'exec' mode
                    try:
                        compiled = compile(code_str, '<input>', 'exec')
                        exec(compiled, self.interpreter.locals)
                        success = True

                        # Check if last line looks like an expression that should return a value
                        lines = code_stripped.split('\n')
                        last_line = lines[-1].strip() if lines else ''

                        # Only try to get a return value if last line looks like a pure expression
                        # (not a statement, not a function call with side effects already executed)
                        if last_line and not any(last_line.startswith(kw) for kw in
                                                 ['import', 'from', 'def', 'class', 'if', 'for',
                                                  'while', 'with', 'try', 'except', 'finally',
                                                  'return', 'break', 'continue', 'pass', 'raise',
                                                  'del', 'yield', 'assert', 'global', 'nonlocal', 'print']):
                            try:
                                # Try to compile as expression first to see if it's valid
                                compile(last_line, '<input>', 'eval')
                                # If it compiles as expression, get its value from namespace
                                # DON'T re-evaluate it - just check if it's a simple name reference
                                if last_line.isidentifier():
                                    result = self.interpreter.locals.get(last_line)
                                # For more complex expressions, we'd need to re-eval
                                # but that risks double execution, so skip
                            except:
                                pass

                    except SyntaxError as e:
                        error = traceback.format_exc()
                        success = False

                else:
                    # Single line - use 'single' mode for REPL behavior
                    more = self.interpreter.runsource(code_str, '<input>', 'single')

                    if more:
                        error = "Incomplete code block"
                        success = False
                    else:
                        success = True
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