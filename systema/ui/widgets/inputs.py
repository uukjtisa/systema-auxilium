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
    # Emitted after auto-grow actually changes the field height, so an overlay
    # host can re-anchor synchronously (a deferred reposition can measure a
    # stale sizeHint and let the growing pill spill downward off the bottom).
    heightChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Send a message... (Shift+Enter for new line)")
        self.setMinimumHeight(24)
        self.setMaximumHeight(400)
        self._grow_max = 400          # auto-grow ceiling (see adjust_height)
        # A manual drag of the resize handle sets a TEMPORARY floor (px) here.
        # Auto-grow is never disabled — it can still grow above the floor — and
        # the floor is cleared the instant the field is emptied, so the box
        # always collapses back to one line. 0 = no floor.
        self._user_floor = 0

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
        # Auto-grow is ALWAYS active. A manual drag only raises a temporary floor
        # (self._user_floor) that auto-grow can still exceed; emptying the field
        # clears the floor so it never stays stuck tall after a big paste is
        # deleted (the two bugs this replaces).
        # Force the document to lay out at the current editor width so its height
        # reflects wrapped lines (without this the height can lag a keystroke or
        # stay at one line when the widget hasn't been given a width yet).
        doc = self.document()
        vw = self.viewport().width()
        if vw > 0:
            doc.setTextWidth(vw)
        if not self.toPlainText():
            self._user_floor = 0
        doc_height = doc.size().height()
        # Effective height = max(content, manual floor), clamped. Cap against the
        # CONSTANT ceiling, not maximumHeight() — setFixedHeight sets max==height,
        # so reading maximumHeight() here would ratchet the cap down each call.
        new_height = min(max(int(doc_height) + 10, self._user_floor, 24), self._grow_max)
        if new_height != self.height():
            self.setFixedHeight(new_height)
            # Propagate up so the pill / input container actually expand (each
            # ancestor uses a SetMinimumSize layout that must recompute).
            w = self.parentWidget()
            depth = 0
            while w is not None and depth < 5:
                w.updateGeometry()
                w = w.parentWidget()
                depth += 1
            # Re-anchor any floating overlay host NOW (synchronously), while the
            # grown geometry is fresh — see heightChanged docstring.
            self.heightChanged.emit()


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
                    return True
            elif event.type() == event.Type.MouseMove and self.is_resizing:
                delta = self.resize_start_y - event.globalPosition().y()
                new_height = self.resize_start_height + delta
                new_height = max(self.min_height, min(self.max_height, new_height))
                self.text_input.setFixedHeight(int(new_height))
                # Record the drag as a temporary floor; auto-grow can still grow
                # above it, and it clears itself when the field is emptied.
                self.text_input._user_floor = int(new_height)
                self.updateGeometry()
                if self.parent():
                    self.parent().updateGeometry()
                    if self.parent().parent():
                        self.parent().parent().updateGeometry()
                # Re-anchor the floating overlay NOW so a manual drag grows the
                # pill UPWARD too (keeps it bottom-anchored during the drag).
                self.text_input.heightChanged.emit()
                return True
            elif event.type() == event.Type.MouseButtonRelease:
                self.is_resizing = False
                return True
        return super().eventFilter(obj, event)

    def toPlainText(self):
        return self.text_input.toPlainText()

    def clear(self):
        """Clear input and collapse back to one line. Drops any manual-drag floor
        (a drag only affects the message being composed) and re-anchors the
        overlay. The explicit setFixedHeight wins over the adjust_height that
        clear() triggers, keeping the collapsed height deterministic."""
        self.text_input._user_floor = 0
        self.text_input.clear()
        self.text_input.setFixedHeight(self.min_height)
        self.text_input.heightChanged.emit()
        self.updateGeometry()
        self.update()

    def setEnabled(self, enabled):
        self.text_input.setEnabled(enabled)

    def setPlaceholderText(self, text):
        self.text_input.setPlaceholderText(text)


