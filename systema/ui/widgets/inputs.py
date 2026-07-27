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

    def _chat(self):
        """The ChatWindow hosting this input, if it can be reached."""
        return self.get_chat_window() if hasattr(self, 'get_chat_window') else None

    def keyPressEvent(self, event):
        key = event.key()

        # ── slash-command autocomplete ───────────────────────────────────────
        # Only intercepted WHILE THE DROPDOWN IS OPEN, so none of these keys
        # change meaning during ordinary typing.
        chat = self._chat()
        if chat is not None and getattr(chat, 'command_popup_visible', None) \
                and chat.command_popup_visible():
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Right):
                if chat.accept_command_completion():
                    event.accept()
                    return
            elif key == Qt.Key.Key_Down:
                chat.move_command_selection(1)
                event.accept()
                return
            elif key == Qt.Key.Key_Up:
                chat.move_command_selection(-1)
                event.accept()
                return
            elif key == Qt.Key.Key_Escape:
                # CONSUMED here on purpose. Escape is bound to interrupt at the
                # window level, so letting it through would stop the assistant
                # merely because a dropdown was dismissed. A second Escape,
                # with the popup closed, interrupts as normal.
                chat.hide_command_completer()
                event.accept()
                return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                # Enter ALWAYS runs the line as typed — it never doubles as
                # "accept the completion", or a fully typed command would
                # no-op instead of executing.
                #
                # The dropdown closes here regardless of what the line turns
                # out to be. Leaving that to the command handler meant any
                # early return down that path (a bare "/" parses to nothing)
                # stranded the list on screen after the text was gone.
                if chat is not None and hasattr(chat, 'hide_command_completer'):
                    chat.hide_command_completer()
                self.enterPressed.emit()
                event.accept()
            return

        super().keyPressEvent(event)
        if chat is not None and hasattr(chat, 'refresh_command_completer'):
            chat.refresh_command_completer()

    def canInsertFromMimeData(self, source):
        # OS file drags carry URLs but often no text, so the default QTextEdit
        # check rejects them — Qt then shows the forbidden cursor over the input
        # pill. Accepting URLs here routes the drop into insertFromMimeData
        # below, which already inserts the cleaned/quoted path (or attaches an
        # image via the dialog).
        return source.hasUrls() or super().canInsertFromMimeData(source)

    IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif')

    @classmethod
    def _looks_like_image(cls, path: str) -> bool:
        return path.lower().endswith(cls.IMAGE_EXTS)

    def _route_dropped_paths(self, paths) -> bool:
        """Offer to attach any image files among `paths`; insert the rest as text.

        Returns True when it handled the paste. Written to take a LIST because
        both entry points can carry several files at once and both used to
        mishandle that: the URL branch returned after the FIRST image, and the
        text branch tested the whole multi-line blob as one path — so pasting
        two images (Windows "Copy as path" gives quoted, newline-separated
        text) silently pasted raw paths instead of offering to attach them.
        """
        chat_window = self.get_chat_window()
        if not chat_window or not paths:
            return False

        cleaned = [chat_window.clean_file_path(p) for p in paths if p]
        images = [p for p in cleaned if self._looks_like_image(p)]
        others = [p for p in cleaned if not self._looks_like_image(p)]

        if images:
            if len(images) == 1:
                QTimer.singleShot(
                    0, lambda p=images[0]: chat_window._handle_image_file_drop(p))
            else:
                QTimer.singleShot(
                    0, lambda ps=list(images):
                    chat_window._handle_multiple_image_files_dialog(ps))

        for path in others:
            if chat_window.should_quote_path(path):
                path = f'"{path}"'
            self.insertPlainText(path + ("\n" if len(others) > 1 else ""))

        return bool(images or others)

    def insertFromMimeData(self, source):
        """Override paste to handle file paths (one or many)."""
        if source.hasUrls():
            paths = [u.toLocalFile() for u in source.urls() if u.toLocalFile()]
            if self._route_dropped_paths(paths):
                return
            for p in paths:                       # no chat window to ask
                self.insertPlainText(p)
            return

        if source.hasText():
            text = source.text().strip()
            chat_window = self.get_chat_window()
            if chat_window and text:
                # One path per line, each possibly quoted — that is exactly the
                # shape Windows "Copy as path" produces for a multi-selection.
                candidates = [ln.strip().strip('"').strip()
                              for ln in text.splitlines() if ln.strip()]
                resolved = [chat_window.clean_file_path(c) for c in candidates]
                if resolved and all(os.path.exists(p) for p in resolved):
                    if self._route_dropped_paths(candidates):
                        return

            # If not file paths, paste normally
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
        empty = not self.toPlainText()
        if empty:
            self._user_floor = 0
        doc_height = doc.size().height()
        # Effective height = max(content, manual floor), clamped. Cap against the
        # CONSTANT ceiling, not maximumHeight() — setFixedHeight sets max==height,
        # so reading maximumHeight() here would ratchet the cap down each call.
        # An EMPTY field always collapses to the one-line design height — the
        # empty document briefly reports a taller height right after a clear,
        # which used to leave an 8px residue floored into the pill.
        new_height = 24 if empty else min(
            max(int(doc_height) + 10, self._user_floor, 24), self._grow_max)
        if new_height != self.height():
            self.setFixedHeight(new_height)
            self._relayout_ancestors()
            # Re-anchor any floating overlay host NOW (synchronously), while the
            # grown geometry is fresh — see heightChanged docstring.
            self.heightChanged.emit()

    def _relayout_ancestors(self):
        """Bottom-up re-activation of every ancestor's layout after a height
        change. Each ancestor (ResizableInput → row → pill card → overlay
        container) uses a SetMinimumSize layout, which writes an EXPLICIT
        minimum size onto its widget; Qt only re-applies those lazily, so on a
        SHRINK (send → clear) the stale tall minimums floored the pill's size
        hint and the overlay stayed suspended mid-screen. invalidate() +
        activate() child-before-parent re-applies every minimum with fresh
        values, synchronously."""
        w = self.parentWidget()
        depth = 0
        while w is not None and depth < 5:
            w.updateGeometry()
            lay = w.layout()
            if lay is not None:
                lay.invalidate()
                lay.activate()
            w = w.parentWidget()
            depth += 1


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
        # getattr guard: if the Python wrapper was GC'd and PyQt recreated it
        # for a live C++ widget, instance attributes are gone — never crash in
        # an event filter over that.
        if obj is getattr(self, 'resize_handle', None):
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
                # Bottom-up relayout so drag-SHRINK releases the stale
                # SetMinimumSize floors too, not just drag-grow.
                self.text_input._relayout_ancestors()
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
        # Release the ancestors' SetMinimumSize floors for THIS height too —
        # without it the pill keeps whatever floor the clear()-triggered
        # adjust_height pass left behind.
        self.text_input._relayout_ancestors()
        self.text_input.heightChanged.emit()
        self.updateGeometry()
        self.update()

    def setEnabled(self, enabled):
        self.text_input.setEnabled(enabled)

    def setPlaceholderText(self, text):
        self.text_input.setPlaceholderText(text)


