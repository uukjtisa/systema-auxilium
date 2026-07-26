"""
core/python_interpreter.py
Python Interpreter Tool - Full interactive Python interpreter using code module
Handles both single-line expressions AND multi-line code blocks
HANDLES ALL CODE FROM CODE EXECUTION TOOL CALLS
"""

import sys
import os
import io
import builtins
import time
import traceback
import code
import threading
import ctypes
from systema.common.logger import _make_logger, _NoOpLogger
from systema.execution.path_syncer import get_syncer


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("PythonInterpreter") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


def _wm_write_file(path, content, mode=None, encoding="utf-8"):
    """Write literal data to a file, creating parent directories as needed.

    Always available inside python_interpreter as write_file() — for data the
    code itself produced. (Standalone file writes use the write_file TOOL.)
    Accepts str (text mode) or bytes (binary mode). Returns the path written."""
    p = os.fspath(path)
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if isinstance(content, (bytes, bytearray)):
        with open(p, mode or "wb") as f:
            f.write(content)
    else:
        with open(p, mode or "w", encoding=encoding) as f:
            f.write(content)
    return p


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

        # Create custom displayhook that stores the result.
        #
        # CAPTURE ONLY — it must not also print. The default hook writes repr()
        # to stdout the way a REPL echoes an expression, but the value is then
        # ALSO reported by run_python_interpreter as its own RESULT section, so
        # a bare expression came back to the model twice:
        #
        #   STDOUT:
        #   '[attach_image_to_context] Queued for ONE-TURN analysis: ...'
        #   RESULT:
        #   [attach_image_to_context] Queued for ONE-TURN analysis: ...
        #
        # (Only for a bare expression with a non-None value — code that print()s
        # never fired the hook, which is why it looked intermittent.)
        def capture_displayhook(value):
            self.last_result = value  # Store in instance!
            log.debug(f"[CustomInterpreter.runcode] displayhook triggered — "
                      f"captured result type: {type(value).__name__} | value: {repr(value)[:120]}")
            # The interpreter is persistent, so `_` stays useful across steps —
            # that half of the default hook's job is kept, the printing is not.
            try:
                builtins._ = value
            except Exception:
                pass

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
        self._install_helpers()
        # Live-output buffers — point at the currently-executing code's capture
        # StringIOs so another thread (the GUI) can stream output as it runs.
        self._live_stdout = None
        self._live_stderr = None
        # Thread id of the code currently running — lets another thread interrupt it.
        self._current_exec_tid = None
        # Interrupt escalation state: interrupt_current() sets the event; the
        # execute() supervisor loop watches it and escalates (async-exc →
        # child-process kill → abandon) when the exec thread is blocked in a
        # C call the async exception can't reach.
        self._interrupt_event = threading.Event()
        self._interrupt_time = 0.0
        log.info("[PythonInterpreter.__init__] PythonInterpreter ready — "
                 "execution_count=0, interpreter attached")

    def interrupt_current(self) -> bool:
        """Raise KeyboardInterrupt in the currently-executing code thread, so a
        running work-mode execution stops while preserving its partial output.
        Returns True if an interrupt was delivered, False if nothing was running.
        Safe to call from another thread (e.g. the GUI).

        The async exception only lands when the thread runs Python bytecode —
        code blocked in a C call (subprocess.run, time.sleep, sockets) never
        sees it. The execute() supervisor watches _interrupt_event and
        escalates: kill child processes spawned by the step (unblocks a
        subprocess wait so the pending interrupt fires), then abandon the
        thread + reset the interpreter as a last resort."""
        tid = self._current_exec_tid
        if tid:
            log.warning(f"[PythonInterpreter.interrupt_current] Interrupting exec thread {tid}")
            self._interrupt_time = time.monotonic()
            self._interrupt_event.set()
            self._async_raise(tid, KeyboardInterrupt)
            return True
        log.debug("[PythonInterpreter.interrupt_current] No execution in progress")
        return False

    @staticmethod
    def _snapshot_children():
        """PIDs of this process's children before a step runs — the baseline
        for _kill_new_children. None when psutil is unavailable."""
        try:
            import psutil
            return {p.pid for p in psutil.Process().children(recursive=True)}
        except Exception:
            return None

    @staticmethod
    def _kill_new_children(before):
        """Terminate child processes spawned since the step started. A
        C-blocked subprocess wait is the #1 reason an interrupt can't land —
        killing the child returns the wait, and the pending KeyboardInterrupt
        fires on the next bytecode instruction. May also catch an unrelated
        process the app spawned mid-step (rare); an unstoppable step is the
        worse failure."""
        try:
            import psutil
            new = [p for p in psutil.Process().children(recursive=True)
                   if before is None or p.pid not in before]
            if not new:
                return 0
            log.warning(f"[PythonInterpreter._kill_new_children] Terminating "
                        f"{len(new)} child process(es) spawned by the "
                        f"interrupted step: {[p.pid for p in new]}")
            for p in new:
                try:
                    p.terminate()
                except Exception:
                    pass
            _gone, alive = psutil.wait_procs(new, timeout=1.5)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass
            return len(new)
        except Exception as e:
            log.warning(f"[PythonInterpreter._kill_new_children] skipped: {e}")
            return 0

    def peek_live_output(self) -> str:
        """Best-effort snapshot of the currently-executing code's stdout+stderr.
        Safe to call from another thread (e.g. the GUI) while code runs — returns
        '' when nothing is executing. Reads are tolerant of torn StringIO state."""
        out = ""
        try:
            so = self._live_stdout
            if so is not None:
                out = so.getvalue()
        except Exception:
            pass
        try:
            se = self._live_stderr
            if se is not None:
                err = se.getvalue()
                if err:
                    out = f"{out}\n{err}" if out else err
        except Exception:
            pass
        return out

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

        # ── Supervised execution via worker thread ────────────────────────────
        # ALWAYS supervised (the old no-timeout path ran inline on the calling
        # thread, which made a user interrupt powerless against code blocked in
        # a C call — the async KeyboardInterrupt never landed and nothing else
        # could unblock it). The supervisor watches for:
        #   • completion (done_event),
        #   • the optional timeout (extend-or-kill via timeout_callback),
        #   • a user interrupt (escalation: the already-delivered async-exc →
        #     kill child processes spawned by the step → abandon the thread
        #     and reset the interpreter).
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        result_holder  = {'error': None}
        done_event = threading.Event()
        exec_exception = []
        self._interrupt_event.clear()
        children_before = self._snapshot_children()

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

        timed = timeout is not None and callable(timeout_callback)
        deadline = (time.monotonic() + timeout) if timed else None
        timed_out = False
        abandoned = False
        interrupt_children_killed = False

        while not done_event.wait(timeout=0.25):
            now = time.monotonic()

            # ── Interrupt escalation ─────────────────────────────────────────
            if self._interrupt_event.is_set():
                since = now - self._interrupt_time
                if since > 1.5 and not interrupt_children_killed:
                    # The async-exc hasn't landed — the thread is blocked in a
                    # C call. Kill the step's child processes to unblock it.
                    self._kill_new_children(children_before)
                    interrupt_children_killed = True
                elif since > 5.0:
                    # Still blocked (sleep/socket/native code with no child to
                    # kill). Abandon the daemon thread and reset the
                    # interpreter — the user asked for a stop, they get one.
                    log.warning("[PythonInterpreter.execute] Interrupted thread "
                                "still blocked after escalation — abandoning it "
                                "and resetting the interpreter")
                    abandoned = True
                    break

            # ── Timeout handling (extend-or-kill, unchanged contract) ────────
            if deadline is not None and now >= deadline:
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
                    deadline = time.monotonic() + decision
                    timed_out = False
                    log.info(f"[PythonInterpreter.execute] User extended by {decision}s")
                    continue
                # Kill the execution thread
                log.warning(f"[PythonInterpreter.execute] User chose to kill execution "
                            f"#{self.execution_count}")
                self._async_raise(thread_ident, KeyboardInterrupt)
                # Unblock a C-level wait too, then give cleanup a moment.
                self._kill_new_children(children_before)
                done_event.wait(timeout=3)
                if not done_event.is_set():
                    log.warning("[PythonInterpreter.execute] Thread didn't stop after interrupt — "
                                "namespace may be inconsistent, resetting interpreter")
                    self.reset()
                break

        # Collect results
        if abandoned:
            # Release the zombie's interrupt bookkeeping so the next execution
            # starts clean (its finally skips cleanup once tid is cleared here).
            self._current_exec_tid = None
            self._live_stdout = None
            self._live_stderr = None
            self._interrupt_event.clear()
            self.reset()
            return {
                'success': False,
                'stdout': stdout_capture.getvalue(),
                'stderr': stderr_capture.getvalue(),
                'result': None,
                'error': ("KeyboardInterrupt: execution interrupted by user "
                          "(the code was blocked in a non-interruptible call; "
                          "its thread was abandoned and the interpreter session "
                          "was reset — variables from this session are gone)"),
                'execution_count': self.execution_count,
            }

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

        # Publish buffers for live streaming (GUI polls peek_live_output()).
        self._live_stdout = stdout_capture
        self._live_stderr = stderr_capture
        # Publish this thread's id so interrupt_current() can stop it mid-run.
        self._current_exec_tid = threading.get_ident()

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

        except KeyboardInterrupt:
            # User interrupted a running work-mode execution — keep partial output.
            log.warning("[PythonInterpreter._execute_inline] KeyboardInterrupt — "
                        "execution interrupted by user")
            error = "KeyboardInterrupt: execution interrupted by user"
            success = False
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
            # Stop publishing — but only OUR OWN state. An abandoned zombie
            # thread finishing late must never wipe the tid/buffers of a newer
            # execution that started after it was given up on.
            if self._current_exec_tid == threading.get_ident():
                self._live_stdout = None
                self._live_stderr = None
                self._current_exec_tid = None

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
        self._install_helpers()
        log.info("[PythonInterpreter.reset] Reset complete — fresh interpreter attached, count=0")

    def _install_helpers(self):
        """Inject always-available python interpreter helpers into the namespace."""
        self.namespace['write_file'] = _wm_write_file

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