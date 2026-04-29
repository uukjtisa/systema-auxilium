"""
ui/code_approval_dialog.py
Code Approval Dialog - Shows code before execution for supervised mode
FIXED: Simple chat window for explanations, stays on top, no crashes
"""

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                             QTextEdit, QLabel, QCheckBox, QMessageBox, QSplitter,
                             QWidget, QScrollArea)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont


class AIResponseWorker(QThread):
    """Worker thread for AI responses to prevent UI freezing"""

    response_ready = pyqtSignal(str)  # Signal when response is ready
    error_occurred = pyqtSignal(str)  # Signal when error occurs

    def __init__(self, ai_engine, prompt):
        super().__init__()
        self.ai_engine = ai_engine
        self.prompt = prompt

    def run(self):
        """Run the AI request in background"""
        try:
            # Get AI response using internal method
            if hasattr(self.ai_engine, '_get_ai_response_internal'):
                response = self.ai_engine._get_ai_response_internal(self.prompt)
                if isinstance(response, dict) and 'response' in response:
                    self.response_ready.emit(response['response'])
                else:
                    self.response_ready.emit(str(response))
            else:
                # Fallback: use provider-specific method
                if self.ai_engine.ai_provider == 'anthropic':
                    response = self._get_anthropic_response()
                elif self.ai_engine.ai_provider == 'gemini':
                    response = self._get_gemini_response()
                elif self.ai_engine.ai_provider == 'puter':
                    response = self._get_puter_response()
                else:
                    self.error_occurred.emit("Unknown AI provider")
                    return

                self.response_ready.emit(response)

        except Exception as e:
            self.error_occurred.emit(f"Error: {str(e)}")

    def _get_anthropic_response(self):
        """Get Anthropic response"""
        import requests
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.ai_engine.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        data = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": self.prompt}]
        }

        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()

        return result['content'][0]['text']

    def _get_gemini_response(self):
        """Get Gemini response"""
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.ai_engine.gemini_model}:generateContent?key={self.ai_engine.gemini_api_key}"

        data = {
            "contents": [{
                "parts": [{"text": self.prompt}]
            }]
        }

        response = requests.post(url, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()

        return result['candidates'][0]['content']['parts'][0]['text']

    def _get_puter_response(self):
        """Get Puter response"""
        if not self.ai_engine.puter_server or not self.ai_engine.puter_server.is_running():
            raise Exception("Puter server not running")

        response = self.ai_engine.generate_response(self.prompt)
        return response


class SimpleChatWidget(QWidget):
    """Simple chat widget for code explanations"""

    send_message = pyqtSignal(str)  # Signal to send message to AI

    def __init__(self, parent=None):
        super().__init__(parent)
        self.message_history = []
        self.init_ui()

    def init_ui(self):
        """Initialize the chat UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        # Chat title/header
        header = QLabel("💬 Code Analysis Chat")
        header.setStyleSheet("""
            QLabel {
                color: #8B949E;
                font-size: 12px;
                font-weight: 600;
                margin-bottom: 8px;
                padding: 8px 0px;
                letter-spacing: 0.5px;
            }
        """)
        layout.addWidget(header)

        # Chat history display
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setStyleSheet("""
            QTextEdit {
                background-color: #0D1117;
                border: 1px solid rgba(88, 166, 255, 0.18);
                border-radius: 8px;
                padding: 12px;
                font-size: 12px;
                color: #E6EDF3;
                line-height: 1.5;
            }
            QScrollBar:vertical {
                background: transparent; width: 6px; border: none;
            }
            QScrollBar::handle:vertical {
                background: #21262D; border-radius: 3px; min-height: 20px;
            }
            QScrollBar::handle:vertical:hover { background: #30363D; }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
        """)
        layout.addWidget(self.chat_display, 1)

        # Input area
        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)

        self.input_field = QTextEdit()
        self.input_field.setPlaceholderText("Ask about the code...")
        self.input_field.setMaximumHeight(50)
        self.input_field.setStyleSheet("""
            QTextEdit {
                background-color: #161B22;
                border: 1px solid rgba(88, 166, 255, 0.18);
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
                color: #E6EDF3;
            }
            QTextEdit:focus {
                border: 1px solid rgba(88, 166, 255, 0.55);
            }
        """)

        # Send button
        send_btn = QPushButton("Send")
        send_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(88, 166, 255, 0.12);
                border: 1px solid rgba(88, 166, 255, 0.28);
                border-radius: 6px;
                padding: 10px 18px;
                font-size: 12px;
                color: #58A6FF;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(88, 166, 255, 0.22);
                border-color: #58A6FF;
            }
            QPushButton:disabled {
                background-color: transparent;
                border-color: #21262D;
                color: #30363D;
            }
        """)
        send_btn.clicked.connect(self.send_user_message)

        input_layout.addWidget(self.input_field, 1)
        input_layout.addWidget(send_btn)

        layout.addLayout(input_layout)

    def send_user_message(self):
        """Send user message"""
        message = self.input_field.toPlainText().strip()
        if not message:
            return
        self.add_message("You", message, "#58A6FF")
        self.send_message.emit(message)
        self.input_field.clear()

    def add_message(self, sender, message, color="#8B949E"):
        """Add message to chat display"""
        self.message_history.append((sender, message))
        formatted = f'<div style="margin-bottom: 10px; padding: 8px 10px; background: rgba(88,166,255,0.04); border-left: 2px solid {color}; border-radius: 3px;">'
        formatted += f'<b style="color: {color}; font-size: 11px;">{sender}</b><br>'
        formatted += f'<span style="color: #E6EDF3; font-size: 12px;">{message}</span>'
        formatted += '</div>'
        self.chat_display.append(formatted)
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.chat_display.setTextCursor(cursor)

    def clear_history(self):
        """Clear chat history"""
        self.message_history = []
        self.chat_display.clear()

    def show_loading(self):
        """Show loading indicator"""
        formatted = '<div style="margin-bottom: 10px; padding: 8px 10px; background: rgba(88,166,255,0.04); border-left: 2px solid #30363D; border-radius: 3px;">'
        formatted += '<b style="color: #30363D; font-size: 11px;">AI</b><br>'
        formatted += '<span style="color: #8B949E; font-size: 12px;"><i>Thinking…</i></span>'
        formatted += '</div>'
        self.chat_display.append(formatted)
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.chat_display.setTextCursor(cursor)

    def remove_last_message(self):
        """Remove the last message (used to remove loading indicator)"""
        if self.message_history:
            self.message_history.pop()

        # Rebuild display without last message
        self.chat_display.clear()
        for sender, message in self.message_history:
            color = "#58A6FF" if sender == "You" else "#8B949E"
            formatted = f'<div style="margin-bottom: 12px;">'
            formatted += f'<b style="color: {color};">{sender}:</b><br>'
            formatted += f'<span style="color: #E8EAED;">{message}</span>'
            formatted += '</div>'
            self.chat_display.append(formatted)


class CodeApprovalDialog(QDialog):
    """Dialog for reviewing and approving code before execution"""

    def __init__(self, code, execution_type, ai_engine, parent=None):
        """
        Initialize code approval dialog

        Args:
            code: The code to be executed
            execution_type: 'execute_code' or 'work_environment'
            ai_engine: Reference to AI engine for explanations
            parent: Parent widget
        """
        super().__init__(parent)
        self._is_explained = False
        self.code = code
        self.execution_type = execution_type
        self.ai_engine = ai_engine
        self.result = None  # 'accept', 'reject', or None
        self.modified_code = code
        self.chat_visible = False
        self.worker = None  # AI response worker thread

        self.init_ui()

    def init_ui(self):
        """Initialize the UI"""
        self.setWindowTitle("Code Approval Required")
        self.setModal(True)
        self.setMinimumSize(900, 500)
        self.resize(1200, 600)

        # CRITICAL: Make window stay on top of everything
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint |  # Stay on top!
            Qt.WindowType.WindowCloseButtonHint
        )

        # Raise and activate window
        self.raise_()
        self.activateWindow()

        self.setStyleSheet("""
            QDialog {
                background-color: #0D1117;
            }
            QWidget {
                color: #E6EDF3;
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Title
        title = QLabel("⚠️ Code Execution Approval (You can disable this pop up in the settings)")
        title.setStyleSheet("""
            QLabel {
                color: #E6EDF3;
                font-size: 15px;
                font-weight: 600;
                margin-bottom: 8px;
            }
        """)
        layout.addWidget(title)

        # Description
        desc_text = "work environment" if self.execution_type == "work_environment" else "direct execution"
        desc = QLabel(f"The AI wants to execute the following code ({desc_text}). Review and approve:")
        desc.setStyleSheet("color: #8B949E; font-size: 11px; margin-bottom: 8px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Splitter for code editor and chat (chat hidden by default)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # Code editor container
        code_container = QWidget()
        code_layout = QVBoxLayout(code_container)
        code_layout.setContentsMargins(0, 0, 0, 0)

        self.code_edit = QTextEdit()
        self.code_edit.setPlainText(self.code)
        self.code_edit.setStyleSheet("""
            QTextEdit {
                background-color: #0D1117;
                border: 1px solid rgba(88, 166, 255, 0.18);
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                color: #E6EDF3;
                line-height: 1.6;
            }
            QTextEdit:focus {
                border: 1px solid rgba(88, 166, 255, 0.55);
                background-color: #0a0f16;
            }
        """)

        # Set monospace font
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.code_edit.setFont(font)

        code_layout.addWidget(self.code_edit)

        # Chat widget (hidden by default)
        self.chat_widget = SimpleChatWidget()
        self.chat_widget.send_message.connect(self.handle_chat_message)
        self.chat_widget.setStyleSheet("""
            SimpleChatWidget {
                background-color: #0D1117;
                border-left: 1px solid rgba(88, 166, 255, 0.18);
                border-radius: 0px;
                padding-left: 12px;
            }
        """)
        self.chat_widget.hide()

        self.splitter.addWidget(code_container)
        self.splitter.addWidget(self.chat_widget)
        self.splitter.setStretchFactor(0, 2)  # Code gets 2/3 of space
        self.splitter.setStretchFactor(1, 1)  # Chat gets 1/3 of space

        layout.addWidget(self.splitter, 1)

        # Warning label
        warning = QLabel("⚠️ Only approve code you understand and trust")
        warning.setStyleSheet("""
            QLabel {
                color: rgba(242, 139, 130, 0.85);
                font-size: 11px;
                font-style: italic;
                margin-top: 4px;
            }
        """)
        layout.addWidget(warning)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        _btn_base = """
            QPushButton {
                border-radius: 6px;
                padding: 9px 20px;
                font-size: 12px;
                font-weight: 500;
            }
        """

        # Explain button
        self.explain_btn = QPushButton("🤔 Explain")
        self.explain_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 9px 20px;
                font-size: 12px;
                color: #8B949E;
            }
            QPushButton:hover {
                background-color: rgba(88, 166, 255, 0.08);
                color: #E6EDF3;
                border-color: rgba(88, 166, 255, 0.4);
            }
        """)
        self.explain_btn.clicked.connect(self.on_explain)

        # Reject button
        reject_btn = QPushButton("❌ Reject")
        reject_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 1px solid rgba(242, 139, 130, 0.45);
                border-radius: 6px;
                padding: 9px 20px;
                font-size: 12px;
                color: #F28B82;
            }
            QPushButton:hover {
                background-color: rgba(242, 139, 130, 0.1);
                border-color: #F28B82;
            }
        """)
        reject_btn.clicked.connect(self.on_reject)

        # Accept button
        accept_btn = QPushButton("✅ Accept & Execute")
        accept_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(88, 166, 255, 0.15);
                border: 1px solid rgba(88, 166, 255, 0.45);
                border-radius: 6px;
                padding: 9px 24px;
                font-size: 12px;
                color: #58A6FF;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(88, 166, 255, 0.25);
                border-color: #58A6FF;
                color: #79BBFF;
            }
        """)
        accept_btn.clicked.connect(self.on_accept)
        accept_btn.setDefault(True)

        button_layout.addWidget(self.explain_btn)
        button_layout.addStretch()
        button_layout.addWidget(reject_btn)
        button_layout.addWidget(accept_btn)

        layout.addLayout(button_layout)

    def showEvent(self, event):
        """Override show event to ensure window stays on top"""
        super().showEvent(event)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        """Override close event to cleanup worker thread"""
        # Stop worker thread if running
        if self.worker and self.worker.isRunning():
            self.worker.quit()
            self.worker.wait()
        super().closeEvent(event)

    def on_explain(self):
        """Show chat and request explanation of the code"""
        # Toggle chat visibility
        if not self.chat_visible:
            # Show chat
            self.chat_widget.show()
            self.chat_visible = True
            self.explain_btn.setText("🤔 Hide Panel")

            # Get current code
            current_code = self.code_edit.toPlainText().strip()

            # Send initial explanation request
            explain_prompt = (
                f"<SYSTEM_AUTOMATED_MESSAGE> PLEASE EXPLAIN THIS CODE THAT IS TO BE EXECUTED, "
                f"WARN OF ANY RISK AND MALICIOUS INTENT IF ANY. "
                f"SAY WHETHER IT IS [SAFE, RISKY, BAD] ALSO KEEP YOUR RESPONSE FOR THIS SPECIFIC MESSAGE CONCISE AND CLEAN OF ANY MARKDOWN OR SEPCIAL CHARACTERS </SYSTEM_AUTOMATED_MESSAGE>\n\n"
                f"```python\n{current_code}\n```"
            )

            if not self._is_explained:
                self.chat_widget.add_message("You", "Explain this code and assess its safety", "#58A6FF")
                self.request_ai_response(explain_prompt)
                self._is_explained = True
        else:
            # Hide chat
            self.chat_widget.hide()
            self.chat_visible = False
            self.explain_btn.setText("🤔 Explain")

    def handle_chat_message(self, message):
        """Handle user message in chat"""
        # Send to AI
        self.request_ai_response(message)

    def request_ai_response(self, prompt):
        """Request AI response without affecting main conversation"""
        # Show loading indicator
        self.chat_widget.show_loading()

        # Disable send button during request
        for widget in self.chat_widget.findChildren(QPushButton):
            if widget.text() == "Send":
                widget.setEnabled(False)

        # Stop any existing worker
        if self.worker and self.worker.isRunning():
            self.worker.quit()
            self.worker.wait()

        # Create and start worker thread
        self.worker = AIResponseWorker(self.ai_engine, prompt)
        self.worker.response_ready.connect(self.on_ai_response)
        self.worker.error_occurred.connect(self.on_ai_error)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def on_ai_response(self, response):
        """Handle AI response from worker thread"""
        # Get current HTML and remove the loading message
        html = self.chat_widget.chat_display.toHtml()

        # Simple approach: clear and rebuild without loading
        if "Thinking..." in html:
            # Get all messages except the loading one
            temp_history = self.chat_widget.message_history.copy()
            self.chat_widget.chat_display.clear()

            # Rebuild chat without loading message
            for sender, message in temp_history:
                color = "#58A6FF" if sender == "You" else "#8B949E"
                formatted = f'<div style="margin-bottom: 12px;">'
                formatted += f'<b style="color: {color};">{sender}:</b><br>'
                formatted += f'<span style="color: #E8EAED;">{message}</span>'
                formatted += '</div>'
                self.chat_widget.chat_display.append(formatted)

        # Add actual response
        self.chat_widget.add_message("AI", response, "#3FB950")

    def on_ai_error(self, error_msg):
        """Handle AI error from worker thread"""
        # Get current HTML and remove the loading message
        html = self.chat_widget.chat_display.toHtml()

        # Simple approach: clear and rebuild without loading
        if "Thinking..." in html:
            # Get all messages except the loading one
            temp_history = self.chat_widget.message_history.copy()
            self.chat_widget.chat_display.clear()

            # Rebuild chat without loading message
            for sender, message in temp_history:
                color = "#58A6FF" if sender == "You" else "#8B949E"
                formatted = f'<div style="margin-bottom: 12px;">'
                formatted += f'<b style="color: {color};">{sender}:</b><br>'
                formatted += f'<span style="color: #E8EAED;">{message}</span>'
                formatted += '</div>'
                self.chat_widget.chat_display.append(formatted)

        # Add error message
        self.chat_widget.add_message("System", error_msg, "#F28B82")

    def on_worker_finished(self):
        """Re-enable UI after worker finishes"""
        # Re-enable send button
        for widget in self.chat_widget.findChildren(QPushButton):
            if widget.text() == "Send":
                widget.setEnabled(True)

    def on_reject(self):
        """Reject code execution"""
        # Stop worker thread if running
        if self.worker and self.worker.isRunning():
            self.worker.quit()
            self.worker.wait()

        # Clear chat history
        if self.chat_visible:
            self.chat_widget.clear_history()

        self.result = 'reject'
        self.close()

    def on_accept(self):
        """Accept and execute code"""
        # Stop worker thread if running
        if self.worker and self.worker.isRunning():
            self.worker.quit()
            self.worker.wait()

        # Clear chat history
        if self.chat_visible:
            self.chat_widget.clear_history()

        self.result = 'accept'
        self.modified_code = self.code_edit.toPlainText().strip()
        self.accept()

    @staticmethod
    def get_approval(code, execution_type, ai_engine, parent=None):
        """
        Show dialog and get approval

        Returns:
            tuple: (approved: bool, modified_code: str)
        """
        dialog = CodeApprovalDialog(code, execution_type, ai_engine, parent)

        # Ensure it shows on top
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

        result = dialog.exec()

        approved = dialog.result == 'accept'
        modified_code = dialog.modified_code if approved else code

        return approved, modified_code