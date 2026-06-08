"""
core/python_interpreter.py
Python Interpreter Tool - Full interactive Python interpreter using code module
Handles both single-line expressions AND multi-line code blocks
HANDLES ALL CODE FROM CODE EXECUTION TOOL CALLS
"""

import sys
import io
import traceback
import code
import threading
import ctypes
from core.logger import _make_logger, _NoOpLogger
from core.path_syncer import get_syncer


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
        except Exception:
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

    # ── Timeout/interrupt helpers ─────────────────────────────────────────────

    @staticmethod
    def _async_raise(tid, exc_type):
        """Raise an exception in a thread by its thread ID (Windows-safe via ctypes)."""
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(tid), ctypes.py_object(exc_type))
        if res == 0:
            log.warning(f"[PythonInterpreter._async_raise] Invalid thread ID {tid}")
        elif res > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)
            log.warning("[PythonInterpreter._async_raise] Multiple targets — cleaned up")

    def execute(self, code_str, timeout=None, timeout_callback=None):
        """
        Execute Python code using the interactive interpreter

        If timeout (seconds) is provided and timeout_callback is callable,
        it will be called when the timeout fires. The callback receives
        (thread_ident, done_event) and should return:
          - int > 0: extend timeout by this many seconds
          - 0 or False: kill the execution thread

        Returns:
            dict: {
                'success': bool,
                'stdout': str,
                'stderr': str,
                'result': any,
                'error': str or None,
                'execution_count': int,
                'timed_out': bool   (only set when timeout fired)
            }
        """
        # ── Sync system paths before every execution ──────────────────────────
        try:
            get_syncer().merge()
        except Exception as _sync_err:
            log.warning(f"[PythonInterpreter.execute] PathSyncer merge failed (non-fatal): {_sync_err}")
        # ──────────────────────────────────────────────────────────────────────

        self.execution_count += 1
        code_preview = code_str.strip()[:80].replace('\n', '↵')
        log.info(f"[PythonInterpreter.execute] ── Execution #{self.execution_count} ──────────────")
        log.debug(f"[PythonInterpreter.execute] Code preview: '{code_preview}' "
                  f"| total chars: {len(code_str)}")

        # ── If no timeout, run inline (original fast path) ────────────────────
        if timeout is None or not callable(timeout_callback):
            return self._execute_inline(code_str)

        # ── Timed execution via worker thread ─────────────────────────────────
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        result_holder  = {'error': None}
        done_event = threading.Event()
        exec_exception = []

        def _run():
            try:
                r = self._execute_inline(code_str, stdout_capture, stderr_capture)
                result_holder.update(r)
            except BaseException as e:
                exec_exception.append(e)
            finally:
                if hasattr(sys.stdout, 'clear_capture'):
                    sys.stdout.clear_capture()
                if hasattr(sys.stderr, 'clear_capture'):
                    sys.stderr.clear_capture()
                done_event.set()

        exec_thread = threading.Thread(target=_run, daemon=True,
                                       name=f"PyExec-{self.execution_count}")
        exec_thread.start()
        thread_ident = exec_thread.ident

        remaining = timeout
        timed_out = False

        while remaining > 0:
            done = done_event.wait(timeout=remaining)
            if done:
                timed_out = False
                break
            # Timeout hit — ask the callback what to do
            timed_out = True
            log.warning(f"[PythonInterpreter.execute] Execution #{self.execution_count} "
                        f"timed out after {timeout}s")
            decision = timeout_callback(thread_ident, done_event)
            # If execution finished while the dialog was up, treat as normal completion
            if done_event.is_set():
                timed_out = False
                log.info("[PythonInterpreter.execute] Execution completed during timeout dialog — normal result")
                break
            if decision is None:
                decision = 0  # treat None as kill
            if isinstance(decision, (int, float)) and decision > 0:
                remaining = decision
                log.info(f"[PythonInterpreter.execute] User extended by {decision}s")
                continue
            else:
                # Kill the execution thread
                log.warning(f"[PythonInterpreter.execute] User chose to kill execution "
                            f"#{self.execution_count}")
                self._async_raise(thread_ident, KeyboardInterrupt)
                # Give it a moment, then wait a bit more for cleanup
                done_event.wait(timeout=3)
                if not done_event.is_set():
                    log.warning("[PythonInterpreter.execute] Thread didn't stop after interrupt — "
                                "namespace may be inconsistent, resetting interpreter")
                    self.reset()
                break

        # Collect results
        if timed_out:
            stderr_val = stderr_capture.getvalue()
            error_msg = f"Execution timed out after {timeout}s"
            log.error(f"[PythonInterpreter.execute] {error_msg}")
            return {
                'success': False,
                'stdout': stdout_capture.getvalue(),
                'stderr': stderr_val,
                'result': None,
                'error': error_msg,
                'execution_count': self.execution_count,
                'timed_out': True
            }

        if exec_exception:
            result_holder['error'] = traceback.format_exception(type(exec_exception[0]), exec_exception[0], exec_exception[0].__traceback__)
            result_holder['success'] = False
        return result_holder

    def _execute_inline(self, code_str, stdout_capture=None, stderr_capture=None):
        """
        Core execution logic — runs inline in whatever thread calls it.
        Accepts optional pre-allocated StringIO buffers for timed execution.
        """
        need_capture = stdout_capture is None or stderr_capture is None
        if need_capture:
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()

        result = None
        error = None
        success = False

        # Reset last result
        self.interpreter.last_result = None
        log.debug("[PythonInterpreter._execute_inline] last_result reset, stdout/stderr buffers ready")

        try:
            # Detect if code has multiple lines or multiple statements
            code_stripped = code_str.strip()
            has_newlines  = '\n' in code_stripped
            has_semicolons = ';' in code_stripped

            log.debug(f"[PythonInterpreter._execute_inline] Mode detection — "
                      f"has_newlines={has_newlines} | has_semicolons={has_semicolons}")

            if has_newlines or has_semicolons:
                log.info("[PythonInterpreter._execute_inline] → MULTI-LINE / EXEC mode selected")
                try:
                    log.debug("[PythonInterpreter._execute_inline] Compiling code in 'exec' mode")
                    compiled = compile(code_str, '<input>', 'exec')
                    log.debug("[PythonInterpreter._execute_inline] Compile succeeded — running exec()")
                    if hasattr(sys.stdout, 'set_capture'):
                        sys.stdout.set_capture(stdout_capture)
                    if hasattr(sys.stderr, 'set_capture'):
                        sys.stderr.set_capture(stderr_capture)
                    exec(compiled, self.interpreter.locals)
                    if hasattr(sys.stdout, 'clear_capture'):
                        sys.stdout.clear_capture()
                    if hasattr(sys.stderr, 'clear_capture'):
                        sys.stderr.clear_capture()
                    success = True
                    log.info("[PythonInterpreter._execute_inline] exec() finished successfully")

                    lines = code_stripped.split('\n')
                    last_line = lines[-1].strip() if lines else ''
                    log.debug(f"[PythonInterpreter._execute_inline] Inspecting last line for return value: "
                              f"'{last_line}'")

                    if last_line and not any(last_line.startswith(kw) for kw in
                                             ['import', 'from', 'def', 'class', 'if', 'for',
                                              'while', 'with', 'try', 'except', 'finally',
                                              'return', 'break', 'continue', 'pass', 'raise',
                                              'del', 'yield', 'assert', 'global', 'nonlocal', 'print']):
                        log.debug("[PythonInterpreter._execute_inline] Last line passed keyword filter — "
                                  "attempting expression eval for return value")
                        try:
                            compile(last_line, '<input>', 'eval')
                            if last_line.isidentifier():
                                result = self.interpreter.locals.get(last_line)
                                log.debug(f"[PythonInterpreter._execute_inline] Identifier '{last_line}' "
                                          f"resolved from locals → type: {type(result).__name__}")
                            else:
                                log.debug("[PythonInterpreter._execute_inline] Last line is complex expression — "
                                          "skipping re-eval to avoid double execution")
                        except Exception as _inner_e:
                            log.debug(f"[PythonInterpreter._execute_inline] Last-line expression eval skipped: "
                                      f"{_inner_e}")
                    else:
                        log.debug("[PythonInterpreter._execute_inline] Last line is a statement — "
                                  "no return value extraction attempted")

                except SyntaxError as e:
                    log.error(f"[PythonInterpreter._execute_inline] SyntaxError during compile: {e}")
                    error = traceback.format_exc()
                    success = False

            else:
                log.info("[PythonInterpreter._execute_inline] → SINGLE-LINE / REPL mode selected")
                if hasattr(sys.stdout, 'set_capture'):
                    sys.stdout.set_capture(stdout_capture)
                if hasattr(sys.stderr, 'set_capture'):
                    sys.stderr.set_capture(stderr_capture)
                more = self.interpreter.runsource(code_str, '<input>', 'single')
                if hasattr(sys.stdout, 'clear_capture'):
                    sys.stdout.clear_capture()
                if hasattr(sys.stderr, 'clear_capture'):
                    sys.stderr.clear_capture()

                if more:
                    log.warning("[PythonInterpreter._execute_inline] runsource() returned more=True — "
                                "incomplete code block detected")
                    error = "Incomplete code block"
                    success = False
                else:
                    log.info("[PythonInterpreter._execute_inline] runsource() completed — more=False")
                    success = True
                    result = self.interpreter.last_result
                    log.debug(f"[PythonInterpreter._execute_inline] Captured last_result: "
                              f"type={type(result).__name__} | value={repr(result)[:80]}")

        except Exception as e:
            log.error(f"[PythonInterpreter._execute_inline] Outer exception caught: "
                      f"{type(e).__name__}: {e}")
            error = traceback.format_exc()
            success = False
        finally:
            if hasattr(sys.stdout, 'clear_capture'):
                sys.stdout.clear_capture()
            if hasattr(sys.stderr, 'clear_capture'):
                sys.stderr.clear_capture()

        stdout_val = stdout_capture.getvalue()
        stderr_val = stderr_capture.getvalue()

        if stdout_val:
            log.debug(f"[PythonInterpreter._execute_inline] stdout ({len(stdout_val)} chars): "
                      f"{stdout_val[:200].replace(chr(10), '↵')}")
        if stderr_val:
            log.warning(f"[PythonInterpreter._execute_inline] stderr ({len(stderr_val)} chars): "
                        f"{stderr_val[:200].replace(chr(10), '↵')}")
        if error:
            log.error(f"[PythonInterpreter._execute_inline] error field set: {error[:200]}")

        log.info(f"[PythonInterpreter._execute_inline] ── Result: success={success} | "
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