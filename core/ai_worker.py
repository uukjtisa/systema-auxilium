"""
core/ai_worker.py
AI Worker - Runs AI operations in background thread
UPDATED: Updated to match new work_environment and execute_code naming
"""

from core.logger import _make_logger, _NoOpLogger
from PyQt6.QtCore import QThread, pyqtSignal


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("AIWorker") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


class AIWorker(QThread):
    """Worker thread for AI operations"""

    # Signals
    response_ready = pyqtSignal(dict)  # Emits response dict
    error_occurred = pyqtSignal(str)   # Emits error message

    def __init__(self, ai_engine, operation, *args):
        super().__init__()
        self.ai_engine = ai_engine
        self.operation = operation
        self.args = args
        log.info(f"[AIWorker.__init__] Worker created | operation='{operation}' | "
                 f"args_count={len(args)} | engine={type(ai_engine).__name__}")

    def run(self):
        """Run the AI operation in background"""
        log.info(f"[AIWorker.run] ── Thread started | operation='{self.operation}' ──────────────")

        try:
            if self.operation == 'generate':
                log.debug("[AIWorker.run] Dispatching → ai_engine.generate_response(*args)")
                result = self.ai_engine.generate_response(*self.args)


            elif self.operation == 'generate_with_image':
                # Accepts single path (str) or list of paths (multi-image)
                user_message, image_paths = self.args
                log.debug(f"[AIWorker.run] Dispatching → ai_engine.generate_response_with_image | "
                          f"images='{image_paths}' | "
                          f"message_preview='{str(user_message)[:60]}'")
                result = self.ai_engine.generate_response_with_image(user_message, image_paths)

            elif self.operation == 'continue_tool':
                log.debug("[AIWorker.run] Dispatching → ai_engine.continue_work_mode()")
                result = self.ai_engine.continue_work_mode()

            else:
                log.warning(f"[AIWorker.run] Unknown operation: '{self.operation}' — returning stub result")
                result = {
                    'response': 'Unknown operation',
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False,
                }

            log.info(f"[AIWorker.run] ✓ Operation '{self.operation}' complete | "
                     f"has_work_call={result.get('has_work_call')} | "
                     f"in_work_mode={result.get('in_work_mode')} | "
                     f"thinking={result.get('thinking')} | "
                     f"response_len={len(str(result.get('response', '')))}")
            log.debug("[AIWorker.run] Emitting response_ready signal")
            self.response_ready.emit(result)

        except Exception as e:
            log.error(f"[AIWorker.run] ✗ Exception in operation '{self.operation}': "
                      f"{type(e).__name__}: {e}")
            log.debug("[AIWorker.run] Emitting error_occurred signal")
            self.error_occurred.emit(str(e))
