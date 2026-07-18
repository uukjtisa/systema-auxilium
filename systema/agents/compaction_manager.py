"""
systema/agents/compaction_manager.py

CompactionManager — runs per-session '[Compacted]' summarization jobs in the
background. A job SURVIVES session switches: while its session is the CURRENT
(loaded) one it rewrites the live conversation_history (the card list + token
pill update live); after you switch away it keeps compacting that session's
FILE on disk. One job per session; the active-agents dialog lists them with a
Stop control.

Reuses controller.rewrite_tool_output (live) / controller._rewrite_output_in_session_file
(background) — both stash the pre-compaction original as `_output_original` so
Restore/Revert can reverse it.
"""
import threading

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from systema.common.logger import _make_logger
from systema.common.token_est import estimate_tokens

log = _make_logger("CompactionManager")


class _JobWorker(QObject):
    progress = pyqtSignal(str)             # session_id — a step completed
    finished = pyqtSignal(str, int, int)   # session_id, n_done, saved

    def __init__(self, controller, session_id, targets, cancel):
        super().__init__()
        self._ctrl = controller
        self.session_id = session_id
        self._targets = targets            # list of (code, original_output)
        self._cancel = cancel
        self.total = len(targets)
        self.done = 0
        self.saved = 0

    def run(self):
        from systema.agents.compactor_agent import CompactorAgent
        agent = CompactorAgent(self._ctrl.ai)
        n_done = 0
        for code, out in self._targets:
            if self._cancel.is_set():
                break
            try:
                summary = agent.compact(code, out)
            except Exception as e:
                log.error(f"[_JobWorker] compact failed: {e}")
                summary = None
            if summary and not self._cancel.is_set():
                new = "[Compacted] " + summary
                if self._apply(out, new):
                    n_done += 1
                    self.saved += max(0, estimate_tokens(out) - estimate_tokens(new))
            self.done += 1
            self.progress.emit(self.session_id)
        self.finished.emit(self.session_id, n_done, self.saved)

    def _apply(self, old, new) -> bool:
        """Rewrite the output where the session currently lives: the live history
        when it is the loaded session, else its file on disk (survives switches)."""
        ctrl = self._ctrl
        try:
            if self.session_id == ctrl.current_session_id:
                # save=False: _auto_save_session touches the UI (refresh_session_list)
                # and must not run on this worker thread — the GUI-thread finish /
                # session-switch save persists it.
                return bool(ctrl.rewrite_tool_output(old, new, save=False))
            return bool(ctrl._rewrite_output_in_session_file(self.session_id, old, new))
        except Exception as e:
            log.error(f"[_JobWorker._apply] {e}")
            return False


class CompactionManager(QObject):
    """Owns the running jobs (GUI-thread object). `changed` fires on any job
    start/step/finish so the agents dialog can refresh."""
    changed = pyqtSignal()

    def __init__(self, controller):
        super().__init__()
        self._ctrl = controller
        self._jobs = {}   # session_id -> {'thread', 'worker', 'cancel'}

    # ── queries ───────────────────────────────────────────────────────────────
    def is_active(self, session_id=None) -> bool:
        if session_id is None:
            return bool(self._jobs)
        return session_id in self._jobs

    def active(self):
        """[(session_id, name, done, total), …] for the dialog."""
        rows = []
        for sid, j in self._jobs.items():
            w = j['worker']
            try:
                name = self._ctrl.session_manager.get_session_name(sid) or sid
            except Exception:
                name = sid
            rows.append((sid, name, w.done, w.total))
        return rows

    # ── control ───────────────────────────────────────────────────────────────
    def start(self, session_id, targets) -> bool:
        if not session_id or session_id in self._jobs or not targets:
            return False
        cancel = threading.Event()
        worker = _JobWorker(self._ctrl, session_id, targets, cancel)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        self._jobs[session_id] = {'thread': thread, 'worker': worker, 'cancel': cancel}
        thread.start()
        self.changed.emit()
        return True

    def stop(self, session_id):
        j = self._jobs.get(session_id)
        if j is not None:
            j['cancel'].set()

    def stop_all(self):
        for sid, j in list(self._jobs.items()):
            j['cancel'].set()
            try:
                j['thread'].quit()
                j['thread'].wait(2000)
            except Exception:
                pass
        self._jobs.clear()

    # ── slots (GUI thread) ─────────────────────────────────────────────────────
    def _on_progress(self, sid):
        ctrl = self._ctrl
        chat = getattr(ctrl, '_chat', None)
        try:
            if chat and sid == ctrl.current_session_id and hasattr(chat, '_update_token_count'):
                chat._update_token_count()   # live pill drop
        except Exception:
            pass
        self.changed.emit()

    def _on_finished(self, sid, n_done, saved):
        j = self._jobs.pop(sid, None)
        if j is not None:
            try:
                j['thread'].quit()
                j['thread'].wait(2000)
            except Exception:
                pass
        ctrl = self._ctrl
        chat = getattr(ctrl, '_chat', None)
        try:
            name = ctrl.session_manager.get_session_name(sid) or sid
        except Exception:
            name = sid
        try:
            if sid == ctrl.current_session_id:
                ctrl._auto_save_session()
                if chat:
                    chat.render_loaded_messages()
                    if hasattr(chat, '_update_token_count'):
                        chat._update_token_count()
            if chat:
                chat.add_system_message(
                    f"Compacted {n_done} output(s) in '{name}' — reclaimed "
                    f"~{saved:,} tokens. Restore via the session menu.")
        except Exception as e:
            log.error(f"[CompactionManager._on_finished] {e}")
        self.changed.emit()
