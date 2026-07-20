"""
systema/ui/widgets/code_blocks.py
Code & table block widgets + syntax highlighter.
Extracted verbatim from chat_window.py.
"""
import re
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
                             QPushButton, QScrollArea, QTextEdit, QApplication,
                             QSizePolicy)
from PyQt6.QtCore import Qt, QTimer, QEvent
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont

# Copy-button "Copied!" feedback duration (ms)
ANIM_COPY_FEEDBACK_MS = 1500


class CodeSyntaxHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for code blocks - supports multiple languages"""

    def __init__(self, parent, language):
        super().__init__(parent)
        self.language = language.lower()
        self.highlighting_rules = []

        # Define color scheme (similar to ChatGPT/VS Code Dark+)
        self.colors = {
            'keyword': '#C586C0',  # Purple for keywords
            'builtin': '#4EC9B0',  # Teal for built-in types
            'string': '#CE9178',  # Orange for strings
            'comment': '#6A9955',  # Green for comments
            'function': '#DCDCAA',  # Yellow for functions
            'number': '#B5CEA8',  # Light green for numbers
            'operator': '#D4D4D4',  # Light gray for operators
            'class': '#4EC9B0',  # Teal for class names
            'decorator': '#4EC9B0',  # Teal for decorators
        }

        self.setup_rules()

    def setup_rules(self):
        """Setup syntax highlighting rules based on language"""

        # Python keywords
        if self.language == 'python':
            keywords = [
                'def', 'class', 'import', 'from', 'as', 'if', 'elif', 'else',
                'for', 'while', 'break', 'continue', 'return', 'yield', 'pass',
                'try', 'except', 'finally', 'raise', 'with', 'assert', 'lambda',
                'and', 'or', 'not', 'in', 'is', 'None', 'True', 'False', 'async',
                'await', 'global', 'nonlocal', 'del'
            ]

            builtins = [
                'int', 'str', 'float', 'bool', 'list', 'dict', 'tuple', 'set',
                'object', 'type', 'super', 'self', 'len', 'range', 'print',
                'input', 'open', 'enumerate', 'zip', 'map', 'filter'
            ]

            # Keywords
            keyword_format = QTextCharFormat()
            keyword_format.setForeground(QColor(self.colors['keyword']))
            keyword_format.setFontWeight(QFont.Weight.Bold)
            for word in keywords:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), keyword_format))

            # Built-in types and functions
            builtin_format = QTextCharFormat()
            builtin_format.setForeground(QColor(self.colors['builtin']))
            for word in builtins:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), builtin_format))

            # Strings (single and double quotes)
            string_format = QTextCharFormat()
            string_format.setForeground(QColor(self.colors['string']))
            self.highlighting_rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))
            self.highlighting_rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))
            self.highlighting_rules.append((re.compile(r'""".*?"""', re.DOTALL), string_format))
            self.highlighting_rules.append((re.compile(r"'''.*?'''", re.DOTALL), string_format))

            # Comments
            comment_format = QTextCharFormat()
            comment_format.setForeground(QColor(self.colors['comment']))
            comment_format.setFontItalic(True)
            self.highlighting_rules.append((re.compile(r'#[^\n]*'), comment_format))

            # Function definitions
            function_format = QTextCharFormat()
            function_format.setForeground(QColor(self.colors['function']))
            self.highlighting_rules.append((re.compile(r'\bdef\s+(\w+)'), function_format))

            # Class names
            class_format = QTextCharFormat()
            class_format.setForeground(QColor(self.colors['class']))
            self.highlighting_rules.append((re.compile(r'\bclass\s+(\w+)'), class_format))

            # Decorators
            decorator_format = QTextCharFormat()
            decorator_format.setForeground(QColor(self.colors['decorator']))
            self.highlighting_rules.append((re.compile(r'@\w+'), decorator_format))

            # Numbers
            number_format = QTextCharFormat()
            number_format.setForeground(QColor(self.colors['number']))
            self.highlighting_rules.append((re.compile(r'\b\d+\.?\d*\b'), number_format))

        # JavaScript/TypeScript
        elif self.language in ['javascript', 'js', 'typescript', 'ts']:
            keywords = [
                'var', 'let', 'const', 'function', 'return', 'if', 'else',
                'for', 'while', 'do', 'switch', 'case', 'break', 'continue',
                'try', 'catch', 'finally', 'throw', 'new', 'this', 'typeof',
                'instanceof', 'in', 'of', 'class', 'extends', 'super', 'import',
                'export', 'default', 'async', 'await', 'yield', 'null', 'undefined',
                'true', 'false'
            ]

            keyword_format = QTextCharFormat()
            keyword_format.setForeground(QColor(self.colors['keyword']))
            keyword_format.setFontWeight(QFont.Weight.Bold)
            for word in keywords:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), keyword_format))

            # Strings
            string_format = QTextCharFormat()
            string_format.setForeground(QColor(self.colors['string']))
            self.highlighting_rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))
            self.highlighting_rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))
            self.highlighting_rules.append((re.compile(r'`[^`]*`'), string_format))

            # Comments
            comment_format = QTextCharFormat()
            comment_format.setForeground(QColor(self.colors['comment']))
            comment_format.setFontItalic(True)
            self.highlighting_rules.append((re.compile(r'//[^\n]*'), comment_format))
            self.highlighting_rules.append((re.compile(r'/\*.*?\*/', re.DOTALL), comment_format))

            # Numbers
            number_format = QTextCharFormat()
            number_format.setForeground(QColor(self.colors['number']))
            self.highlighting_rules.append((re.compile(r'\b\d+\.?\d*\b'), number_format))

        # Java
        elif self.language == 'java':
            keywords = [
                'abstract', 'assert', 'boolean', 'break', 'byte', 'case', 'catch',
                'char', 'class', 'const', 'continue', 'default', 'do', 'double',
                'else', 'enum', 'extends', 'final', 'finally', 'float', 'for',
                'goto', 'if', 'implements', 'import', 'instanceof', 'int', 'interface',
                'long', 'native', 'new', 'package', 'private', 'protected', 'public',
                'return', 'short', 'static', 'strictfp', 'super', 'switch', 'synchronized',
                'this', 'throw', 'throws', 'transient', 'try', 'void', 'volatile', 'while',
                'true', 'false', 'null'
            ]

            builtins = [
                'String', 'Integer', 'Boolean', 'Double', 'Float', 'Long', 'Short',
                'Byte', 'Character', 'Object', 'System', 'Math', 'List', 'ArrayList',
                'HashMap', 'HashSet', 'Map', 'Set', 'Collection', 'Arrays'
            ]

            # Keywords
            keyword_format = QTextCharFormat()
            keyword_format.setForeground(QColor(self.colors['keyword']))
            keyword_format.setFontWeight(QFont.Weight.Bold)
            for word in keywords:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), keyword_format))

            # Built-in classes
            builtin_format = QTextCharFormat()
            builtin_format.setForeground(QColor(self.colors['builtin']))
            for word in builtins:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), builtin_format))

            # Strings
            string_format = QTextCharFormat()
            string_format.setForeground(QColor(self.colors['string']))
            self.highlighting_rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))
            self.highlighting_rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))

            # Comments
            comment_format = QTextCharFormat()
            comment_format.setForeground(QColor(self.colors['comment']))
            comment_format.setFontItalic(True)
            self.highlighting_rules.append((re.compile(r'//[^\n]*'), comment_format))
            self.highlighting_rules.append((re.compile(r'/\*.*?\*/', re.DOTALL), comment_format))

            # Annotations
            decorator_format = QTextCharFormat()
            decorator_format.setForeground(QColor(self.colors['decorator']))
            self.highlighting_rules.append((re.compile(r'@\w+'), decorator_format))

            # Numbers
            number_format = QTextCharFormat()
            number_format.setForeground(QColor(self.colors['number']))
            self.highlighting_rules.append((re.compile(r'\b\d+\.?\d*[fFdDlL]?\b'), number_format))

        # C/C++
        elif self.language in ['c', 'cpp', 'c++']:
            keywords = [
                'int', 'void', 'char', 'float', 'double', 'long', 'short',
                'unsigned', 'signed', 'if', 'else', 'for', 'while', 'do',
                'switch', 'case', 'break', 'continue', 'return', 'struct',
                'typedef', 'enum', 'union', 'const', 'static', 'extern',
                'auto', 'register', 'volatile', 'sizeof', 'class', 'public',
                'private', 'protected', 'virtual', 'namespace', 'using',
                'template', 'typename', 'bool', 'true', 'false', 'nullptr'
            ]

            keyword_format = QTextCharFormat()
            keyword_format.setForeground(QColor(self.colors['keyword']))
            keyword_format.setFontWeight(QFont.Weight.Bold)
            for word in keywords:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), keyword_format))

            # Preprocessor directives
            preprocessor_format = QTextCharFormat()
            preprocessor_format.setForeground(QColor(self.colors['decorator']))
            self.highlighting_rules.append((re.compile(r'#\w+'), preprocessor_format))

            # Strings
            string_format = QTextCharFormat()
            string_format.setForeground(QColor(self.colors['string']))
            self.highlighting_rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))
            self.highlighting_rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))

            # Comments
            comment_format = QTextCharFormat()
            comment_format.setForeground(QColor(self.colors['comment']))
            comment_format.setFontItalic(True)
            self.highlighting_rules.append((re.compile(r'//[^\n]*'), comment_format))
            self.highlighting_rules.append((re.compile(r'/\*.*?\*/', re.DOTALL), comment_format))

            # Numbers
            number_format = QTextCharFormat()
            number_format.setForeground(QColor(self.colors['number']))
            self.highlighting_rules.append((re.compile(r'\b\d+\.?\d*\b'), number_format))

    def highlightBlock(self, text):
        """Apply syntax highlighting to a block of text"""
        for pattern, format in self.highlighting_rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), format)


class CodeBlockWidget(QWidget):
    """Code block with syntax highlighting, rendered in the same minimal,
    theme-driven frame as TableBlockWidget: a thin bordered box holding the
    scrollable code, with a footer that carries the language label plus
    Wrap / Copy / Hide chips and a resize grip. Expanded by default."""

    def __init__(self, language, code, theme=None, parent=None):
        super().__init__(parent)
        self.code = code
        self.language = language

        theme = theme or {}
        base     = theme.get('base', '#0D1117')
        elevated = theme.get('elevated', '#21262D')
        border   = theme.get('border', '#30363D')
        accent   = theme.get('accent', '#58A6FF')
        muted    = '#8B949E'
        text_col = '#E6EDF3'
        self._accent = accent

        self.is_expanded        = True
        self.is_resizing        = False
        self._manual_h          = False  # set once the user drags the resize grip
        self.resize_start_pos   = None
        self.resize_start_size  = None   # (width, height) of main_container at drag start
        self.min_height = 60
        self.max_height = 800
        self.min_width  = 300
        self.max_width  = 1200

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 6, 0, 0)
        root_layout.setSpacing(0)

        # ── Minimal frame (matches the table) ─────────────────────────────────
        self.main_container = QFrame()
        self.main_container.setObjectName("codeBlock")
        self.main_container.setStyleSheet(
            f"QFrame#codeBlock {{ background: {base}; border: 1px solid {border}; "
            f"border-radius: 8px; }}")
        self._frame = self.main_container
        container_layout = QVBoxLayout(self.main_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # ── Scrollable code area ──────────────────────────────────────────────
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("codeScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet(f"""
            QScrollArea#codeScrollArea {{
                background: transparent;
                border: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }}
            QScrollBar:horizontal {{ background: transparent; height: 7px; border: none; }}
            QScrollBar::handle:horizontal {{ background: {border}; border-radius: 3px; min-width: 24px; }}
            QScrollBar::handle:horizontal:hover {{ background: {accent}; }}
            QScrollBar:vertical {{ background: transparent; width: 7px; border: none; }}
            QScrollBar::handle:vertical {{ background: {border}; border-radius: 3px; min-height: 24px; }}
            QScrollBar::handle:vertical:hover {{ background: {accent}; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ border: none; background: none; height: 0; width: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
            QScrollBar::corner {{ background: transparent; }}
        """)

        code_container = QWidget()
        code_container.setStyleSheet("QWidget { background: transparent; border: none; }")
        code_container_layout = QVBoxLayout(code_container)
        code_container_layout.setContentsMargins(16, 12, 16, 12)
        code_container_layout.setSpacing(0)

        self.code_editor = QTextEdit()
        self.code_editor.setObjectName("codeEditor")
        self.code_editor.setPlainText(code)
        self.code_editor.setReadOnly(True)
        # Read-only viewer: never a drop target — without this a file dragged
        # over it shows the forbidden cursor instead of falling through to the
        # chat surface's drop handler.
        self.code_editor.setAcceptDrops(False)
        self.code_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.code_editor.setFrameShape(QTextEdit.Shape.NoFrame)
        # Disable the QTextEdit's own scrollbars — QScrollArea handles all scrolling
        self.code_editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.code_editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.code_editor.setStyleSheet(f"""
            QTextEdit#codeEditor {{
                background: transparent; border: none;
                color: {text_col};
                selection-background-color: rgba(88, 166, 255, 0.25);
            }}
        """)

        font = QFont('Consolas', 10)
        if not font.exactMatch():
            font = QFont('Monaco', 10)
        if not font.exactMatch():
            font = QFont('Courier New', 10)
        self.code_editor.setFont(font)

        self.code_editor.viewport().installEventFilter(self)

        self.highlighter = CodeSyntaxHighlighter(self.code_editor.document(), language)
        code_container_layout.addWidget(self.code_editor)
        self.scroll_area.setWidget(code_container)

        line_count = len(code.split('\n'))
        calculated_height = min(max(line_count * 17 + 24, self.min_height), self.max_height)
        self.scroll_area.setFixedHeight(calculated_height)
        container_layout.addWidget(self.scroll_area)

        # ── FLOATING overlays (2026-07 redesign): the old in-flow footer was a
        #    solid strip blocking the last code lines. LANG chip = solid fitted
        #    tag bottom-left (always readable over code); Wrap/Copy/Hide/Resize
        #    = translucent chips bottom-right, floating over the frame. ──────
        display_lang = language.strip().upper() if language and language.strip().lower() not in ('', 'text') else 'TEXT'
        # Chips are TRANSPARENT at rest — they used to sit on solid/dark pills
        # that read as a bar blocking the content beneath. The translucent dark
        # pill returns on hover (and hover-while-checked) so a pointed-at
        # control is always readable over any code.
        self._lang_chip = QLabel(display_lang, self.main_container)
        self._lang_chip.setStyleSheet(
            f"QLabel {{ background: transparent; color: {muted}; font-size: 10px; "
            f"font-weight: 700; border: none; border-radius: 4px; "
            f"padding: 2px 8px; }}")
        self._lang_chip.adjustSize()

        self._chip_style = f"""
            QPushButton {{
                background: transparent;
                color: {accent};
                font-size: 10px; font-weight: 600;
                padding: 2px 9px;
                border: 1px solid transparent;
                border-radius: 4px;
            }}
            QPushButton:hover {{ border-color: {accent}; background: rgba(33, 38, 45, 0.92); }}
            QPushButton:checked {{ border-color: {accent}; background: transparent; }}
            QPushButton:checked:hover {{ background: rgba(33, 38, 45, 0.92); }}
        """

        self._controls_bar = QWidget(self.main_container)
        self._controls_bar.setObjectName("codeControls")
        self._controls_bar.setStyleSheet("QWidget#codeControls { background: transparent; }")
        _cb_lay = QHBoxLayout(self._controls_bar)
        _cb_lay.setContentsMargins(0, 0, 0, 0)
        _cb_lay.setSpacing(6)

        self.wrap_btn = QPushButton("↵ Wrap")
        self.wrap_btn.setObjectName("codeWrapBtn")
        self.wrap_btn.setCheckable(True)
        self.wrap_btn.setChecked(False)
        self.wrap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wrap_btn.setStyleSheet(self._chip_style)
        self.wrap_btn.clicked.connect(self._toggle_wrap)
        _cb_lay.addWidget(self.wrap_btn)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("codeCopyBtn")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setStyleSheet(self._chip_style)
        self.copy_btn.clicked.connect(self.copy_code)
        _cb_lay.addWidget(self.copy_btn)

        self.toggle_btn = QPushButton("Hide")
        self.toggle_btn.setObjectName("codeToggleBtn")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet(self._chip_style)
        self.toggle_btn.clicked.connect(self.toggle_expand)
        _cb_lay.addWidget(self.toggle_btn)

        self.corner_grip = QLabel("⤡  Resize")
        self.corner_grip.setStyleSheet(f"""
            QLabel {{
                background: transparent;
                color: {accent};
                font-size: 10px; font-weight: 700;
                padding: 2px 8px;
                border: 1px solid transparent;
                border-radius: 4px;
            }}
            QLabel:hover {{ border-color: {accent}; background: rgba(33, 38, 45, 0.92); }}
        """)
        self.corner_grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.corner_grip.mousePressEvent   = self._corner_press
        self.corner_grip.mouseMoveEvent    = self._corner_move
        self.corner_grip.mouseReleaseEvent = self._corner_release
        _cb_lay.addWidget(self.corner_grip)
        self._controls_bar.adjustSize()

        # Bottom padding inside the scroll content keeps the LAST code line
        # scrollable clear of the floating chips.
        code_container_layout.setContentsMargins(16, 12, 16, 34)

        root_layout.addWidget(self.main_container)

        # Re-fit height once the editor is laid out — the line-count guess made
        # during construction under-sizes the visible area.
        QTimer.singleShot(0, self._fit_height)
        QTimer.singleShot(0, self._position_overlays)

    def _position_overlays(self):
        """Pin the lang chip bottom-left and the control chips bottom-right of
        the frame (floating — they occupy no layout space)."""
        try:
            f = self.main_container
            if self.is_expanded:
                y = f.height() - self._controls_bar.height() - 8
                self._lang_chip.move(10, f.height() - self._lang_chip.height() - 8)
            else:
                y = max(4, (f.height() - self._controls_bar.height()) // 2)
                self._lang_chip.move(10, max(4, (f.height() - self._lang_chip.height()) // 2))
            self._controls_bar.adjustSize()
            self._controls_bar.move(f.width() - self._controls_bar.width() - 10, y)
            self._lang_chip.raise_()
            self._controls_bar.raise_()
        except RuntimeError:
            pass

    def _fit_height(self):
        """Size the scroll area to the code's actual document height. Skipped
        after a manual grip resize."""
        if getattr(self, '_manual_h', False) or self.is_resizing:
            return
        try:
            doc_h = int(self.code_editor.document().size().height())
            h = min(max(doc_h + 46, self.min_height), self.max_height)
            self.scroll_area.setFixedHeight(h)
            self._position_overlays()
        except RuntimeError:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_height)
        self._position_overlays()

    # ── Ctrl+Scroll ───────────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self.code_editor.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                chat_win = self._find_chat_window()
                if chat_win:
                    if event.angleDelta().y() > 0:
                        chat_win.zoom_in()
                    else:
                        chat_win.zoom_out()
                return True
        return super().eventFilter(obj, event)

    def _find_chat_window(self):
        p = self.parent()
        while p:
            if p.__class__.__name__ == 'ChatWindow':
                return p
            p = p.parent()
        return None

    # ── Toggle (collapse the code; the footer chips stay visible) ─────────────

    def toggle_expand(self):
        self.is_expanded = not self.is_expanded
        if self.is_expanded:
            self.scroll_area.show()
            self.corner_grip.show()
            self.toggle_btn.setText("Hide")
            self.main_container.setMinimumHeight(0)
        else:
            self.scroll_area.hide()
            self.corner_grip.hide()
            self.toggle_btn.setText("Show")
            # Keep a slim host strip so the floating chips (Show!) survive
            # the collapse — without it the frame shrinks to nothing.
            self.main_container.setMinimumHeight(40)
        QTimer.singleShot(0, self._position_overlays)

    # ── Corner resize (width + height) ───────────────────────────────────────
    # We resize main_container width so the whole block (scroll + footer)
    # moves together, rather than just the inner scroll area.

    def _corner_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_resizing     = True
            self.resize_start_pos  = event.globalPosition()
            self.resize_start_size = (self.main_container.width(),
                                      self.scroll_area.height())
            event.accept()

    def _effective_max_width(self) -> int:
        """Upper bound for manual resize. Never wider than the visible bubble —
        otherwise the block (and its resize grip) gets dragged off-screen and
        becomes impossible to grab back. Falls back to self.max_width."""
        avail = self.max_width
        chat = self._find_chat_window()
        if chat is not None and hasattr(chat, '_bubble_max_width'):
            try:
                # bubble inner width minus content_wrapper margins + a little slack
                avail = min(avail, chat._bubble_max_width() - 28)
            except Exception:
                pass
        # Floor low enough to fit a narrow bubble — the widget must never be
        # forced wider than the space it has (min_width would overflow it).
        return max(120, int(avail))

    def clamp_width(self):
        """Re-clamp a manually-resized block so it never exceeds the visible
        bubble — e.g. after the window is shrunk. No-op if already within range."""
        try:
            if self.main_container.width() > 0:
                max_w = self._effective_max_width()
                if self.main_container.width() > max_w:
                    self.main_container.setFixedWidth(max_w)
        except Exception:
            pass

    def _corner_move(self, event):
        if self.is_resizing:
            self._manual_h = True
            dx    = event.globalPosition().x() - self.resize_start_pos.x()
            dy    = event.globalPosition().y() - self.resize_start_pos.y()
            max_w = self._effective_max_width()
            new_w = max(self.min_width,  min(self.resize_start_size[0] + dx, max_w))
            new_h = max(self.min_height, min(self.resize_start_size[1] + dy, self.max_height))
            # Fix the outer container width so header + scroll + bar all follow
            self.main_container.setFixedWidth(int(new_w))
            self.scroll_area.setFixedHeight(int(new_h))
            event.accept()

    def _corner_release(self, event):
        self.is_resizing = False
        event.accept()

    # ── Copy ─────────────────────────────────────────────────────────────────

    def _toggle_wrap(self):
        """Toggle word-wrap on this code block's editor."""
        if self.wrap_btn.isChecked():
            self.code_editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.wrap_btn.setText("↵ Wrap ✓")
        else:
            self.code_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.wrap_btn.setText("↵ Wrap")

    def copy_code(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.code)
        self.copy_btn.setText("Copied ✓")
        self.copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(52, 168, 83, 0.14);
                color: #3FB950;
                font-size: 10px; font-weight: 600;
                padding: 2px 9px;
                border: 1px solid rgba(52, 168, 83, 0.40);
                border-radius: 4px;
            }}
        """)
        QTimer.singleShot(ANIM_COPY_FEEDBACK_MS, self.reset_copy_button)

    def reset_copy_button(self):
        self.copy_btn.setText("Copy")
        self.copy_btn.setStyleSheet(self._chip_style)


class TableBlockWidget(QWidget):
    """Renders a markdown pipe-table as horizontally-scrollable HTML so wide
    tables are never truncated by the message-bubble width cap. Tall tables
    scroll vertically; wide tables scroll horizontally — content is never cut."""

    _MAX_HEIGHT = 460

    def __init__(self, table_md, theme, render_fn, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(0)

        base     = theme.get('base', '#0D1117')
        elevated = theme.get('elevated', '#21262D')
        border   = theme.get('border', '#30363D')
        accent   = theme.get('accent', '#58A6FF')
        text_col = '#E6EDF3'

        self.is_resizing = False
        self._manual_h = False        # set once the user drags the resize grip
        self.min_width, self.max_width = 300, 1200
        self.min_height, self.max_height = 44, 800

        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: {base}; border: 1px solid {border}; border-radius: 8px; }}")
        self._frame = frame
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)

        # markdown2 → <table>…; add visible borders (Qt rich text honours attrs)
        html = render_fn(table_md)
        html = html.replace('<table>', '<table border="1" cellspacing="0" cellpadding="6">')
        styled = (
            "<style>"
            f"table {{ border-collapse: collapse; color: {text_col}; }}"
            f"th {{ background-color: {elevated}; color: {accent}; }}"
            f"th, td {{ border: 1px solid {border}; padding: 4px 10px; }}"
            "</style>" + html
        )

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setAcceptDrops(False)   # let file drags fall through to the chat surface
        self.view.setHtml(styled)
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)  # wrap to fit by default
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.view.setFrameShape(QTextEdit.Shape.NoFrame)
        self.view.setStyleSheet(f"""
            QTextEdit {{ background: transparent; border: none; color: {text_col}; }}
            QScrollBar:horizontal {{ background: transparent; height: 7px; border: none; }}
            QScrollBar::handle:horizontal {{ background: {border}; border-radius: 3px; min-width: 24px; }}
            QScrollBar:vertical {{ background: transparent; width: 7px; border: none; }}
            QScrollBar::handle:vertical {{ background: {border}; border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: none; background: none; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        """)

        # Size height to content (capped); overflow scrolls instead of clipping.
        doc = self.view.document()
        doc.setDocumentMargin(8)
        content_h = int(doc.size().height()) + 6
        self.view.setFixedHeight(min(max(content_h, 44), self._MAX_HEIGHT))

        fl.addWidget(self.view)

        # ── Resize grip: FLOATING overlay chip (bottom-right, translucent,
        #    hover-revealed) — the old in-flow footer bar read as a solid
        #    strip sitting over the last table row. ─────────────────────────
        self.corner_grip = QLabel("⤡  Resize", frame)
        self.corner_grip.setStyleSheet(f"""
            QLabel {{
                background: transparent;
                color: {accent};
                font-size: 10px; font-weight: 700;
                padding: 2px 8px;
                border: 1px solid transparent;
                border-radius: 4px;
            }}
            QLabel:hover {{ background: rgba(33, 38, 45, 0.92); border-color: {accent}; }}
        """)
        self.corner_grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.corner_grip.mousePressEvent   = self._corner_press
        self.corner_grip.mouseMoveEvent    = self._corner_move
        self.corner_grip.mouseReleaseEvent = self._corner_release
        self.corner_grip.adjustSize()
        self.corner_grip.hide()   # revealed on hover (enterEvent below)
        frame.installEventFilter(self)

        root.addWidget(frame)

        # Re-fit once the widget has a real laid-out width — the height computed
        # during construction (before layout) under-sizes wrapped tables.
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        """Size the table view to its content at the CURRENT width. Skipped once
        the user has manually resized it."""
        if getattr(self, '_manual_h', False):
            return
        try:
            doc = self.view.document()
            w = self.view.viewport().width()
            if w > 0:
                doc.setTextWidth(w)
            content_h = int(doc.size().height()) + 12
            # A visible horizontal scrollbar eats viewport height — add its
            # thickness or the last row hides under it.
            hbar = self.view.horizontalScrollBar()
            if hbar is not None and hbar.maximum() > 0:
                content_h += hbar.sizeHint().height()
            self.view.setFixedHeight(min(max(content_h, self.min_height), self._MAX_HEIGHT))
        except RuntimeError:
            pass

    def _position_grip(self):
        """Pin the floating grip to the frame's bottom-right corner."""
        try:
            g, f = self.corner_grip, self._frame
            g.move(f.width() - g.width() - 8, f.height() - g.height() - 6)
            g.raise_()
        except RuntimeError:
            pass

    def eventFilter(self, obj, event):
        if obj is getattr(self, '_frame', None):
            et = event.type()
            if et == QEvent.Type.Enter:
                self._position_grip()
                self.corner_grip.show()
            elif et == QEvent.Type.Leave:
                if not self.is_resizing:
                    self.corner_grip.hide()
            elif et == QEvent.Type.Resize:
                self._position_grip()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_height)
        self._position_grip()

    # ── Corner resize (width + height), clamped to the visible bubble ─────────

    def _find_chat_window(self):
        p = self.parent()
        while p:
            if p.__class__.__name__ == 'ChatWindow':
                return p
            p = p.parent()
        return None

    def _effective_max_width(self) -> int:
        """Never wider than the visible bubble, so the grip can't go off-screen."""
        avail = self.max_width
        chat = self._find_chat_window()
        if chat is not None and hasattr(chat, '_bubble_max_width'):
            try:
                avail = min(avail, chat._bubble_max_width() - 28)
            except Exception:
                pass
        # Floor low enough to fit a narrow bubble — the widget must never be
        # forced wider than the space it has (min_width would overflow it).
        return max(120, int(avail))

    def clamp_width(self):
        """Re-clamp a manually-resized table after the window shrinks."""
        try:
            if self._frame.width() > 0:
                max_w = self._effective_max_width()
                if self._frame.width() > max_w:
                    self._frame.setFixedWidth(max_w)
        except Exception:
            pass

    def _corner_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_resizing = True
            self.resize_start_pos  = event.globalPosition()
            self.resize_start_size = (self._frame.width(), self.view.height())
            event.accept()

    def _corner_move(self, event):
        if self.is_resizing:
            self._manual_h = True
            dx = event.globalPosition().x() - self.resize_start_pos.x()
            dy = event.globalPosition().y() - self.resize_start_pos.y()
            max_w = self._effective_max_width()
            new_w = max(self.min_width,  min(self.resize_start_size[0] + dx, max_w))
            new_h = max(self.min_height, min(self.resize_start_size[1] + dy, self.max_height))
            self._frame.setFixedWidth(int(new_w))
            self.view.setFixedHeight(int(new_h))
            event.accept()

    def _corner_release(self, event):
        self.is_resizing = False
        event.accept()


