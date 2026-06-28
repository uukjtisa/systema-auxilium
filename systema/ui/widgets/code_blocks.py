"""
systema/ui/widgets/code_blocks.py
Code & table block widgets + syntax highlighter.
Extracted verbatim from chat_window.py.
"""
import re
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
                             QPushButton, QScrollArea, QTextEdit, QApplication,
                             QSizePolicy)
from PyQt6.QtCore import Qt, QTimer
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
    """Widget for displaying code blocks with syntax highlighting and resize handles"""

    # ── Obsidian Blue palette constants ───────────────────────────────────────
    _CB_BASE    = "#0D1117"
    _CB_HEADER  = "#161B22"
    _CB_BORDER  = "rgba(88, 166, 255, 0.18)"
    _CB_ACCENT  = "#58A6FF"
    _CB_ACCENT2 = "rgba(88, 166, 255, 0.12)"
    _CB_ACCENT3 = "rgba(88, 166, 255, 0.28)"
    _CB_TEXT    = "#E6EDF3"
    _CB_MUTED   = "#8B949E"
    _CB_SB      = "#21262D"

    def __init__(self, language, code, parent=None):
        super().__init__(parent)
        self.code = code
        self.language = language

        self.is_expanded        = False
        self.is_resizing        = False
        self.resize_start_pos   = None
        self.resize_start_size  = None   # (width, height) of main_container at drag start
        self.min_height = 60
        self.max_height = 800
        self.min_width  = 300
        self.max_width  = 1200

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 6, 0, 0)
        root_layout.setSpacing(0)

        # ── Main container ────────────────────────────────────────────────────
        self.main_container = QFrame()
        self.main_container.setObjectName("codeBlock")
        self.main_container.setStyleSheet(f"""
            QFrame#codeBlock {{
                background: {self._CB_BASE};
                border: 1px solid {self._CB_BORDER};
                border-radius: 10px;
            }}
        """)
        container_layout = QVBoxLayout(self.main_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # ── Header (taller so buttons are never clipped) ──────────────────────
        header = QFrame()
        header.setObjectName("codeHeader")
        header.setFixedHeight(54)
        header.setStyleSheet(f"""
            QFrame#codeHeader {{
                background-color: {self._CB_HEADER};
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                border-bottom: 1px solid {self._CB_BORDER};
            }}
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 12, 0)
        header_layout.setSpacing(6)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Traffic-light dots
        for _color in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QFrame()
            dot.setFixedSize(11, 11)
            dot.setStyleSheet(f"QFrame {{ background: {_color}; border-radius: 5px; border: none; }}")
            header_layout.addWidget(dot)

        header_layout.addSpacing(10)

        # Language label — always visible
        display_lang = language.strip().upper() if language and language.strip().lower() not in ('', 'text') else 'TEXT'
        lang_label = QLabel(display_lang)
        lang_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        lang_label.setStyleSheet(f"""
            QLabel {{
                background: transparent;
                color: {self._CB_MUTED};
                font-size: 11px;
                font-weight: 700;
                border: none;
                padding: 0 4px;
            }}
        """)
        header_layout.addWidget(lang_label)
        header_layout.addStretch()

        _btn_style = f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
                padding: 4px 11px;
                font-size: 11px;
                font-weight: 500;
                color: {self._CB_MUTED};
                min-width: 64px;
            }}
            QPushButton:hover {{
                background-color: {self._CB_ACCENT2};
                border-color: {self._CB_ACCENT3};
                color: {self._CB_ACCENT};
            }}
            QPushButton:pressed {{
                background-color: {self._CB_ACCENT3};
            }}
        """

        self.toggle_btn = QPushButton("▶  Show")
        self.toggle_btn.setObjectName("codeToggleBtn")
        self.toggle_btn.setStyleSheet(_btn_style)
        self.toggle_btn.clicked.connect(self.toggle_expand)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout.addWidget(self.toggle_btn)

        self.wrap_btn = QPushButton("↵  Wrap")
        self.wrap_btn.setObjectName("codeWrapBtn")
        self.wrap_btn.setStyleSheet(_btn_style)
        self.wrap_btn.setCheckable(True)
        self.wrap_btn.setChecked(False)
        self.wrap_btn.clicked.connect(self._toggle_wrap)
        self.wrap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout.addWidget(self.wrap_btn)

        self.copy_btn = QPushButton("📋  Copy")
        self.copy_btn.setObjectName("codeCopyBtn")
        self.copy_btn.setStyleSheet(_btn_style)
        self.copy_btn.clicked.connect(self.copy_code)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout.addWidget(self.copy_btn)

        container_layout.addWidget(header)

        # ── Scrollable code area ──────────────────────────────────────────────
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("codeScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet(f"""
            QScrollArea#codeScrollArea {{
                background: {self._CB_BASE};
                border: none;
            }}
            QScrollBar:horizontal {{
                background: transparent; height: 6px; border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {self._CB_SB}; border-radius: 3px; min-width: 20px;
            }}
            QScrollBar::handle:horizontal:hover {{ background: #30363D; }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {self._CB_SB}; border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #30363D; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ border: none; background: none; height: 0; width: 0; }}
            QScrollBar::corner {{ background: transparent; }}
        """)

        code_container = QWidget()
        code_container.setStyleSheet(f"QWidget {{ background: {self._CB_BASE}; border: none; }}")
        code_container_layout = QVBoxLayout(code_container)
        code_container_layout.setContentsMargins(16, 12, 16, 12)
        code_container_layout.setSpacing(0)

        self.code_editor = QTextEdit()
        self.code_editor.setObjectName("codeEditor")
        self.code_editor.setPlainText(code)
        self.code_editor.setReadOnly(True)
        self.code_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.code_editor.setFrameShape(QTextEdit.Shape.NoFrame)
        # Disable the QTextEdit's own scrollbars — QScrollArea handles all scrolling
        self.code_editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.code_editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.code_editor.setStyleSheet(f"""
            QTextEdit#codeEditor {{
                background: transparent; border: none;
                color: {self._CB_TEXT};
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
        self.scroll_area.hide()
        container_layout.addWidget(self.scroll_area)

        # ── Resize grip — inside the container, bottom-right ──────────────────
        # A transparent footer row that floats at the bottom of the black box.
        grip_row = QWidget()
        grip_row.setObjectName("codeGripRow")
        grip_row.setStyleSheet("QWidget#codeGripRow { background: transparent; }")
        grip_row.setFixedHeight(28)
        grip_row.hide()
        grip_row_layout = QHBoxLayout(grip_row)
        grip_row_layout.setContentsMargins(0, 0, 8, 4)
        grip_row_layout.setSpacing(0)
        grip_row_layout.addStretch()

        self.corner_grip = QLabel("⤡  Resize")
        self.corner_grip.setStyleSheet(f"""
            QLabel {{
                background: rgba(88,166,255,0.08);
                color: rgba(88,166,255,0.55);
                font-size: 10px;
                font-weight: 700;
                padding: 2px 8px;
                border: 1px solid rgba(88,166,255,0.20);
                border-radius: 4px;
            }}
            QLabel:hover {{
                background: rgba(88,166,255,0.18);
                color: {self._CB_ACCENT};
                border-color: rgba(88,166,255,0.45);
            }}
        """)
        self.corner_grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.corner_grip.mousePressEvent   = self._corner_press
        self.corner_grip.mouseMoveEvent    = self._corner_move
        self.corner_grip.mouseReleaseEvent = self._corner_release
        grip_row_layout.addWidget(self.corner_grip)

        self._grip_row = grip_row
        container_layout.addWidget(grip_row)
        root_layout.addWidget(self.main_container)

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

    # ── Toggle ────────────────────────────────────────────────────────────────

    def toggle_expand(self):
        self.is_expanded = not self.is_expanded
        if self.is_expanded:
            self.scroll_area.show()
            self._grip_row.show()
            self.toggle_btn.setText("▼  Hide")
        else:
            self.scroll_area.hide()
            self._grip_row.hide()
            self.toggle_btn.setText("▶  Show")

    # ── Corner resize (width + height) ───────────────────────────────────────
    # We resize main_container width so the whole block (header + scroll + bar)
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
        return max(self.min_width, int(avail))

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
            self.wrap_btn.setText("↵  Wrap ✓")
        else:
            self.code_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.wrap_btn.setText("↵  Wrap")

    def copy_code(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.code)
        self.copy_btn.setText("✓  Copied!")
        self.copy_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(52, 168, 83, 0.12);
                border: 1px solid rgba(52, 168, 83, 0.3);
                border-radius: 5px;
                padding: 4px 11px;
                font-size: 11px;
                min-width: 64px;
                color: #3FB950;
            }}
        """)
        QTimer.singleShot(ANIM_COPY_FEEDBACK_MS, self.reset_copy_button)

    def reset_copy_button(self):
        self.copy_btn.setText("📋  Copy")
        self.copy_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
                padding: 4px 11px;
                font-size: 11px;
                font-weight: 500;
                min-width: 64px;
                color: {self._CB_MUTED};
            }}
            QPushButton:hover {{
                background-color: {self._CB_ACCENT2};
                border-color: {self._CB_ACCENT3};
                color: {self._CB_ACCENT};
            }}
            QPushButton:pressed {{
                background-color: {self._CB_ACCENT3};
            }}
        """)


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
        self.view.setHtml(styled)
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)   # wide → horizontal scroll
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

        # ── Footer: wrap toggle (left) + resize grip (right) ──────────────────
        # Both styled from the table's own theme colours (accent / border).
        grip_row = QWidget()
        grip_row.setStyleSheet("background: transparent;")
        grip_row.setFixedHeight(28)
        grl = QHBoxLayout(grip_row)
        grl.setContentsMargins(8, 0, 8, 4)
        grl.setSpacing(6)

        _chip_style = f"""
            QPushButton {{
                background: {elevated};
                color: {accent};
                font-size: 10px; font-weight: 600;
                padding: 2px 9px;
                border: 1px solid {border};
                border-radius: 4px;
            }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton:checked {{ border-color: {accent}; background: {base}; }}
        """
        self.wrap_btn = QPushButton("↵ Wrap")
        self.wrap_btn.setCheckable(True)
        self.wrap_btn.setChecked(False)
        self.wrap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wrap_btn.setStyleSheet(_chip_style)
        self.wrap_btn.clicked.connect(self._toggle_wrap)
        grl.addWidget(self.wrap_btn)

        grl.addStretch()

        self.corner_grip = QLabel("⤡  Resize")
        self.corner_grip.setStyleSheet(f"""
            QLabel {{
                background: {elevated};
                color: {accent};
                font-size: 10px; font-weight: 700;
                padding: 2px 8px;
                border: 1px solid {border};
                border-radius: 4px;
            }}
            QLabel:hover {{ border-color: {accent}; }}
        """)
        self.corner_grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.corner_grip.mousePressEvent   = self._corner_press
        self.corner_grip.mouseMoveEvent    = self._corner_move
        self.corner_grip.mouseReleaseEvent = self._corner_release
        grl.addWidget(self.corner_grip)
        fl.addWidget(grip_row)

        root.addWidget(frame)

    def _toggle_wrap(self):
        """Toggle between horizontal-scroll (NoWrap, default) and wrap-to-fit —
        same behaviour as the code-block wrap button."""
        if self.wrap_btn.isChecked():
            self.view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.wrap_btn.setText("↵ Wrap ✓")
        else:
            self.view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.wrap_btn.setText("↵ Wrap")

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
        return max(self.min_width, int(avail))

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


