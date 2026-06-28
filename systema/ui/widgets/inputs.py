"""
systema/ui/widgets/inputs.py
Message input widgets — MultiLineInput + ResizableInput.
Extracted verbatim from chat_window.py.
"""
import os
from PyQt6.QtWidgets import QTextEdit, QWidget, QVBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QTimer, pyqtSignal


class MultiLineInput(QTextEdit):
    """Custom text input with Shift+Enter support"""
    enterPressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Send a message... (Shift+Enter for new line)")
        self.setMinimumHeight(24)
        self.setMaximumHeight(400)
        self.manual_resize = False

        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.textChanged.connect(self.adjust_height)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.enterPressed.emit()
                event.accept()
        else:
            super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        """Override paste to handle file paths"""
        if source.hasUrls():
            # Handle file drops/pastes
            for url in source.urls():
                file_path = url.toLocalFile()
                if file_path:
                    # Get the chat window to use its helper methods
                    chat_window = self.get_chat_window()
                    if chat_window:
                        cleaned_path = chat_window.clean_file_path(file_path)

                        # Check if image — prompt user via dialog
                        valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
                        if any(cleaned_path.lower().endswith(ext) for ext in valid_extensions):
                            QTimer.singleShot(0, lambda p=cleaned_path: chat_window._handle_image_file_drop(p))
                            return

                        # Quote non-image paths
                        if chat_window.should_quote_path(cleaned_path):
                            cleaned_path = f'"{cleaned_path}"'

                        self.insertPlainText(cleaned_path)
                    else:
                        # Fallback if can't find chat window
                        self.insertPlainText(file_path)
            return
        elif source.hasText():
            text = source.text().strip()

            # Get the chat window to use its helper methods
            chat_window = self.get_chat_window()
            if chat_window:
                cleaned_path = chat_window.clean_file_path(text)

                # Check if it's a valid file path
                if os.path.exists(cleaned_path):
                    # Check if image — prompt user via dialog
                    valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
                    if any(cleaned_path.lower().endswith(ext) for ext in valid_extensions):
                        QTimer.singleShot(0, lambda p=cleaned_path: chat_window._handle_image_file_drop(p))
                        return

                    # Quote non-image paths
                    if chat_window.should_quote_path(cleaned_path):
                        cleaned_path = f'"{cleaned_path}"'

                    self.insertPlainText(cleaned_path)
                    return

            # If not a file path, paste normally
            super().insertFromMimeData(source)
        else:
            super().insertFromMimeData(source)

    def get_chat_window(self):
        """Get the ChatWindow parent"""
        parent = self.parent()
        while parent:
            if parent.__class__.__name__ == 'ChatWindow':
                return parent
            parent = parent.parent()
        return None

    def adjust_height(self):
        if self.manual_resize:
            return
        doc_height = self.document().size().height()
        new_height = min(max(int(doc_height) + 10, 24), self.maximumHeight())
        self.setFixedHeight(new_height)
        if self.parent():
            self.parent().updateGeometry()


class ResizableInput(QWidget):
    """Input container with manual resize handle"""
    enterPressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.min_height = 24
        self.max_height = 400
        self.is_resizing = False
        self.resize_start_y = 0
        self.resize_start_height = 0

        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)

        self.resize_handle = QLabel("⋮⋮⋮")
        self.resize_handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.resize_handle.setFixedHeight(8)
        self.resize_handle.setCursor(Qt.CursorShape.SizeVerCursor)
        self.resize_handle.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #8B949E;
                font-size: 6px;
                letter-spacing: 1px;
            }
            QLabel:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #9AA0A6;
            }
        """)
        self.resize_handle.installEventFilter(self)
        layout.addWidget(self.resize_handle)

        self.text_input = MultiLineInput()
        self.text_input.enterPressed.connect(self.enterPressed.emit)
        self.text_input.setFixedHeight(self.min_height)
        layout.addWidget(self.text_input)

    def eventFilter(self, obj, event):
        if obj == self.resize_handle:
            if event.type() == event.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.is_resizing = True
                    self.resize_start_y = event.globalPosition().y()
                    self.resize_start_height = self.text_input.height()
                    self.text_input.manual_resize = True
                    return True
            elif event.type() == event.Type.MouseMove and self.is_resizing:
                delta = self.resize_start_y - event.globalPosition().y()
                new_height = self.resize_start_height + delta
                new_height = max(self.min_height, min(self.max_height, new_height))
                self.text_input.setFixedHeight(int(new_height))
                self.updateGeometry()
                if self.parent():
                    self.parent().updateGeometry()
                    if self.parent().parent():
                        self.parent().parent().updateGeometry()
                return True
            elif event.type() == event.Type.MouseButtonRelease:
                self.is_resizing = False
                return True
        return super().eventFilter(obj, event)

    def toPlainText(self):
        return self.text_input.toPlainText()

    def clear(self):
        """Clear input and maintain manual resize state if needed"""
        current_manual = self.text_input.manual_resize
        current_height = self.text_input.height() if current_manual else self.min_height

        self.text_input.clear()

        # Only reset if not manually resized
        if not current_manual:
            self.text_input.setFixedHeight(self.min_height)
        else:
            self.text_input.setFixedHeight(current_height)

        self.updateGeometry()
        self.update()

    def setEnabled(self, enabled):
        self.text_input.setEnabled(enabled)

    def setPlaceholderText(self, text):
        self.text_input.setPlaceholderText(text)


