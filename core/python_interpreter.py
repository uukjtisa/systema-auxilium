"""
Python Interpreter Tool - Full interactive Python interpreter using code module
Handles both single-line expressions AND multi-line code blocks
HANDLES ALL CODE FROM CODE EXECUTION TOOL CALLS
"""

import sys
import io
import traceback
import code
from core.logger import _make_logger, _NoOpLogger
from contextlib import redirect_stdout, redirect_stderr


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("PythonInterpreter") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


class CustomInterpreter(code.InteractiveInterpreter):
    """Custom interpreter that captures expression results"""

    def __init__(self, locals=None):
        log.debug("[CustomInterpreter.__init__] Initializing CustomInterpreter "
                  f"| locals provided: {locals is not None}")
        super().__init__(locals)
        self.last_result = None  # Store results here!
        log.debug("[CustomInterpreter.__init__] Ready — last_result reset to None")

    def runcode(self, code_obj):
        """
        Override runcode to capture expression results.
        This is called by runsource() after successful compilation.
        """
        log.debug("[CustomInterpreter.runcode] Executing compiled code object | "
                  f"code flags: {code_obj.co_flags}")

        # Save the current displayhook
        old_displayhook = sys.displayhook
        log.debug("[CustomInterpreter.runcode] Original displayhook saved — installing capture hook")

        # Create custom displayhook that stores the result
        def capture_displayhook(value):
            self.last_result = value  # Store in instance!
            log.debug(f"[CustomInterpreter.runcode] displayhook triggered — "
                      f"captured result type: {type(value).__name__} | value: {repr(value)[:120]}")
            old_displayhook(value)  # Also print it

        sys.displayhook = capture_displayhook

        try:
            log.debug("[CustomInterpreter.runcode] Calling exec() on code object")
            # Execute the code (this will trigger displayhook for expressions)
            exec(code_obj, self.locals)
            log.debug("[CustomInterpreter.runcode] exec() completed without exception")
        except SystemExit:
            log.warning("[CustomInterpreter.runcode] SystemExit raised during exec — re-raising")
            raise
        except:
            log.error("[CustomInterpreter.runcode] Exception during exec — delegating to showtraceback()")
            self.showtraceback()
        finally:
            # Restore displayhook
            sys.displayhook = old_displayhook
            log.debug("[CustomInterpreter.runcode] displayhook restored to original")


class PythonInterpreter:
    """Full interactive Python interpreter with persistent state"""

    def __init__(self):
        log.info("[PythonInterpreter.__init__] Creating PythonInterpreter instance")

        # Create namespace
        self.namespace = {
            '__name__': '__main__',
            '__doc__': None,
        }
        log.debug("[PythonInterpreter.__init__] Base namespace created: "
                  f"{list(self.namespace.keys())}")

        # Create the interactive interpreter
        self.interpreter = CustomInterpreter(self.namespace)
        self.execution_count = 0
        log.info("[PythonInterpreter.__init__] PythonInterpreter ready — "
                 "execution_count=0, interpreter attached")

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
        code_preview = code_str.strip()[:80].replace('\n', '↵')
        log.info(f"[PythonInterpreter.execute] ── Execution #{self.execution_count} ──────────────")
        log.debug(f"[PythonInterpreter.execute] Code preview: '{code_preview}' "
                  f"| total chars: {len(code_str)}")

        # Capture stdout and stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        result = None
        error = None
        success = False

        # Reset last result
        self.interpreter.last_result = None
        log.debug("[PythonInterpreter.execute] last_result reset, stdout/stderr buffers ready")

        try:
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                # Detect if code has multiple lines or multiple statements
                code_stripped = code_str.strip()
                has_newlines  = '\n' in code_stripped
                has_semicolons = ';' in code_stripped

                log.debug(f"[PythonInterpreter.execute] Mode detection — "
                          f"has_newlines={has_newlines} | has_semicolons={has_semicolons}")

                if has_newlines or has_semicolons:
                    log.info("[PythonInterpreter.execute] → MULTI-LINE / EXEC mode selected")

                    # Multi-line or multiple statements - use 'exec' mode
                    try:
                        log.debug("[PythonInterpreter.execute] Compiling code in 'exec' mode")
                        compiled = compile(code_str, '<input>', 'exec')
                        log.debug("[PythonInterpreter.execute] Compile succeeded — running exec()")
                        exec(compiled, self.interpreter.locals)
                        success = True
                        log.info("[PythonInterpreter.execute] exec() finished successfully")

                        # Check if last line looks like an expression that should return a value
                        lines = code_stripped.split('\n')
                        last_line = lines[-1].strip() if lines else ''
                        log.debug(f"[PythonInterpreter.execute] Inspecting last line for return value: "
                                  f"'{last_line}'")

                        # Only try to get a return value if last line looks like a pure expression
                        # (not a statement, not a function call with side effects already executed)
                        if last_line and not any(last_line.startswith(kw) for kw in
                                                 ['import', 'from', 'def', 'class', 'if', 'for',
                                                  'while', 'with', 'try', 'except', 'finally',
                                                  'return', 'break', 'continue', 'pass', 'raise',
                                                  'del', 'yield', 'assert', 'global', 'nonlocal', 'print']):
                            log.debug("[PythonInterpreter.execute] Last line passed keyword filter — "
                                      "attempting expression eval for return value")
                            try:
                                # Try to compile as expression first to see if it's valid
                                compile(last_line, '<input>', 'eval')
                                # If it compiles as expression, get its value from namespace
                                # DON'T re-evaluate it - just check if it's a simple name reference
                                if last_line.isidentifier():
                                    result = self.interpreter.locals.get(last_line)
                                    log.debug(f"[PythonInterpreter.execute] Identifier '{last_line}' "
                                              f"resolved from locals → type: {type(result).__name__}")
                                # For more complex expressions, we'd need to re-eval
                                # but that risks double execution, so skip
                                else:
                                    log.debug("[PythonInterpreter.execute] Last line is complex expression — "
                                              "skipping re-eval to avoid double execution")
                            except Exception as _inner_e:
                                log.debug(f"[PythonInterpreter.execute] Last-line expression eval skipped: "
                                          f"{_inner_e}")
                        else:
                            log.debug("[PythonInterpreter.execute] Last line is a statement — "
                                      "no return value extraction attempted")

                    except SyntaxError as e:
                        log.error(f"[PythonInterpreter.execute] SyntaxError during compile: {e}")
                        error = traceback.format_exc()
                        success = False

                else:
                    log.info("[PythonInterpreter.execute] → SINGLE-LINE / REPL mode selected")
                    # Single line - use 'single' mode for REPL behavior
                    more = self.interpreter.runsource(code_str, '<input>', 'single')

                    if more:
                        log.warning("[PythonInterpreter.execute] runsource() returned more=True — "
                                    "incomplete code block detected")
                        error = "Incomplete code block"
                        success = False
                    else:
                        log.info("[PythonInterpreter.execute] runsource() completed — more=False")
                        success = True
                        result = self.interpreter.last_result
                        log.debug(f"[PythonInterpreter.execute] Captured last_result: "
                                  f"type={type(result).__name__} | value={repr(result)[:80]}")

        except Exception as e:
            log.error(f"[PythonInterpreter.execute] Outer exception caught: {type(e).__name__}: {e}")
            error = traceback.format_exc()
            success = False

        # Collect output
        stdout_val = stdout_capture.getvalue()
        stderr_val = stderr_capture.getvalue()

        if stdout_val:
            log.debug(f"[PythonInterpreter.execute] stdout ({len(stdout_val)} chars): "
                      f"{stdout_val[:200].replace(chr(10), '↵')}")
        if stderr_val:
            log.warning(f"[PythonInterpreter.execute] stderr ({len(stderr_val)} chars): "
                        f"{stderr_val[:200].replace(chr(10), '↵')}")
        if error:
            log.error(f"[PythonInterpreter.execute] error field set: {error[:200]}")

        log.info(f"[PythonInterpreter.execute] ── Result: success={success} | "
                 f"has_result={result is not None} | "
                 f"stdout_len={len(stdout_val)} | stderr_len={len(stderr_val)} ──")

        return {
            'success': success,
            'stdout': stdout_val,
            'stderr': stderr_val,
            'result': result,
            'error': error,
            'execution_count': self.execution_count
        }

    def reset(self):
        """Reset the interpreter state"""
        log.warning("[PythonInterpreter.reset] Resetting interpreter — ALL namespace state will be cleared")
        self.namespace.clear()
        self.namespace.update({
            '__name__': '__main__',
            '__doc__': None,
        })
        self.interpreter = CustomInterpreter(self.namespace)
        self.execution_count = 0
        log.info("[PythonInterpreter.reset] Reset complete — fresh interpreter attached, count=0")

    def get_namespace_info(self):
        """Get information about current namespace"""
        log.debug("[PythonInterpreter.get_namespace_info] Scanning namespace for user-defined vars")
        user_vars = {
            k: type(v).__name__
            for k, v in self.namespace.items()
            if not k.startswith('_')
        }
        log.debug(f"[PythonInterpreter.get_namespace_info] Found {len(user_vars)} user var(s): "
                  f"{list(user_vars.keys())}")
        return user_vars

    def get_available_vars(self):
        """Get list of available variables"""
        log.debug("[PythonInterpreter.get_available_vars] Listing all non-private namespace keys")
        available = [k for k in self.namespace.keys() if not k.startswith('_')]
        log.debug(f"[PythonInterpreter.get_available_vars] {len(available)} variable(s) available: "
                  f"{available}")
        return available
