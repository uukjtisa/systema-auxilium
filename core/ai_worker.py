"""
AI Worker - Runs AI operations in background thread
UPDATED: Added 'post_exit' operation for guiding AI after tool mode
"""

from PyQt6.QtCore import QThread, pyqtSignal


class AIWorker(QThread):
    """Worker thread for AI operations"""

    # Signals
    response_ready = pyqtSignal(dict)  # Emits response dict
    error_occurred = pyqtSignal(str)  # Emits error message

    def __init__(self, ai_engine, operation, *args):
        super().__init__()
        self.ai_engine = ai_engine
        self.operation = operation
        self.args = args

    def run(self):
        """Run the AI operation in background"""
        try:
            if self.operation == 'generate':
                result = self.ai_engine.generate_response(*self.args)
            elif self.operation == 'generate_with_image':
                # New operation for image messages
                user_message, image_path = self.args
                result = self.ai_engine.generate_response_with_image(user_message, image_path)
            elif self.operation == 'continue_tool':
                result = self.ai_engine.continue_tool_mode()
            elif self.operation == 'post_exit':
                result = self.ai_engine.send_post_exit_prompt()
            else:
                result = {'response': 'Unknown operation', 'has_tool_call': False, 'in_tool_mode': False,
                          'thinking': False}

            self.response_ready.emit(result)

        except Exception as e:
            self.error_occurred.emit(str(e))