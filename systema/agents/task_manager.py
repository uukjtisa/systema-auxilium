"""
core/task_manager.py
Task Manager - Background automation layer for scheduled AI task sessions.
Each task runs in its own daemon thread with an isolated AI caller.
"""

import json
import threading
import re
import io
import sys
import traceback
import uuid
from datetime import datetime, date, timedelta
import math
from pathlib import Path
from systema.common.logger import _make_logger, _NoOpLogger
from systema.execution.python_interpreter import PythonInterpreter

_verbose = True
log = _make_logger("TaskManager") if _verbose else _NoOpLogger()

from systema import APP_ROOT as _APP_ROOT


# ── Timestamp helper ──────────────────────────────────────────────────────────

def _now_stamp():
    """Returns e.g. 'Monday, May 19, 2025 - 10:27 PM'"""
    return datetime.now().strftime("%A, %B %d, %Y - %I:%M %p")


# ── {{code}} block resolver ───────────────────────────────────────────────────

def _resolve_instruction(instruction: str, interpreter=None, functions: list = None) -> tuple:
    """
    Finds all {{...}} blocks in instruction text, executes each one
    using a shared PythonInterpreter instance (persistent namespace across blocks),
    and replaces the block with its output inline.

    Returns:
        (resolved_text: str, error_traceback: str | None)
        If any block errors, returns original instruction + traceback.
    """
    pattern = re.compile(r'\{\{(.*?)\}\}', re.DOTALL)
    errors = []
    inject_errors = []
    interp = interpreter if interpreter is not None else PythonInterpreter()

    # Pre-inject saved functions so {{ my_func() }} works in instruction blocks.
    # Errors here used to be swallowed silently; now we keep them so a {{ block }}
    # that fails because its helper never defined gets a real explanation.
    if functions:
        for _fn in functions:
            result = interp.execute(_fn['code'])
            if result.get('error'):
                inject_errors.append(f"[function '{_fn.get('name', '?')}'] {result['error']}")

    def _run_block(match):
        code = match.group(1).strip()
        result = interp.execute(code)

        if result['error']:
            errors.append(result['error'])
            return match.group(0)  # leave block unchanged on error

        parts = []
        if result['stdout']:
            parts.append(result['stdout'].rstrip())
        if result['result'] is not None:
            parts.append(str(result['result']))
        return '\n'.join(parts) if parts else ''

    resolved = pattern.sub(_run_block, instruction)

    if errors:
        # A {{ block }} hard-failed → skip the ping and report. Prefix any failed
        # function injections since they're the likely root cause.
        combined = errors[:]
        if inject_errors:
            combined = ["Function library load errors (likely root cause):",
                        *inject_errors, "", "Instruction block errors:", *errors]
        return instruction, '\n'.join(combined)

    if inject_errors:
        # No block hard-failed, but a saved function failed to load. Don't kill
        # the ping — surface it as a visible banner so it's never silent (the
        # interpreter swallows runtime errors in single-line {{ }} blocks, so a
        # broken helper would otherwise just render as empty).
        banner = ("⚠️ Function library load warning — some saved functions "
                  "failed to load and may be unavailable in {{ }} blocks:\n  "
                  + "\n  ".join(inject_errors))
        log.warning(f"[_resolve_instruction] {banner}")
        return f"{banner}\n\n{resolved}", None

    return resolved, None


# ── Task AI Engine ────────────────────────────────────────────────────────────

class TaskAIEngine:
    """
    Isolated AI caller for background task sessions.

    Owns a dedicated AIEngine instance — completely separate from the main
    conversation engine so task pings never block or interfere with the user.

    Settings are synced from the controller before every call, so any
    provider/model change the user makes is picked up automatically.
    """

    # Matches: {"tool": "send_message_main", "input": "..."}
    _SEND_MSG_RE = re.compile(
        r'\{[^{}]*"tool"\s*:\s*"send_message_main"[^{}]*"input"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        re.DOTALL
    )

    def __init__(self, controller, task: dict | None = None, send_main_callback=None):
        self._controller = controller
        self._task = task or {}
        self._engine = None
        # Delivery channel for the send_message_main() namespace function. Lets the
        # task agent push a message to the main chat from INSIDE python_interpreter
        # Python code — works identically in compat and native mode (it's just a
        # function call, not a fenced/JSON tool the model has to format as text).
        self._send_main_callback = send_main_callback
        self._pending_context_images = []
        self._images_lock = threading.Lock()
        self._init_engine()

    def _init_engine(self):
        """Spin up a private AIEngine instance using current controller settings."""
        try:
            from systema.engine.ai_engine import AIEngine
            self._engine = AIEngine(
                settings_callback=lambda: self._controller.settings,
            )
            # Background mode — no UI, no approval dialogs, and no live
            # streaming (its deltas belong to no chat turn).
            self._engine.tool_manager._get_chat = lambda: None
            self._engine.tool_manager._get_android_bridge = lambda: None
            self._engine.allow_streaming = False
            # Resilience: retry transient provider failures so a single hiccup
            # doesn't kill an unattended background ping (main session uses 0).
            self._engine.provider_max_retries = 3
            self._engine.provider_retry_backoff = 3.0
            perms = self._task.get('permissions', {})
            self._engine.tool_manager.allow_workmode = perms.get('allow_workmode', False)
            # The task's native tool list must be gated by the SAME permission
            # its prompt is gated by (build_task_system_prompt passes this into
            # get_system_prompt) — otherwise the tasker is taught tools it was
            # never handed, or handed tools it was never taught.
            self._engine.include_image_tools = perms.get('inject_image_tools', False)
            # (allow_execute_code was retired with the execute_code tool; old
            # task dicts may still carry the key — it is simply ignored.)
            self._apply_supervision(perms)
            self._apply_background_policy(perms)
            # Same guarantee the main session gets: the interpreter re-binds the
            # task's helpers itself on ANY reset, including the one execute()
            # performs when it abandons a stuck thread. Without this, a task that
            # hit a hard cancel stayed broken until its NEXT ping re-injected.
            self._engine.tool_manager._namespace_injector = self._inject_task_namespace
            self._inject_task_namespace()
            log.info("[TaskAIEngine._init_engine] ✓ Dedicated AIEngine instance created")
        except Exception as e:
            log.error(f"[TaskAIEngine._init_engine] ✗ {type(e).__name__}: {e}")
            self._engine = None

    def _apply_supervision(self, perms: dict | None = None):
        """Set the task engine's supervision policy from the task's permissions.

        A background task NEVER prompts (no UI to answer a dialog). Its security
        is decided entirely by the per-task ``task_security_policy`` grid applied
        in ``_apply_background_policy`` — so here we simply pin the tool_manager
        into a non-prompting, non-bypassing state and let the policy be the gate.

        (Legacy ``bypass_supervised`` tasks were migrated to an all-'allow' policy
        on save; the flag itself is no longer consulted at runtime.)"""
        tm = self._engine.tool_manager
        tm.bypass_security = False
        # False = never open a supervised-execution dialog. The background_policy
        # is the sole authority on what runs.
        tm.supervised_execution = False

    def _apply_background_policy(self, perms: dict | None = None):
        """Hand the task's per-category allow/deny policy to the tool_manager as a
        ``background_policy`` override. When set, every code/file op is decided
        purely from it — deny -> block with an actionable observation (no dialog),
        allow -> run. A denied op also alerts the user's main chat.

        Master toggle: ``allow_workmode`` off means the Python interpreter tool is
        not offered at all (handled where tools are assembled), so the policy only
        matters for tasks that CAN run code."""
        if perms is None:
            perms = self._task.get('permissions', {})
        policy = perms.get('task_security_policy')
        tm = self._engine.tool_manager
        if isinstance(policy, dict) and policy:
            # Normalise to {category: 'allow'|'deny'}; ignore stray values.
            tm.background_policy = {str(k): v for k, v in policy.items()
                                    if v in ('allow', 'deny')}
        else:
            # No explicit policy (old task with no migration yet, or a bare dict):
            # legacy bypass -> allow all; otherwise deny all (safe default — the
            # historical non-bypass task only ever ran finding-free code anyway).
            allow_all = bool(perms.get('bypass_supervised', False))
            tm.background_policy = {'*': 'allow' if allow_all else 'deny'}
        # Give the tool_manager a way to alert the main chat when it blocks an op.
        tm.background_alert = self._alert_main_blocked

    def _alert_main_blocked(self, category: str, detail: str = ""):
        """Push a note to the user's main chat that this task hit its security
        policy and blocked an op. Invoked by the tool_manager's background gate.
        Best-effort: a missing delivery channel is silently non-fatal."""
        try:
            cb = self._send_main_callback
            if cb is None:
                return
            name = self._task.get('name') or self._task.get('title') or 'A background task'
            extra = f" ({detail})" if detail else ""
            cb(f"[Task blocked] {name} tried an operation denied by its security "
               f"policy — {category} is set to deny{extra}. It was told to find "
               f"another approach.")
        except Exception as _e:
            log.error(f"[TaskAIEngine._alert_main_blocked] ✗ {_e}")

    def _inject_task_namespace(self):
        """(Re-)inject everything the task's Python tool needs. Idempotent and
        cheap — safe to call every ping. Covers explicit reinit after a
        run_full_ping timeout AND a silent PythonInterpreter.reset() from a
        killed code-execution timeout."""
        ns = self._engine.tool_manager.tools['python'].namespace
        _task_ai_ref = self

        # Task-specific bindings — the SAME capability names the main session
        # uses, implemented for this context: images queue into this task's own
        # _pending_context_images (not the main session's
        # bridge_attach_image_signal flow), and send_message_main is the task's
        # declared link back to the user.

        def _task_take_screenshot(save_path=None):
            import uuid
            try:
                from PIL import ImageGrab
                if save_path is None:
                    _temp_dir = _APP_ROOT / "data" / "temp"
                    _temp_dir.mkdir(parents=True, exist_ok=True)
                    save_path = str(_temp_dir / f"task_shot_{uuid.uuid4().hex[:12]}.png")
                ImageGrab.grab().save(save_path)
                _task_ai_ref._queue_image(save_path)
                return save_path
            except Exception:
                try:
                    import pyautogui, uuid
                    if save_path is None:
                        _temp_dir = _APP_ROOT / "data" / "temp"
                        _temp_dir.mkdir(parents=True, exist_ok=True)
                        save_path = str(_temp_dir / f"task_shot_{uuid.uuid4().hex[:12]}.png")
                    pyautogui.screenshot(save_path)
                    _task_ai_ref._queue_image(save_path)
                    return save_path
                except Exception as _e2:
                    return f"[take_screenshot ERROR] {_e2}"

        def _attach_image_to_context(path):
            import os
            if not os.path.isfile(path):
                return f"[attach_image_to_context] File not found: {path}"
            _task_ai_ref._queue_image(path)
            return f"[attach_image_to_context] Queued: {path}"

        # send_message_main — the ONE supported way for a task agent to message the
        # user's main chat. A plain namespace function so it works in BOTH tool
        # modes (the old "{"tool":"send_message_main",...}" JSON-in-text approach
        # breaks under native, where the model is told never to write tool calls as
        # text). Delivers immediately when called from python_interpreter.
        def _send_message_main(message):
            try:
                cb = _task_ai_ref._send_main_callback
                if cb is None:
                    return "[send_message_main] No delivery channel available."
                cb(str(message))
                return "[send_message_main] Delivered to the main chat."
            except Exception as _e:
                return f"[send_message_main ERROR] {_e}"

        # ── Build the namespace FROM the capability manifest ──────────────────
        # Same names as the main session, task-appropriate bindings, and gated by
        # the task's OWN permissions — which the prompt already honours. These
        # used to be hand-injected unconditionally, so a task whose prompt hid
        # notify/memory still had them sitting in its namespace.
        from systema.execution import capabilities as caps
        perms = self._task.get('permissions', {})
        gates = caps.gates_for_task(
            perms,
            # TaskAIEngine has no skill manager of its own — skills belong to
            # the owning TaskThread / controller.
            has_skills=bool(getattr(self._engine, 'skill_manager', None)),
            has_main_channel=self._send_main_callback is not None,
        )
        # attach_image_to_chat: a background task has no chat turn of its own, so
        # its images land in the user's MAIN chat (declared divergence — see the
        # manifest note). Both surfaces route through this one binding.
        if gates.get(caps.G_IMAGES):
            self._engine.tool_manager._attach_image_binding = \
                self._controller._agent_attach_image
        bindings = {
            'controller': self._controller,
            'notify': getattr(self._controller, 'notify', None),
            'memorize': self._controller.memorize,
            'search_memory': self._controller.search_memory,
            'update_memory': self._controller.update_memory,
            'forget_memory': self._controller.forget_memory,
            'app_root': str(_APP_ROOT),
            'skills_path': str(_APP_ROOT / "skills"),
            'take_screenshot': _task_take_screenshot,
            'attach_image_to_context': _attach_image_to_context,
            'attach_image_to_chat': self._engine.tool_manager.interp_attach_image_to_chat,
            'web_search': self._engine.tool_manager.interp_web_search,
            'send_message_main': _send_message_main,
        }
        ns.update(caps.build_namespace(caps.TASK, gates, bindings))

        # A tasker's prompt gates ARE its injection gates (both come from the
        # task's permissions), so the work-mode ping can reuse them directly —
        # it will describe task bindings with task wording and never name a
        # chat-only capability.
        tm = self._engine.tool_manager
        tm.prompt_context = caps.TASK
        tm.documented_gates = gates

    def _queue_image(self, path: str):
        with self._images_lock:
            self._pending_context_images.append(path)

    def _drain_images(self) -> list:
        with self._images_lock:
            pending = list(self._pending_context_images)
            self._pending_context_images.clear()
        return pending

    def _sync_settings(self):
        """Copy only what AIEngine actually reads. If you add a setting AIEngine
        dispatches on, add it here too, and nowhere else."""
        if self._engine is None:
            self._init_engine()
        if self._engine is None:
            return
        s = self._controller.settings
        self._engine.custom_script_path = s.get('custom_script_path', '')
        self._engine.ai_provider = s.get('ai_provider', 'custom_script')

    def run_full_ping(self, ping_content: str, history: list, task_system_prompt: str,
                      max_iterations: int = 20, on_step=None) -> tuple:
        """
        Run a full ping using the same pipeline as the main session:
        generate_response + continue_work loop.
        Supports python_interpreter, memorize, load_skill, etc.

        Returns:
            (response_text: str | None, send_main_calls: list[str], updated_history: list)
        """
        self._sync_settings()
        if self._engine is None:
            log.error("[TaskAIEngine.run_full_ping] No engine available")
            return None, [], history

        # Re-inject namespace (covers silent PythonInterpreter.reset() from a
        # killed timeout, and any other path that wiped the namespace since
        # the last ping or _init_engine call).
        self._inject_task_namespace()

        # Inject task system prompt (includes full user system prompt + task context)
        self._engine.system_prompt_hijacked = True
        self._engine.custom_system_prompt = task_system_prompt
        # Ensure the task's supervision policy persists after sync
        self._apply_supervision()

        # Restore session history into the engine. StampedHistory, same as the
        # main session: a task agent's own session file gets the same log
        # cross-referencing, since a background task failing at 3am is exactly
        # the case where you cannot just remember what happened.
        # The constructor never re-stamps, so restored entries keep the run they
        # were actually written during.
        from systema.common.run_context import StampedHistory
        self._engine.conversation_history = StampedHistory(history)

        import time
        all_responses = []
        send_main_calls = []

        def _emit_step():
            """Notify the caller so it can persist the session live for the viewer."""
            if on_step:
                try:
                    on_step(list(self._engine.conversation_history))
                except Exception as e:
                    log.error(f"[TaskAIEngine.run_full_ping] on_step error: {e}")

        def _record(res: dict):
            """Capture a step's response + any send_message_main call. Skips hard
            provider-error sentinels so a transient outage isn't surfaced as the
            agent's 'reply'."""
            resp = (res or {}).get('response') or ''
            if not resp or self._is_provider_error(res):
                return
            all_responses.append(resp)
            send_msg, _ = self.extract_send_message_main(resp)
            if send_msg:
                send_main_calls.append(send_msg)

        # First call — generate_response handles tool calls internally.
        # (Provider-level transient retries already happen inside _call_provider.)
        try:
            result = self._engine.generate_response(ping_content)
        except Exception as e:
            log.error(f"[TaskAIEngine.run_full_ping] generate_response failed: {e}")
            self._reset_work()
            return None, [], list(self._engine.conversation_history)
        _record(result)
        _emit_step()

        # Work mode continuation loop. A single failed step (provider error or
        # exception) no longer kills the ping — it's retried a few times; only
        # repeated back-to-back failures end the ping (with partial output kept).
        iteration = 0
        consecutive_failures = 0
        MAX_STEP_FAILURES = 2
        while result.get('thinking') and iteration < max_iterations:
            iteration += 1
            if iteration > 1:
                time.sleep(1.5)  # courtesy delay between provider calls

            step_failed = False
            step_result = None
            try:
                pending = self._drain_images()
                if pending:
                    from systema.common.data_paths import discard_temp_image
                    work_prompt = self._engine.tool_manager.get_work_prompt()
                    self._engine.conversation_history.append({'role': 'system', 'content': work_prompt})
                    ai_text = self._engine._call_provider(images=pending)
                    # Clean up only OUR OWN temp captures — attach_image_to_context()
                    # takes any path, and this used to delete the user's files.
                    for _p in pending:
                        discard_temp_image(_p)
                    if not ai_text:
                        step_failed = True
                    else:
                        step_result = self._engine._process_work_response(ai_text)
                else:
                    step_result = self._engine.continue_work()
                    if self._is_provider_error(step_result):
                        step_failed = True
            except Exception as e:
                log.error(f"[TaskAIEngine.run_full_ping] interpreter step failed: {type(e).__name__}: {e}")
                step_failed = True

            if step_failed:
                consecutive_failures += 1
                if consecutive_failures > MAX_STEP_FAILURES:
                    log.error(f"[TaskAIEngine.run_full_ping] {consecutive_failures} consecutive step "
                              f"failures — ending ping with partial output (iter {iteration})")
                    break
                log.warning(f"[TaskAIEngine.run_full_ping] interpreter step failed "
                            f"({consecutive_failures}/{MAX_STEP_FAILURES}) — retrying after backoff")
                time.sleep(3.0 * consecutive_failures)
                # `result` is left unchanged (still thinking=True) so the loop retries.
                continue

            consecutive_failures = 0
            result = step_result
            _record(result)
            _emit_step()

            if result.get('finished_working'):
                log.info(f"[TaskAIEngine.run_full_ping] Work mode exited after {iteration} iteration(s)")
                break

        if iteration >= max_iterations and self._engine.tool_manager.work.is_working:
            log.warning(f"[TaskAIEngine.run_full_ping] Hit max_iterations={max_iterations} without clean exit")

        # Always clear a dangling work-mode flag so a broken ping never poisons
        # the next one (covers max-iterations AND repeated-failure exits).
        self._reset_work()

        # Cleanup any leftover queued images that never got flushed (app-made
        # temp captures only — never a path the task AI was pointed at).
        from systema.common.data_paths import discard_temp_image
        for _p in self._drain_images():
            discard_temp_image(_p)

        final_text = '\n'.join(r for r in all_responses if r)
        return final_text or None, send_main_calls, list(self._engine.conversation_history)

    @staticmethod
    def _is_provider_error(result: dict) -> bool:
        """True when a step result is the 'no response from provider' sentinel
        (i.e. the provider failed even after retries), not a real agent reply."""
        resp = (result or {}).get('response') or ''
        return resp.startswith('Error: No response')

    def _reset_work(self):
        """Clear the engine's work-mode state, guarded."""
        try:
            if self._engine and self._engine.tool_manager.work.is_working:
                self._engine.tool_manager.work.reset()
        except Exception:
            pass

    def extract_send_message_main(self, text: str) -> tuple:
        """
        Pull the first send_message_main call out of AI response text.

        Returns:
            (message: str | None, cleaned_text: str)
        """
        match = self._SEND_MSG_RE.search(text)
        if not match:
            return None, text
        msg = match.group(1).replace('\\"', '"').replace('\\n', '\n')
        cleaned = self._SEND_MSG_RE.sub('', text).strip()
        return msg, cleaned


# ── Task Thread ───────────────────────────────────────────────────────────────

class TaskThread(threading.Thread):

    """One daemon thread per task — runs the scheduled ping loop independently."""

    def __init__(self, task: dict, controller, skill_manager, send_main_callback, set_inactive_callback=None, task_manager=None):
        super().__init__(daemon=True, name=f"Task-{task['id'][:8]}")
        self._task = task
        self._controller = controller
        self._settings = lambda: controller.settings   # kept for any internal use
        self._skill_manager = skill_manager
        self._send_main = send_main_callback   # fn(str) → appends to main session
        self._set_inactive = set_inactive_callback   # fn(task_id) → marks task inactive after one-shot
        self._task_manager = task_manager   # owning TaskManager — used to flag "ping active" for the UI title dot
        self._stop_event = threading.Event()
        self._ping_lock = threading.Lock()   # serializes scheduled + manual pings per task
        # Pass _safe_send_main so the agent's send_message_main() namespace function
        # can deliver to the main chat from inside its Python execution.
        self._ai = TaskAIEngine(controller, task, send_main_callback=self._safe_send_main)
        self._sessions_root = _APP_ROOT / "data" / "task-sessions"
        self._sessions_root.mkdir(parents=True, exist_ok=True)
        log.info(f"[TaskThread.__init__] Ready | task='{task['name']}' id={task['id'][:8]}")

    def stop(self):
        self._stop_event.set()

    # ── Session I/O ───────────────────────────────────────────────────────────

    def _session_path(self, task_id: str, date_str: str) -> Path:
        folder = self._sessions_root / task_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{date_str}.json"

    def _load_session(self, task_id: str, date_str: str) -> dict | None:
        p = self._session_path(task_id, date_str)
        if not p.exists():
            return None
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log.error(f"[TaskThread._load_session] {e}")
            return None

    def _save_session(self, session: dict, task_id: str, date_str: str):
        p = self._session_path(task_id, date_str)
        try:
            tmp = p.with_suffix('.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(session, f, indent=2, ensure_ascii=False)
            tmp.replace(p)
        except Exception as e:
            log.error(f"[TaskThread._save_session] {e}")

    def _new_session(self) -> dict:
        task = self._task
        now = datetime.now()
        session_id = now.strftime("%m_%d_%Y_%H_%M_%S_") + str(now.microsecond)[:2]
        return {
            "session_name": f"task-session-{task['name']}",
            "creation_time_and_date": _now_stamp(),
            "id": session_id,
            "task_id": task['id'],
            "date": date.today().isoformat(),
            "ended_at": None,
            "chat_history": []
        }

    # ── System prompt ─────────────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Delegate to the centralized controller builder — single source of truth."""
        return self._controller.build_task_system_prompt(self._task)

    # ── Schedule helpers ──────────────────────────────────────────────────────

    def _window_times(self):
        """(start_time, end_time) from the schedule, or None when whole-day.

        Never raises: an unparseable schedule is treated as whole-day, which is
        the permissive answer — a task that cannot read its window should keep
        running, not go silent.
        """
        sched = self._task.get('daily_schedule', {})
        if sched.get('whole_day', False):
            return None
        try:
            start = datetime.strptime(sched.get('start', '00:00'), '%H:%M').time()
            end = datetime.strptime(sched.get('end', '23:59'), '%H:%M').time()
            return start, end
        except Exception:
            return None

    def _window_wraps(self) -> bool:
        """True when the window crosses midnight (22:00 -> 01:00)."""
        times = self._window_times()
        return bool(times and times[0] > times[1])

    def _in_window(self, now: datetime | None = None) -> bool:
        """Is `now` inside the daily schedule window?

        A window whose end is EARLIER than its start crosses midnight, and the
        obvious `start <= now <= end` cannot express that: for 22:00 -> 01:00 it
        is false at 23:00, because 23:00 <= 01:00 is false. A sleep reminder set
        for 22:00-01:00 is exactly the shape that wraps, so this is the case
        most likely to be configured and the one that silently never ran.
        """
        times = self._window_times()
        if times is None:
            return True
        start, end = times
        current = (now or datetime.now()).time()
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end

    def _window_started_at(self, now: datetime | None = None) -> datetime | None:
        """The datetime the CURRENTLY OPEN window began.

        For a wrapping window in its after-midnight half, that is YESTERDAY's
        start — which is the whole fix for pings dying at midnight. Returns
        None when the window has not opened yet today.
        """
        times = self._window_times()
        now = now or datetime.now()
        if times is None:
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        start, _end = times
        start_today = now.replace(hour=start.hour, minute=start.minute,
                                  second=0, microsecond=0)
        if start_today <= now:
            return start_today
        # start is later today. If we are inside a wrapping window right now,
        # this is its tail and it opened yesterday.
        if self._window_wraps() and self._in_window(now):
            return start_today - timedelta(days=1)
        return None

    def _wait_seconds_until_start(self) -> float:
        sched = self._task.get('daily_schedule', {})
        if sched.get('whole_day', False):
            return 0
        try:
            now = datetime.now()
            start = datetime.strptime(sched.get('start', '00:00'), '%H:%M')
            target = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
            if target <= now:
                # +1 day via timedelta — `replace(day=day+1)` overflows at month end.
                target = target + timedelta(days=1)
            return (target - now).total_seconds()
        except Exception:
            return 0

    # ── History builder ───────────────────────────────────────────────────────

    def _build_api_history(self, session: dict) -> list:
        task = self._task
        limit_cfg = task.get('limit_session_messages', {})
        history = [m for m in session.get('chat_history', []) if m.get('role') in ('system', 'assistant')]
        if limit_cfg.get('enabled', False):
            max_n = max(1, int(limit_cfg.get('max_messages', 5)))
            history = history[-max_n:]
        return history

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _next_specific_ping_seconds(self) -> float | None:
        """
        For specific ping times mode: returns seconds until the next
        scheduled ping time today that hasn't passed yet.
        Returns None if all scheduled pings for today have already passed.
        """
        ping_times = self._task.get('specific_ping_times', [])
        if not ping_times:
            return None
        now = datetime.now()
        future_pings = []
        for t_str in ping_times:
            try:
                h, m = map(int, t_str.split(':'))
                ping_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if ping_dt > now:
                    future_pings.append(ping_dt)
            except Exception:
                pass
        if not future_pings:
            return None
        return (min(future_pings) - now).total_seconds()

    def _next_schedule_relative_seconds(self) -> float:
        """
        Returns seconds until the next interval-aligned ping, anchored to the
        schedule window's start time (or midnight for whole-day tasks).

        Example: window 04:00–19:00, interval 120 min, current time 16:31
          elapsed since 04:00 = 751 min
          next slot = floor(751/120)+1 = 7 → 7×120 = 840 min after 04:00 = 18:00
          wait = 18:00 − 16:31 = 89 min = 5340 s
        """
        task = self._task
        interval_sec = max(1, task.get('interval_minutes', 30) * 60)
        now = datetime.now()

        # The anchor is when the OPEN window began, which for a wrapping window
        # after midnight is yesterday's start time. Anchoring to today's start
        # unconditionally is what killed cross-midnight tasks: at 00:30 with a
        # 22:00 start it measured to 22:00 TONIGHT and slept ~21 hours, so a
        # 22:00-01:00 reminder went quiet the moment the date rolled over — and
        # a restart appeared to fix it because that re-anchored the schedule.
        window_start = self._window_started_at(now)

        if window_start is None:
            # Window has not opened yet today — first ping fires at open.
            times = self._window_times()
            start = times[0] if times else None
            if start is None:
                return float(interval_sec)
            start_today = now.replace(hour=start.hour, minute=start.minute,
                                      second=0, microsecond=0)
            return max(0.0, (start_today - now).total_seconds())

        elapsed_sec = (now - window_start).total_seconds()
        slots_passed = math.floor(elapsed_sec / interval_sec)
        next_ping = window_start + timedelta(seconds=(slots_passed + 1) * interval_sec)
        return max(0.0, (next_ping - now).total_seconds())

    def _get_ping_interval_mode(self) -> str:
        """
        WHAT triggers a ping → 'timed' | 'specific_times' | 'script_trigger'.

        This is a different axis from _get_interval_anchor() (which only matters
        when this returns 'timed'). Reads the unambiguous 'ping_interval_mode'
        key first; falls back to the legacy boolean so old task files still work.
        """
        task = self._task
        mode = task.get('ping_interval_mode', '')
        if mode in ('timed', 'specific_times', 'script_trigger'):
            return mode
        if task.get('use_specific_ping_times', False):
            return 'specific_times'
        return 'timed'

    def _get_interval_anchor(self) -> str:
        """
        For 'timed' mode only: what the interval clock is anchored to →
        'schedule' (slots aligned to the window's start time) or
        'startup'  (fixed delay measured from when the task thread started).

        Distinct from _get_ping_interval_mode(); persisted as the legacy
        'ping_mode' key ('schedule_relative' / 'startup_relative').
        """
        return 'schedule' if self._task.get('ping_mode') == 'schedule_relative' else 'startup'

    # ── Ping assembly helpers ───────────────────────────────────────────────

    def _safe_send_main(self, message: str):
        """Dispatch a message to the main session, swallowing transport errors."""
        try:
            self._send_main(message)
        except Exception as e:
            log.error(f"[TaskThread._safe_send_main] {e}")

    def _make_ping_content(self, resolved_instruction: str) -> str:
        """Wrap a resolved instruction in the automated-task ping envelope."""
        return (
            f"<SYSTEM_AUTOMATED_TASK_PING>\nTHIS IS AN AUTOMATED "
            f"SYSTEM MESSAGE, YOUR TASK IS STATED BELOW:\n\n{resolved_instruction}\n{_now_stamp()}\n\n"
            f"</SYSTEM_AUTOMATED_TASK_PING>"
        )

    def _execute_ping(self, session: dict, task_id: str, today: str,
                      override_instruction: str | None = None) -> bool:
        """
        Single source of truth for running one task ping end-to-end:

            resolve {{code}} → build system prompt + history → wrap ping
            → run the AI (resilient work-mode loop) → dispatch send_message_main
            → persist the engine's new messages into the session.

        Used by ALL drivers (one-shot, timed loop, script trigger, manual ping)
        so the prompt/ping pipeline lives in exactly one place. The whole run is
        serialized behind ``_ping_lock`` so a manual ping can never overlap a
        scheduled one on the same task engine.

        ``override_instruction`` — when given, replaces the task's stored
        instruction for this single ping (used by the manual "⚡ Ping" tester).

        Returns True if the ping produced a response, False if it was skipped
        (code-block error) or produced nothing.
        """
        with self._ping_lock:
            if self._task_manager is not None:
                self._task_manager._ping_started()
            try:
                return self._execute_ping_locked(session, task_id, today, override_instruction)
            finally:
                if self._task_manager is not None:
                    self._task_manager._ping_finished()

    def _execute_ping_locked(self, session: dict, task_id: str, today: str,
                             override_instruction: str | None = None) -> bool:
        task = self._task

        # 1. Resolve {{code}} blocks in the instruction (shares the task's interp).
        instruction = override_instruction if override_instruction is not None else task.get('instruction', '')
        _fns = []
        try:
            _fns = self._controller.task_manager.get_functions()
        except Exception:
            pass
        resolved, error = _resolve_instruction(
            instruction,
            self._ai._engine.tool_manager.tools['python'],
            functions=_fns,
        )
        if error:
            log.error(f"[TaskThread._execute_ping] Code block error in '{task['name']}': {error[:200]}")
            self._safe_send_main(
                f"⚠️ **Task '{task['name']}' — code block error, ping skipped.**\n\n"
                f"```\n{error}\n```"
            )
            return False

        # 2. Rebuild prompt + history fresh each ping (picks up live user edits).
        system_prompt = self._build_system_prompt()
        api_history   = self._build_api_history(session)
        api_hist_len  = len(api_history)

        # 3. Record the ping, then run the AI — persisting the session after EVERY
        #    step so the viewer updates LIVE. `ongoing` drives the "Ongoing" label.
        ping_content = self._make_ping_content(resolved)
        pre_history  = list(session['chat_history'])
        ping_entry   = {'role': 'system', 'content': ping_content}

        def _rebuild(engine_hist, ongoing):
            tail = [dict(m) for m in engine_hist[api_hist_len:]
                    if m.get('role') in ('assistant', 'system')]
            session['chat_history'] = pre_history + [ping_entry] + tail
            session['ongoing'] = ongoing

        # Show the ping + "Ongoing" immediately (before the first provider call).
        _rebuild([], True)
        self._save_session(session, task_id, today)

        def _on_step(engine_hist):
            _rebuild(engine_hist, True)
            self._save_session(session, task_id, today)

        _unlimited = task.get('unlimited_work_iterations', False)
        _max_iter  = 999999 if _unlimited else task.get('max_work_iterations', 20)
        response_text, send_main_calls, engine_history = self._ai.run_full_ping(
            ping_content, api_history, system_prompt, max_iterations=_max_iter, on_step=_on_step
        )

        # 4. Dispatch any user-facing messages the agent emitted.
        for msg in send_main_calls:
            self._safe_send_main(msg)

        # 5. Final persist — no longer ongoing + stamp the last assistant's finish time.
        _rebuild(engine_history, False)
        for _i in range(len(session['chat_history']) - 1, -1, -1):
            if session['chat_history'][_i].get('role') == 'assistant':
                session['chat_history'][_i]['content'] += f"\n{_now_stamp()}"
                break
        self._save_session(session, task_id, today)
        log.info(f"[TaskThread._execute_ping] Ping complete for task='{task['name']}'")
        return response_text is not None

    def fire_manual_ping(self, custom_instruction: str | None = None) -> str:
        """
        Fire one immediate, unscheduled ping (the "⚡ Ping" tester).

        Runs in its own daemon thread so the caller (UI) never blocks. The actual
        run still goes through ``_execute_ping`` → ``_ping_lock``, so it queues
        safely behind any in-flight scheduled ping instead of clobbering it.

        Returns the date_str of the session it writes into, so the UI can open
        that session viewer to watch the live result.
        """
        today = date.today().isoformat()

        def _run():
            try:
                task_id = self._task['id']
                session = self._load_session(task_id, today) or self._new_session()
                self._save_session(session, task_id, today)   # ensure the file exists for the viewer
                self._execute_ping(session, task_id, today,
                                   override_instruction=custom_instruction)
            except Exception as e:
                log.error(f"[TaskThread.fire_manual_ping] {e}", exc_info=True)
                self._safe_send_main(
                    f"⚠️ **Task '{self._task['name']}' — manual ping failed.**\n\n```\n{e}\n```"
                )

        threading.Thread(target=_run, daemon=True,
                         name=f"ManualPing-{self._task['id'][:8]}").start()
        return today

    def _run_script_trigger_loop(self):
        """
        Script Trigger mode main loop.

        Polls fire_ping() in a sub-thread every script_poll_ms milliseconds.
        Fires the full AI ping when fire_ping() returns True.

        Two guards:
          _script_busy — only one fire_ping() call runs at a time.
                         If the previous call is still hanging, that poll tick
                         is skipped entirely. Prevents thread pile-up.
          _ping_busy   — only one AI ping runs at a time. Any True signals that
                         arrive while a ping is in progress are discarded.
        """
        task        = self._task
        task_id     = task['id']
        script_name = task.get('script_name', '')
        poll_ms     = max(100, int(task.get('script_poll_ms', 1000)))
        poll_sec    = poll_ms / 1000.0
        script_path = _APP_ROOT / "data" / "tasks" / "interval-scripts" / script_name

        if not script_name or not script_path.exists():
            log.error(
                f"[TaskThread._run_script_trigger_loop] Script not found: "
                f"'{script_name}' — task '{task['name']}' will not poll."
            )
            self._safe_send_main(
                f"⚠️ **Task '{task['name']}' — trigger script not found.**\n\n"
                f"Expected `{script_name or '(none set)'}` in the interval-scripts folder. "
                f"This task will not poll until the script exists. Edit the task to pick a valid script."
            )
            return

        log.info(
            f"[TaskThread._run_script_trigger_loop] Starting | "
            f"task='{task['name']}' script='{script_name}' poll={poll_ms}ms"
        )

        # _script_busy — only one fire_ping() sub-thread runs at a time (no pile-up).
        # _ping_busy   — only one AI ping at a time; triggers arriving mid-ping are
        #                discarded so a fast-firing script can't queue a backlog.
        #                (Mutual exclusion against MANUAL pings is guaranteed
        #                separately by self._ping_lock inside _execute_ping.)
        _script_busy   = threading.Event()
        _ping_busy     = threading.Event()
        _trigger_lock  = threading.Lock()
        _trigger_ready = [False]             # written by script thread, read by main loop
        _err_state     = {'last': None}      # last surfaced script error (for throttling)

        def _report_script_error(msg: str):
            """Push a script error to the main chat — but only once per distinct
            error, so a persistently broken script doesn't spam every poll tick.
            Re-reports if the error text changes (e.g. after the user edits it)."""
            if msg == _err_state['last']:
                return
            _err_state['last'] = msg
            log.error(f"[TaskThread._run_script_trigger_loop] {msg}")
            self._safe_send_main(
                f"⚠️ **Task '{task['name']}' — trigger script error**  (`{script_name}`)\n\n"
                f"```\n{msg}\n```\n_Polling continues; fix the script and this will clear._"
            )

        def _clear_script_error():
            if _err_state['last'] is not None:
                _err_state['last'] = None
                self._safe_send_main(
                    f"✅ **Task '{task['name']}' — trigger script recovered** (`{script_name}`)."
                )

        def _call_script():
            """Execute fire_ping() once; posts True to _trigger_ready if fired."""
            import traceback as _tb
            try:
                code_src = script_path.read_text(encoding='utf-8')
                ns: dict = {}
                exec(compile(code_src, str(script_path), 'exec'), ns)
                fn = ns.get('fire_ping')
                if callable(fn):
                    fired = fn() is True
                    _clear_script_error()   # ran cleanly
                    if fired:
                        with _trigger_lock:
                            _trigger_ready[0] = True
                        log.debug(
                            f"[TaskThread._run_script_trigger_loop] "
                            f"fire_ping() → True in '{script_name}'"
                        )
                else:
                    _report_script_error(
                        f"Script '{script_name}' defines no callable fire_ping(). "
                        f"Expected:  def fire_ping() -> bool"
                    )
            except Exception:
                _report_script_error(_tb.format_exc().strip())
            finally:
                _script_busy.clear()

        while not self._stop_event.is_set():

            # ── Window gate ──────────────────────────────────────────────────
            if not self._in_window():
                wait = self._wait_seconds_until_start()
                log.info(
                    f"[TaskThread._run_script_trigger_loop] Outside window — "
                    f"sleeping {wait:.0f}s"
                )
                self._stop_event.wait(wait)
                if self._stop_event.is_set():
                    break
                if not self._in_window():
                    continue

            # ── Launch script check (skip if previous call still running) ────
            if not _script_busy.is_set():
                _script_busy.set()
                threading.Thread(
                    target=_call_script,
                    daemon=True,
                    name=f"ScriptCheck-{task_id[:8]}",
                ).start()

            # ── Check for pending trigger (skip if ping already running) ─────
            triggered = False
            with _trigger_lock:
                if _trigger_ready[0] and not _ping_busy.is_set():
                    triggered = True
                    _trigger_ready[0] = False   # consume the signal

            if triggered:
                log.info(
                    f"[TaskThread._run_script_trigger_loop] Trigger received — "
                    f"firing ping for task '{task['name']}'"
                )
                _ping_busy.set()
                try:
                    today   = date.today().isoformat()
                    session = self._load_session(task_id, today)
                    if session is None:
                        session = self._new_session()
                        self._save_session(session, task_id, today)

                    self._execute_ping(session, task_id, today)
                except Exception as _e:
                    log.error(
                        f"[TaskThread._run_script_trigger_loop] Ping error: "
                        f"{type(_e).__name__}: {_e}"
                    )
                finally:
                    _ping_busy.clear()

            # ── Sleep until next poll tick ───────────────────────────────────
            self._stop_event.wait(poll_sec)

        log.info(
            f"[TaskThread._run_script_trigger_loop] Loop exited for "
            f"task='{task['name']}'"
        )

    def run(self):
        task = self._task
        task_id = task['id']
        interval_sec = task.get('interval_minutes', 30) * 60
        log.info(f"[TaskThread.run] Starting | task='{task['name']}' interval={interval_sec}s")

        # ── One-shot task handling ────────────────────────────────────────────
        one_time = task.get('one_time_schedule', {})
        if one_time.get('enabled'):
            datetimes = sorted(one_time.get('datetimes', []))
            if not datetimes:
                log.warning(f"[TaskThread.run] One-shot: no datetimes configured for '{task['name']}' — marking inactive")
                if self._set_inactive:
                    self._set_inactive(task_id)
                return

            # Find the earliest datetime that hasn't passed yet
            now = datetime.now()
            target_dt = None
            for dt_str in datetimes:
                try:
                    candidate = datetime.strptime(dt_str, '%Y-%m-%dT%H:%M')
                    if candidate > now:
                        target_dt = candidate
                        break
                    else:
                        log.info(f"[TaskThread.run] One-shot: '{dt_str}' already passed — trying next backup")
                except Exception:
                    log.warning(f"[TaskThread.run] One-shot: invalid datetime '{dt_str}' — skipping")

            if target_dt is None:
                log.info(f"[TaskThread.run] One-shot: all times passed for '{task['name']}' — marking inactive")
                if self._set_inactive:
                    self._set_inactive(task_id)
                return

            wait_sec = (target_dt - now).total_seconds()
            target_str = target_dt.strftime('%Y-%m-%dT%H:%M')
            log.info(f"[TaskThread.run] One-shot: waiting {wait_sec:.0f}s until '{target_str}'")
            self._stop_event.wait(wait_sec)
            if self._stop_event.is_set():
                return

            # ── Fire the ping (unified pipeline) ──────────────────────────────
            today = date.today().isoformat()
            session = self._load_session(task_id, today) or self._new_session()
            self._save_session(session, task_id, today)
            self._execute_ping(session, task_id, today)
            log.info(f"[TaskThread.run] One-shot ping complete for task='{task['name']}'")
            # ── AI is fully done — NOW mark inactive ──────────────────────────
            if self._set_inactive:
                self._set_inactive(task_id)
            return

        # ── Script Trigger mode ───────────────────────────────────────────────
        if self._get_ping_interval_mode() == 'script_trigger':
            self._run_script_trigger_loop()
            return

        while not self._stop_event.is_set():

            # ── Wait until inside schedule window ────────────────────────────
            if not self._in_window():
                wait = self._wait_seconds_until_start()
                log.info(f"[TaskThread.run] Outside window — sleeping {wait:.0f}s")
                self._stop_event.wait(wait)
                if self._stop_event.is_set():
                    break
                if not self._in_window():
                    continue

            today = date.today().isoformat()

            # ── Load or create today's session ───────────────────────────────
            session = self._load_session(task_id, today)
            if session is None:
                session = self._new_session()
                self._save_session(session, task_id, today)
                log.info(f"[TaskThread.run] New session for task='{task['name']}' date={today}")

            # ── Wait for the interval or next specific ping time ─────────────
            if self._get_ping_interval_mode() == 'specific_times':
                wait_sec = self._next_specific_ping_seconds()
                if wait_sec is None:
                    log.info(f"[TaskThread.run] No more specific pings today — waiting for next window")
                    session['ended_at'] = _now_stamp()
                    self._save_session(session, task_id, today)
                    sleep_secs = self._wait_seconds_until_start() or 86400
                    self._stop_event.wait(sleep_secs)
                    continue
                log.info(f"[TaskThread.run] Next specific ping in {wait_sec:.0f}s")
                self._stop_event.wait(wait_sec)
            else:
                if self._get_interval_anchor() == 'schedule':
                    wait_sec = self._next_schedule_relative_seconds()
                    log.info(f"[TaskThread.run] Schedule-relative: next ping in {wait_sec:.0f}s")
                else:
                    wait_sec = interval_sec
                    log.info(f"[TaskThread.run] Startup-relative: waiting {interval_sec}s before next ping")
                self._stop_event.wait(wait_sec)
            if self._stop_event.is_set():
                break

            # ── Check if window closed during wait ───────────────────────────
            if not self._in_window():
                session['ended_at'] = _now_stamp()
                self._save_session(session, task_id, today)
                log.info(f"[TaskThread.run] Window closed — ending session")
                wait = self._wait_seconds_until_start()
                self._stop_event.wait(wait)
                continue

            # ── If date rolled over, start fresh next iteration ──────────────
            # A WRAPPING window (22:00 -> 01:00) is still open across midnight,
            # so it must not be treated as "the day ended". Only roll the
            # session over for ordinary same-day windows.
            if date.today().isoformat() != today and not self._window_wraps():
                continue

            # ── Run the ping through the unified pipeline ─────────────────────
            self._execute_ping(session, task_id, today)
        log.info(f"[TaskThread.run] Thread exiting for task='{task['name']}'")


# ── Task Manager ──────────────────────────────────────────────────────────────

class TaskManager:
    """
    Loads/saves tasks.json and owns one TaskThread per task.
    Call start() once on app launch.
    """

    TASKS_FILE = _APP_ROOT / "data" / "tasks" / "tasks.json"
    FUNCTIONS_FILE = _APP_ROOT / "data" / "tasks" / "functions.json"
    SCRIPTS_DIR = _APP_ROOT / "data" / "tasks" / "interval-scripts"

    def __init__(self, controller):
        self._controller = controller
        self._tasks: list = []
        self._threads: dict = {}           # task_id → TaskThread
        self._active_ping_count = 0        # tasks currently inside _execute_ping (any_ping_active)
        self._active_ping_count_lock = threading.Lock()
        self.TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_script_template()
        self._load_tasks()
        self._functions: list = []
        self._load_functions()
        log.info(f"[TaskManager.__init__] Ready | {len(self._tasks)} task(s)")

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_tasks(self):
        if not self.TASKS_FILE.exists():
            self._tasks = []
            return
        try:
            with open(self.TASKS_FILE, 'r', encoding='utf-8') as f:
                self._tasks = json.load(f)
        except Exception as e:
            log.error(f"[TaskManager._load_tasks] ✗ {e}")
            self._tasks = []

    def _save_tasks(self):
        try:
            tmp = self.TASKS_FILE.with_suffix('.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._tasks, f, indent=2, ensure_ascii=False)
            tmp.replace(self.TASKS_FILE)
        except Exception as e:
            log.error(f"[TaskManager._save_tasks] ✗ {e}")

    # ── Functions Library ─────────────────────────────────────────────────────

    def _load_functions(self):
        if not self.FUNCTIONS_FILE.exists():
            self._functions = []
            return
        try:
            with open(self.FUNCTIONS_FILE, 'r', encoding='utf-8') as f:
                self._functions = json.load(f)
        except Exception as e:
            log.error(f"[TaskManager._load_functions] ✗ {e}")
            self._functions = []

    def _save_functions(self):
        try:
            self.FUNCTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.FUNCTIONS_FILE.with_suffix('.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._functions, f, indent=2, ensure_ascii=False)
            tmp.replace(self.FUNCTIONS_FILE)
        except Exception as e:
            log.error(f"[TaskManager._save_functions] ✗ {e}")

    def get_functions(self) -> list:
        return list(self._functions)

    def save_function(self, name: str, code: str) -> bool:
        name = name.strip()
        code = code.strip()
        if not name or not code:
            return False
        for fn in self._functions:
            if fn['name'] == name:
                fn['code'] = code
                self._save_functions()
                return True
        self._functions.append({'name': name, 'code': code})
        self._save_functions()
        return True

    def delete_function(self, name: str) -> bool:
        before = len(self._functions)
        self._functions = [f for f in self._functions if f['name'] != name]
        if len(self._functions) < before:
            self._save_functions()
            return True
        return False

    # ── Script Trigger Library ────────────────────────────────────────────────

    def _ensure_script_template(self):
        """Create _template.py in interval-scripts if it does not exist."""
        tpl = self.SCRIPTS_DIR / "_template.py"
        if not tpl.exists():
            tpl.write_text(
                '"""\n'
                'Script Trigger Template\n'
                '────────────────────────────────────────────────────────────────\n'
                'Contract: define fire_ping() → return True to fire the AI ping,\n'
                '          return False to skip this poll cycle.\n'
                '\n'
                'The poller calls fire_ping() every N milliseconds (your Poll Rate).\n'
                'Once True fires and the ping completes, polling resumes immediately.\n'
                'It is YOUR responsibility to reset the condition back to False so\n'
                'the ping does not keep firing on every subsequent poll cycle.\n'
                '────────────────────────────────────────────────────────────────\n'
                '"""\n\n\n'
                'def fire_ping() -> bool:\n'
                '    """\n'
                '    Return True  → ping fires immediately.\n'
                '    Return False → sleep for Poll Rate ms and check again.\n'
                '    """\n'
                '    # ── Example: fire once when a sentinel file appears ──────\n'
                '    # from pathlib import Path\n'
                '    # flag = Path("C:/trigger.flag")\n'
                '    # if flag.exists():\n'
                '    #     flag.unlink()   # ← delete it so it won\'t re-fire\n'
                '    #     return True\n'
                '    # return False\n\n'
                '    return False   # ← replace with your condition\n',
                encoding='utf-8',
            )
            log.info("[TaskManager._ensure_script_template] Created _template.py")

    def get_script_names(self) -> list:
        """Return sorted list of .py filenames in the interval-scripts folder."""
        try:
            return sorted(p.name for p in self.SCRIPTS_DIR.glob("*.py"))
        except Exception as e:
            log.error(f"[TaskManager.get_script_names] ✗ {e}")
            return []

    def open_scripts_folder(self):
        """Open the interval-scripts folder in the OS file manager."""
        import subprocess, sys as _sys
        try:
            folder = str(self.SCRIPTS_DIR.resolve())
            if _sys.platform == 'win32':
                subprocess.Popen(['explorer', folder])
            elif _sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
        except Exception as e:
            log.error(f"[TaskManager.open_scripts_folder] ✗ {e}")

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def get_tasks(self) -> list:
        return list(self._tasks)

    def add_task(self, task_dict: dict) -> dict:
        task_dict['id'] = str(uuid.uuid4())
        task_dict['created_at'] = datetime.now().isoformat()
        self._tasks.append(task_dict)
        self._save_tasks()
        self._start_thread(task_dict)
        log.info(f"[TaskManager.add_task] ✓ '{task_dict['name']}'")
        return task_dict

    def update_task(self, task_id: str, updated: dict) -> bool:
        for i, t in enumerate(self._tasks):
            if t['id'] == task_id:
                updated['id'] = task_id
                updated['created_at'] = t.get('created_at', datetime.now().isoformat())
                self._tasks[i] = updated
                self._save_tasks()
                self._stop_thread(task_id)
                self._start_thread(updated)
                log.info(f"[TaskManager.update_task] ✓ '{updated['name']}'")
                return True
        return False

    def delete_task(self, task_id: str) -> bool:
        self._stop_thread(task_id)
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t['id'] != task_id]
        if len(self._tasks) < before:
            self._save_tasks()
            return True
        return False

    # ── Thread management ─────────────────────────────────────────────────────

    def set_task_inactive(self, task_id: str):
        """Mark a task as inactive in storage. Called by TaskThread after a one-shot fires."""
        for task in self._tasks:
            if task['id'] == task_id:
                task['active'] = False
                self._save_tasks()
                log.info(f"[TaskManager.set_task_inactive] Task '{task['name']}' set to inactive")
                return

    def _start_thread(self, task: dict):
        if not task.get('active', True):
            log.info(f"[TaskManager._start_thread] Skipping inactive task '{task['name']}'")
            return
        t = TaskThread(
            task=task,
            controller=self._controller,
            skill_manager=getattr(self._controller, 'skill_manager', None),
            send_main_callback=self._send_to_main,
            set_inactive_callback=self.set_task_inactive,
            task_manager=self,
        )
        t.start()
        self._threads[task['id']] = t

    def _stop_thread(self, task_id: str):
        t = self._threads.pop(task_id, None)
        if t:
            t.stop()

    def fire_manual_ping(self, task_id: str, instruction: str | None = None) -> str | None:
        """
        Fire an immediate, unscheduled ping for a task — the "⚡ Ping" tester.

        Reuses the task's running thread when active (so it shares the same
        engine/session state); for an inactive task it spins up a transient,
        un-started thread just to run the one ping. Returns the session date_str
        the ping writes into (so the UI can open that viewer), or None on failure.
        """
        thread = self._threads.get(task_id)
        if thread is None:
            task = next((t for t in self._tasks if t['id'] == task_id), None)
            if task is None:
                log.error(f"[TaskManager.fire_manual_ping] Unknown task id={task_id}")
                return None
            # Transient thread: never .start()-ed, only used to drive one ping.
            thread = TaskThread(
                task=task,
                controller=self._controller,
                skill_manager=getattr(self._controller, 'skill_manager', None),
                task_manager=self,
                send_main_callback=self._send_to_main,
                set_inactive_callback=self.set_task_inactive,
            )
        log.info(f"[TaskManager.fire_manual_ping] Manual ping requested for task id={task_id[:8]}")
        return thread.fire_manual_ping(instruction)

    def start(self):
        """Start all task threads. Call once after controller is ready."""
        for task in self._tasks:
            self._start_thread(task)
        log.info(f"[TaskManager.start] ✓ {len(self._tasks)} thread(s) started")

    def stop(self):
        for task_id in list(self._threads.keys()):
            self._stop_thread(task_id)

    # ── Ping-active state (drives the Manage Tasks window title dot) ──────────

    def _ping_started(self):
        with self._active_ping_count_lock:
            self._active_ping_count += 1

    def _ping_finished(self):
        with self._active_ping_count_lock:
            self._active_ping_count = max(0, self._active_ping_count - 1)

    def any_ping_active(self) -> bool:
        """True if any task thread is currently inside a ping right now."""
        with self._active_ping_count_lock:
            return self._active_ping_count > 0

    # ── Session file API (used by the UI window) ──────────────────────────────

    def get_task_sessions(self, task_id: str) -> list:
        """Returns list of date strings (YYYY-MM-DD) that have saved sessions."""
        folder = _APP_ROOT / "data" / "task-sessions" / task_id
        if not folder.exists():
            return []
        return sorted([p.stem for p in folder.glob("*.json")], reverse=True)

    def load_task_session(self, task_id: str, date_str: str) -> dict | None:
        p = _APP_ROOT / "data" / "task-sessions" / task_id / f"{date_str}.json"
        if not p.exists():
            return None
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def delete_task_session(self, task_id: str, date_str: str) -> bool:
        p = _APP_ROOT / "data" / "task-sessions" / task_id / f"{date_str}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    def delete_all_task_sessions(self, task_id: str):
        folder = _APP_ROOT / "data" / "task-sessions" / task_id
        if folder.exists():
            for f in folder.glob("*.json"):
                f.unlink()

    # ── Bridge to main session ────────────────────────────────────────────────

    def _send_to_main(self, message: str):
        """
        Injects a message into the main conversation as an assistant turn.
        Emits task_message_signal so the chat window renders it on the main thread.
        Called from background TaskThread — the signal hop makes it Qt-safe.
        """
        try:
            ctrl = self._controller
            stamped = f"[Task Notification]: {message}"
            ctrl.ai.conversation_history.append({'role': 'assistant', 'content': stamped})
            if hasattr(ctrl, '_auto_save_session'):
                ctrl._auto_save_session()
            if hasattr(ctrl, 'task_message_signal'):
                ctrl.task_message_signal.emit(message)
            log.info(f"[TaskManager._send_to_main] ✓ | preview='{message[:60]}'")
        except Exception as e:
            log.error(f"[TaskManager._send_to_main] ✗ {e}")

