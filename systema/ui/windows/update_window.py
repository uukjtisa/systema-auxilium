"""
systema/ui/windows/update_window.py
UpdateWindow - review-and-select self-update UI (Settings > System).

Built on BaseWindow's shared shell (custom title bar + button styles). Talks to
controller.updater_service (UpdaterService) purely over Qt signals so all
network/disk work stays off the GUI thread. Flow:

    Check  ->  review the file list + per-file diff  ->  resolve conflicts
    inline (take/keep per hunk, Confirm the file)  ->  Apply.

Conflicts are resolved IN THIS WINDOW (no separate Manage dialog): clicking a
CONFLICT file turns the right pane into a hunk editor; Confirm flips the row to
DECIDED and stores the resolved text; Apply passes those resolutions straight
into the one apply path (so a resolved conflict actually applies and never
re-appears). No emojis anywhere - clean monochrome glyphs / text only.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame,
                             QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QRadioButton, QSplitter,
                             QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from systema.common.logger import _make_logger
from systema.ui import theme as _theme
from systema.ui.base_window import BaseWindow
from systema.updater import messages as upd_messages
from systema.updater.hunks import ReviewSession, FileReview

log = _make_logger("UpdateWindow")

# Per-change-type accent colours + a short tag shown in the list.
_CHANGE_STYLE = {
    "added":    ("#6fcf7f", "NEW"),
    "modified": (None,      "MOD"),   # None -> use palette text
    "merged":   ("#5fb0e6", "MERGE"),
    "conflict": ("#e0655a", "CONFLICT"),
    "deleted":  ("#d98b3f", "DEL"),
    "decided":  ("#7bd88f", "DECIDED"),   # a conflict the user has resolved
}

# Change types that carry a reviewable TEXTUAL difference (auto-selected on
# refresh + highlighted). Conflicts are text too but stay opt-in.
_TEXTDIFF_CHANGES = ("added", "modified", "merged")

# Origin colours for the 3-way review. Four distinct hues so you can tell, at a
# glance, what the UPDATE changed vs what YOU changed locally.
_REVIEW_STYLE = {
    "same":            ("text",             "transparent",             " "),
    "update_add":      ("#c6ecc6",          "rgba(80,200,120,0.17)",   "+"),
    "update_del":      ("#efb1ab",          "rgba(224,90,80,0.17)",    "-"),
    "local_add":       ("#bfe0ff",          "rgba(90,160,230,0.17)",   "+"),
    "local_del":       ("#d8c2ff",          "rgba(150,110,230,0.20)",  "-"),
    "conflict_marker": ("accent",           "rgba(224,160,80,0.18)",   " "),
    "conflict_local":  ("#bfe0ff",          "rgba(90,160,230,0.14)",   " "),
    "conflict_base":   ("muted",            "rgba(128,128,128,0.14)",  " "),
    "conflict_remote": ("#c6ecc6",          "rgba(80,200,120,0.14)",   " "),
    "hunk":            ("accent",           "rgba(90,160,230,0.16)",   " "),
    "header":          ("muted",            "transparent",             " "),
}

_LEGEND = [
    ("update_add", "added by update"),
    ("update_del", "removed by update"),
    ("local_add",  "added locally"),
    ("local_del",  "removed locally"),
]

# Height (px) of the Files / Review header rows. Pinned equal on both panes.
_HDR_HEIGHT = 36

# Extra item roles.
_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_IS_CHANGE = Qt.ItemDataRole.UserRole + 1   # True => textual-diff file
_ROLE_SENSITIVE = Qt.ItemDataRole.UserRole + 2   # True => in a user-owned data folder
_ROLE_PROTECTED_RISKY = Qt.ItemDataRole.UserRole + 3  # True => protected AND overwrites/deletes
_ROLE_SETTLED = Qt.ItemDataRole.UserRole + 4     # True => user settled this file
_ROLE_CHANGE = Qt.ItemDataRole.UserRole + 5      # the change value string (e.g. "conflict")
_ROLE_DECIDED = Qt.ItemDataRole.UserRole + 6     # True => a conflict the user has Confirmed

_SENSITIVE_COLOUR = "#e0a24e"

# File-list filter modes.
_FILTERS = [
    ("All changes",          "all"),
    ("Unresolved conflicts", "unresolved"),
    ("Conflicts",            "conflict"),
    ("Decided",              "decided"),
    ("Protected",            "protected"),
    ("Settled",              "settled"),
]


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ReviewPane(QWidget):
    """Right-hand review surface with two modes over the SAME file:
      * diff mode  — read-only coloured 3-way diff (non-conflict / decided);
      * editor mode — per-hunk Take update / Keep mine (+ free-text Edit),
        whole-file quick actions, and Confirm, for an unresolved conflict.
    Embeddable both in the main window and the Expand pop-out; both share the
    same FileReview objects so edits stay in sync."""

    confirmed = pyqtSignal(str)   # emits the path when a file is Confirmed

    _DECISION_LABEL = {"local": "Keep mine", "update": "Take update", "edited": "Edited"}

    def __init__(self, palette: dict, resolve_colour, parent=None):
        super().__init__(parent)
        self.p = palette
        self._resolve_colour = resolve_colour
        self._path = None
        self._review: FileReview | None = None
        self._tagged = None
        self._wrap = False
        self._hunk_i = 0
        self._editing = False
        self._build()

    # ── layout ────────────────────────────────────────────────────────────
    def _build(self):
        p = self.p
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hdr.setFixedHeight(_HDR_HEIGHT)
        h = QHBoxLayout(hdr)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.title_lbl = QLabel("Review")
        self.title_lbl.setStyleSheet(f"color: {p['text']}; font-size: 12px; background: transparent;")
        h.addWidget(self.title_lbl)

        # Hunk navigation (editor mode only).
        self.prev_btn = QPushButton("‹")
        self.next_btn = QPushButton("›")
        for b in (self.prev_btn, self.next_btn):
            b.setFixedWidth(26)
            b.setStyleSheet(self._btn())
            b.setVisible(False)
        self.prev_btn.clicked.connect(lambda: self._step_hunk(-1))
        self.next_btn.clicked.connect(lambda: self._step_hunk(+1))
        self.hunk_lbl = QLabel("")
        self.hunk_lbl.setStyleSheet(f"color: {p['muted']}; font-size: 11px; background: transparent;")
        h.addStretch()
        h.addWidget(self.prev_btn)
        h.addWidget(self.hunk_lbl)
        h.addWidget(self.next_btn)
        self.wrap_check = QCheckBox("Wrap text")
        self.wrap_check.setStyleSheet(
            f"QCheckBox {{ color: {p['muted']}; font-size: 11px; background: transparent; }}")
        self.wrap_check.toggled.connect(self._on_wrap)
        h.addWidget(self.wrap_check)
        lay.addWidget(hdr)

        # Diff view (read-only) OR editor stack.
        self.stack = QStackedWidget()

        self.diff_view = QTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.diff_view.setPlaceholderText("Select a file to review the exact changes.")
        self.diff_view.setStyleSheet(self._edit_style())
        self.stack.addWidget(self.diff_view)                     # index 0 = diff

        self._editor_widget = self._build_editor()
        self.stack.addWidget(self._editor_widget)                # index 1 = editor
        lay.addWidget(self.stack, stretch=1)

    def _build_editor(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # Whole-file quick actions.
        wf = QHBoxLayout()
        wf.setSpacing(6)
        self.take_all_btn = QPushButton("Take all update")
        self.take_all_btn.setStyleSheet(self._btn())
        self.take_all_btn.clicked.connect(lambda: self._set_all("update"))
        self.keep_all_btn = QPushButton("Keep all mine")
        self.keep_all_btn.setStyleSheet(self._btn())
        self.keep_all_btn.clicked.connect(lambda: self._set_all("local"))
        wf.addWidget(self.take_all_btn)
        wf.addWidget(self.keep_all_btn)
        wf.addStretch()
        lay.addLayout(wf)

        # Per-hunk YOURS / UPDATE detail.
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.detail.setStyleSheet(self._edit_style())
        lay.addWidget(self.detail, stretch=1)

        # Free-text editor (Edit mode).
        self.free_editor = QTextEdit()
        self.free_editor.setStyleSheet(self._edit_style())
        self.free_editor.hide()
        self.free_editor.textChanged.connect(self._on_free_edit)
        lay.addWidget(self.free_editor, stretch=1)

        # Per-hunk decision row.
        dr = QHBoxLayout()
        dr.setSpacing(8)
        self.take_radio = QRadioButton("Take update")
        self.keep_radio = QRadioButton("Keep mine")
        self._radio_group = QButtonGroup(w)
        self._radio_group.addButton(self.take_radio)
        self._radio_group.addButton(self.keep_radio)
        for r in (self.take_radio, self.keep_radio):
            r.setStyleSheet(f"QRadioButton {{ color: {self.p['text']}; font-size: 11px; }}")
        self.take_radio.toggled.connect(self._on_radio)
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setStyleSheet(self._btn())
        self.edit_btn.toggled.connect(self._on_edit_toggle)
        dr.addWidget(self.take_radio)
        dr.addWidget(self.keep_radio)
        dr.addWidget(self.edit_btn)
        dr.addStretch()
        self.confirm_btn = QPushButton("✓ Confirm file")
        self.confirm_btn.setStyleSheet(self._btn(primary=True))
        self.confirm_btn.clicked.connect(self._on_confirm)
        dr.addWidget(self.confirm_btn)
        lay.addLayout(dr)
        return w

    # ── public API ──────────────────────────────────────────────────────────
    def show_diff(self, path: str, tagged):
        """Read-only coloured diff (non-conflict files, and decided previews)."""
        self._path = path
        self._review = None
        self._tagged = tagged
        self.prev_btn.setVisible(False)
        self.next_btn.setVisible(False)
        self.hunk_lbl.setText("")
        self.stack.setCurrentIndex(0)
        self._render_diff()

    def edit_conflict(self, path: str, review: FileReview):
        """Editor mode over a conflict's FileReview (live — Confirm reads it)."""
        self._path = path
        self._review = review
        self._tagged = None
        self._hunk_i = 0
        self._editing = False
        self.edit_btn.setChecked(False)
        self.prev_btn.setVisible(True)
        self.next_btn.setVisible(True)
        self.stack.setCurrentIndex(1)
        self._render_hunk()

    def clear(self):
        self._path = None
        self._review = None
        self._tagged = None
        self.diff_view.clear()
        self.stack.setCurrentIndex(0)

    def set_wrap(self, on: bool):
        self.wrap_check.setChecked(on)

    # ── hunk navigation / decisions ──────────────────────────────────────────
    def _conflict_hunks(self):
        if self._review is None:
            return []
        return [h for h in self._review.hunks if h.kind == "conflict"]

    def _cur_hunk(self):
        hs = self._conflict_hunks()
        if not hs:
            return None
        self._hunk_i = max(0, min(self._hunk_i, len(hs) - 1))
        return hs[self._hunk_i]

    def _step_hunk(self, d):
        hs = self._conflict_hunks()
        if not hs:
            return
        self._hunk_i = (self._hunk_i + d) % len(hs)
        self.edit_btn.setChecked(False)
        self._render_hunk()

    def _set_all(self, decision):
        for h in self._conflict_hunks():
            h.decision = decision
            h.edited_text = None
        self.edit_btn.setChecked(False)
        self._render_hunk()

    def _on_radio(self, _checked=False):
        h = self._cur_hunk()
        if h is None or self._editing:
            return
        h.decision = "update" if self.take_radio.isChecked() else "local"

    def _on_edit_toggle(self, on):
        h = self._cur_hunk()
        if h is None:
            return
        self._editing = on
        if on:
            if h.edited_text is None:
                # Seed the editor from the current non-edited resolution.
                base = "".join(h.update_lines if h.decision == "update" else h.local_lines)
                h.edited_text = base
            h.decision = "edited"
            self.free_editor.blockSignals(True)
            self.free_editor.setPlainText(h.edited_text)
            self.free_editor.blockSignals(False)
            self.free_editor.show()
            self.detail.hide()
        else:
            if h.decision == "edited":
                h.decision = "update"
            self.free_editor.hide()
            self.detail.show()
        self.take_radio.setEnabled(not on)
        self.keep_radio.setEnabled(not on)
        if not on:
            self._render_hunk()

    def _on_free_edit(self):
        h = self._cur_hunk()
        if h is not None and self._editing:
            h.edited_text = self.free_editor.toPlainText()

    def _on_confirm(self):
        if self._path:
            self.confirmed.emit(self._path)

    # ── rendering ─────────────────────────────────────────────────────────────
    def _render_hunk(self):
        h = self._cur_hunk()
        hs = self._conflict_hunks()
        n = len(hs)
        self.hunk_lbl.setText(f"Hunk {self._hunk_i + 1} of {n}" if n else "")
        if h is None:
            self.detail.setHtml("")
            return
        self.take_radio.blockSignals(True)
        self.keep_radio.blockSignals(True)
        self.take_radio.setChecked(h.decision in ("update", "edited"))
        self.keep_radio.setChecked(h.decision == "local")
        self.take_radio.blockSignals(False)
        self.keep_radio.blockSignals(False)
        editing = h.decision == "edited"
        self.edit_btn.blockSignals(True)
        self.edit_btn.setChecked(editing)
        self.edit_btn.blockSignals(False)
        self._editing = editing
        self.take_radio.setEnabled(not editing)
        self.keep_radio.setEnabled(not editing)
        self.free_editor.setVisible(editing)
        self.detail.setVisible(not editing)
        if editing:
            self.free_editor.blockSignals(True)
            self.free_editor.setPlainText(h.edited_text or "")
            self.free_editor.blockSignals(False)
            return
        p = self.p
        rows = []
        def block(title, text, fg, bg):
            rows.append(f'<div style="color:{p["muted"]};font-size:10px;padding:4px 0 2px">{title}</div>')
            for ln in (text.splitlines() or [""]):
                rows.append(f'<div style="background:{bg};color:{fg};white-space:pre;'
                            f'font-family:Consolas,monospace;padding:1px 8px">{_esc(ln) or "&nbsp;"}</div>')
        block("YOURS (on disk)", "".join(h.local_lines), "#bfe0ff", "rgba(90,160,230,0.14)")
        block("UPDATE (upstream)", "".join(h.update_lines), "#c6ecc6", "rgba(80,200,120,0.14)")
        self.detail.setHtml("".join(rows))

    def _on_wrap(self, on):
        self._wrap = on
        mode = (QTextEdit.LineWrapMode.WidgetWidth if on else QTextEdit.LineWrapMode.NoWrap)
        self.diff_view.setLineWrapMode(mode)
        self.detail.setLineWrapMode(mode)
        if self._tagged is not None:
            self._render_diff()

    def _legend_html(self, tags: set) -> str:
        chips = []
        for tag, label in _LEGEND:
            if tag not in tags:
                continue
            _, bg, _g = _REVIEW_STYLE[tag]
            fg = self._resolve_colour(_REVIEW_STYLE[tag][0])
            chips.append(f'<span style="background:{bg};color:{fg};padding:1px 7px;'
                         f'border-radius:4px;margin-right:6px">{_esc(label)}</span>')
        if not chips:
            return ""
        return ('<div style="font-family:Consolas,monospace;font-size:10px;'
                'padding:2px 8px 8px">' + "".join(chips) + "</div>")

    def _render_diff(self):
        tagged = self._tagged
        p = self.p
        if tagged and len(tagged) == 1 and tagged[0][0] == "info":
            self.diff_view.setHtml(
                f'<div style="color:{p["muted"]};font-family:Consolas,monospace;'
                f'font-size:11px;padding:14px 10px">{_esc(tagged[0][1])}</div>')
            return
        ws = "pre-wrap" if self._wrap else "pre"
        present = {t for t, _ in (tagged or [])}
        rows = []
        for tag, line in (tagged or []):
            fg_key, bg, gutter = _REVIEW_STYLE.get(tag, ("text", "transparent", " "))
            fg = self._resolve_colour(fg_key)
            bold = ";font-weight:600" if tag == "conflict_marker" else ""
            safe = _esc(line.rstrip("\n")) or "&nbsp;"
            rows.append(
                f'<tr><td style="background:{bg};color:{fg};white-space:pre;'
                f'padding:1px 4px;text-align:center;width:14px;opacity:0.7">{gutter}</td>'
                f'<td style="background:{bg};color:{fg};white-space:{ws};word-break:break-all;'
                f'padding:1px 8px{bold}">{safe}</td></tr>')
        self.diff_view.setHtml(
            self._legend_html(present)
            + '<table width="100%" cellspacing="0" cellpadding="0" '
            'style="font-family:Consolas,monospace;font-size:11px">'
            + "".join(rows) + "</table>")

    # ── styling helpers ──────────────────────────────────────────────────────
    def _btn(self, primary=False):
        p = self.p
        bg = p["accent"] if primary else p["surface2"]
        fg = "#05070a" if primary else p["text"]
        return (f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {p['border']};"
                f" border-radius: 6px; padding: 5px 10px; font-size: 11px; }}"
                f"QPushButton:hover {{ border: 1px solid {p['accent']}; }}"
                f"QPushButton:checked {{ background: {p['accent']}; color: #05070a; }}"
                f"QPushButton:disabled {{ color: {p['muted']}; }}")

    def _edit_style(self):
        p = self.p
        return (f"QTextEdit {{ background-color: {p['surface']}; border: 1px solid {p['border']};"
                f" border-radius: 8px; padding: 8px; font-family: Consolas, monospace;"
                f" font-size: 11px; color: {p['text']}; }}")


class UpdateWindow(BaseWindow):
    _header_height = 44

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.service = controller.updater_service
        self._plan = None
        self._busy = False
        self._loading_item = None
        self._spin_timer = None
        self._spin_i = 0
        self._expanded_dlg = None
        self._expanded_pane = None
        # Conflict-resolution state.
        self._reviews: dict[str, FileReview] = {}   # path -> live FileReview
        self._resolved: dict[str, str] = {}         # path -> confirmed text
        self._p = _theme.current_palette(controller)

        body = self.build_shell(self._p, "Software Updates",
                                min_size=(720, 540), buttons=("minimize", "close"))
        self.setWindowTitle("Software Updates")
        self.resize(900, 640)
        self._build_body(body)
        self._wire_service()
        self._refresh_version_label()
        self._refresh_dev_ui()
        self._refresh_baseline()

    # ── body ────────────────────────────────────────────────────────────────
    def _build_body(self, body: QVBoxLayout):
        p = self._p

        self.version_lbl = QLabel("")
        self.version_lbl.setWordWrap(True)
        self.version_lbl.setStyleSheet(
            f"color: {p['muted']}; font-size: 11px; background: transparent;")
        body.addWidget(self.version_lbl)

        self.banner_lbl = QLabel("")
        self.banner_lbl.setWordWrap(True)
        self.banner_lbl.setVisible(False)
        self.banner_lbl.setStyleSheet(
            f"color: {p['text']}; font-size: 11px; padding: 8px 10px;"
            f" border: 1px solid {p['border']}; border-radius: 7px;"
            f" background: rgba(224,160,80,0.14);")
        body.addWidget(self.banner_lbl)

        self.dev_check = QCheckBox("Developer mode - I edit these files; don't auto-update or notify")
        self.dev_check.setStyleSheet(
            f"QCheckBox {{ color: {p['muted']}; font-size: 11px; background: transparent; }}")
        self.dev_check.toggled.connect(self._on_dev_toggled)
        body.addWidget(self.dev_check)

        # Branch + check row
        row = QHBoxLayout()
        row.setSpacing(8)
        b_lbl = QLabel("Branch")
        b_lbl.setStyleSheet(f"color: {p['text']}; font-size: 12px; background: transparent;")
        row.addWidget(b_lbl)
        self.branch_combo = QComboBox()
        self.branch_combo.addItems(["main", "unstable"])
        self.branch_combo.setCurrentText(self.service.saved_branch)
        self.branch_combo.currentTextChanged.connect(self._on_branch_changed)
        self.branch_combo.setFixedWidth(150)
        self.branch_combo.setStyleSheet(self._combo_style())
        row.addWidget(self.branch_combo)
        row.addStretch()
        self.check_btn = self.make_button("Check for Updates", p, kind="primary")
        self.check_btn.clicked.connect(self._on_check_clicked)
        row.addWidget(self.check_btn)
        body.addLayout(row)

        self.summary_lbl = QLabel("Run a check to see available updates.")
        self.summary_lbl.setWordWrap(True)
        self.summary_lbl.setStyleSheet(
            f"color: {p['text']}; font-size: 12px; background: transparent;")
        body.addWidget(self.summary_lbl)

        self.commits_view = QTextEdit()
        self.commits_view.setReadOnly(True)
        self.commits_view.setMinimumHeight(0)
        self.commits_view.setVisible(False)
        self.commits_view.setStyleSheet(
            f"QTextEdit {{ background-color: {p['surface']}; border: 1px solid {p['border']};"
            f" border-radius: 8px; padding: 6px 8px; font-size: 11px; color: {p['text']}; }}")

        # Filter + select row
        sel_row = QHBoxLayout()
        sel_row.setSpacing(6)
        _flt_lbl = QLabel("Filter")
        _flt_lbl.setStyleSheet(f"color: {p['muted']}; font-size: 11px; background: transparent;")
        sel_row.addWidget(_flt_lbl)
        self.filter_combo = QComboBox()
        self.filter_combo.setFixedWidth(160)
        self.filter_combo.setStyleSheet(self._combo_style())
        for lbl, val in _FILTERS:
            self.filter_combo.addItem(lbl, val)
        self.filter_combo.currentIndexChanged.connect(lambda *_: self._apply_filter())
        sel_row.addWidget(self.filter_combo)
        self.select_changes_btn = self.make_button("Select changes", p, kind="ghost")
        self.select_changes_btn.clicked.connect(self._select_changes)
        self.select_all_btn = self.make_button("All", p, kind="ghost")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_btn = self.make_button("None", p, kind="ghost")
        self.select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        self.dismiss_btn = self.make_button("Ignore rest (settle)", p, kind="ghost")
        self.dismiss_btn.setToolTip(
            "Mark the changes you're NOT applying as 'settled' without applying them.")
        self.dismiss_btn.clicked.connect(self._dismiss_settle)
        sel_row.addWidget(self.select_changes_btn)
        sel_row.addWidget(self.select_all_btn)
        sel_row.addWidget(self.select_none_btn)
        sel_row.addWidget(self.dismiss_btn)
        sel_row.addStretch()
        self.sel_count_lbl = QLabel("")
        self.sel_count_lbl.setStyleSheet(
            f"color: {p['muted']}; font-size: 11px; background: transparent;")
        sel_row.addWidget(self.sel_count_lbl)

        # Split: file list (left, roomier) + review pane (right)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setStyleSheet(f"QSplitter::handle {{ background: {p['border']}; width: 2px; }}")

        list_pane = QWidget()
        list_pane.setStyleSheet("background: transparent;")
        lp = QVBoxLayout(list_pane)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(6)
        list_hdr = QWidget()
        list_hdr.setStyleSheet("background: transparent;")
        list_hdr.setFixedHeight(_HDR_HEIGHT)
        files_hdr = QHBoxLayout(list_hdr)
        files_hdr.setContentsMargins(0, 0, 0, 0)
        files_lbl = QLabel("Files")
        files_lbl.setStyleSheet(f"color: {p['text']}; font-size: 12px; background: transparent;")
        files_hdr.addWidget(files_lbl)
        files_hdr.addStretch()
        lp.addWidget(list_hdr)

        self.file_list = QListWidget()
        self.file_list.setMinimumWidth(320)
        self.file_list.setStyleSheet(
            f"QListWidget {{ background-color: {p['surface']}; border: 1px solid {p['border']};"
            f" border-radius: 8px; padding: 4px; font-family: Consolas, monospace;"
            f" font-size: 11px; color: {p['text']}; outline: none; }}"
            f"QListWidget::item {{ padding: 3px 4px; border-radius: 4px; }}"
            f"QListWidget::item:selected {{ background: {p['surface2']}; color: {p['text']}; }}")
        self.file_list.currentItemChanged.connect(self._on_row_changed)
        self.file_list.itemChanged.connect(lambda *_: self._update_sel_count())
        lp.addWidget(self.file_list, stretch=1)
        split.addWidget(list_pane)

        # Right pane: the ReviewPane, with an Expand button in its own header row
        # sitting beside the pane's built-in Review/Wrap header.
        right = QWidget()
        right.setStyleSheet("background: transparent;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.review_pane = ReviewPane(p, self._resolve_colour)
        self.review_pane.confirmed.connect(self._on_file_confirmed)
        # Slip an Expand button into the pane header.
        self.expand_btn = self.make_button("Expand", p, kind="ghost")
        self.expand_btn.setToolTip("Open this review in a larger, resizable window.")
        self.expand_btn.clicked.connect(self._open_expanded)
        self.review_pane.layout().itemAt(0).widget().layout().addWidget(self.expand_btn)
        rl.addWidget(self.review_pane)
        split.addWidget(right)
        split.setSizes([360, 520])

        review_container = QWidget()
        review_container.setStyleSheet("background: transparent;")
        rc = QVBoxLayout(review_container)
        rc.setContentsMargins(0, 0, 0, 0)
        rc.setSpacing(6)
        rc.addLayout(sel_row)
        rc.addWidget(split, stretch=1)
        review_container.setMinimumHeight(240)

        # Developer notes carried in commit messages (<update_message>), stacked
        # above everything: they say what the user must DO about this update,
        # which the file list and the commit subjects cannot.
        self.notes_box = QWidget()
        self.notes_box.setStyleSheet("background: transparent;")
        self.notes_layout = QVBoxLayout(self.notes_box)
        self.notes_layout.setContentsMargins(0, 0, 0, 4)
        self.notes_layout.setSpacing(5)
        self.notes_box.setVisible(False)
        body.addWidget(self.notes_box)

        outer_split = QSplitter(Qt.Orientation.Vertical)
        outer_split.setStyleSheet(f"QSplitter::handle {{ background: {p['border']}; height: 2px; }}")
        outer_split.addWidget(self.commits_view)
        outer_split.addWidget(review_container)
        outer_split.setCollapsible(0, True)
        outer_split.setCollapsible(1, False)
        outer_split.setStretchFactor(0, 0)
        outer_split.setStretchFactor(1, 1)
        outer_split.setSizes([70, 500])
        body.addWidget(outer_split, stretch=1)

        self.deps_lbl = QLabel("")
        self.deps_lbl.setWordWrap(True)
        self.deps_lbl.setStyleSheet(
            f"color: {p['muted']}; font-size: 11px; background: transparent;")
        body.addWidget(self.deps_lbl)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet(
            f"color: {p['muted']}; font-size: 11px; background: transparent;")
        body.addWidget(self.status_lbl)

        foot = QHBoxLayout()
        self.revert_btn = self.make_button("Revert Last Update", p, kind="secondary")
        self.revert_btn.setEnabled(False)
        self.revert_btn.clicked.connect(self._on_revert_clicked)
        foot.addWidget(self.revert_btn)
        self.baseline_btn = self.make_button("Establish Baseline", p, kind="secondary")
        self.baseline_btn.setToolTip(
            "Record the current upstream as the merge base so future updates can show "
            "YOUR local edits separately from the update's.")
        self.baseline_btn.clicked.connect(self._on_baseline_clicked)
        foot.addWidget(self.baseline_btn)
        foot.addStretch()
        self.apply_btn = self.make_button("Apply update", p, kind="primary")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        foot.addWidget(self.apply_btn)
        close_btn = self.make_button("Close", p, kind="secondary")
        close_btn.clicked.connect(self.close)
        foot.addWidget(close_btn)
        body.addLayout(foot)
        self._refresh_revert()

    def _combo_style(self):
        p = self._p
        return (f"QComboBox {{ background-color: {p['surface2']}; border: 1px solid {p['border']};"
                f" border-radius: 6px; padding: 6px 10px; font-size: 11px; color: {p['text']}; }}"
                f"QComboBox::drop-down {{ border: none; }}"
                f"QComboBox QAbstractItemView {{ background-color: {p['surface2']};"
                f" border: 1px solid {p['border']}; color: {p['text']};"
                f" selection-background-color: {p['accent']}; selection-color: #05070a; }}")

    # ── service wiring ────────────────────────────────────────────────────────
    def _wire_service(self):
        s = self.service
        s.check_started.connect(lambda: self._status("Checking GitHub..."))
        s.check_finished.connect(self._on_plan)
        s.check_failed.connect(self._on_error)
        s.apply_started.connect(lambda: self._status("Applying update..."))
        s.apply_finished.connect(self._on_applied)
        s.apply_failed.connect(self._on_error)
        s.progress.connect(self._on_progress)
        s.rollback_finished.connect(self._on_rolled_back)
        s.rollback_failed.connect(self._on_error)
        s.baseline_seeded.connect(self._on_baseline_seeded)
        s.baseline_failed.connect(self._on_error)
        s.commits_ready.connect(self._on_commits)

    # ── developer mode ────────────────────────────────────────────────────────
    def _banner(self, text: str):
        self.banner_lbl.setText(text)
        self.banner_lbl.setVisible(bool(text))

    def _refresh_dev_ui(self):
        auto = self.service.auto_dev_detected
        self.dev_check.blockSignals(True)
        self.dev_check.setChecked(self.service.is_dev_environment)
        self.dev_check.setEnabled(not auto)
        self.dev_check.blockSignals(False)
        if auto:
            # Says WHAT was detected, never which file gave it away — the marker
            # is an implementation detail and naming it in the UI leaks the
            # maintainer's local tooling to every user.
            self._banner("Developer working copy detected. Auto-update is "
                         "disabled here; applying would overwrite your local files with the repo.")
        elif self.service.dev_mode:
            self._banner("Developer mode is on. Startup update notifications are disabled.")
        else:
            self._banner("")

    def _on_dev_toggled(self, on: bool):
        self.service.set_dev_mode(on)
        self._refresh_dev_ui()

    # ── branch / revert ───────────────────────────────────────────────────────
    def _on_branch_changed(self, branch: str):
        self.service.set_saved_branch(branch)
        self._refresh_version_label()
        self._refresh_revert()
        self._refresh_baseline()

    def _refresh_revert(self):
        snaps = self.service.list_snapshots(self.branch_combo.currentText())
        self.revert_btn.setEnabled(bool(snaps) and not self._busy)
        if snaps:
            last = snaps[0]
            self.revert_btn.setToolTip(
                f"Revert the last update ({last.get('files', 0)} file(s), applied "
                f"{last.get('created', '?')}) back to {last.get('version_before') or 'the prior state'}.")
        else:
            self.revert_btn.setToolTip("No applied update to revert yet.")

    def _on_revert_clicked(self):
        if self._busy:
            return
        snaps = self.service.list_snapshots(self.branch_combo.currentText())
        if not snaps:
            return
        last = snaps[0]
        box = QMessageBox(self)
        box.setWindowTitle("Revert last update?")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            f"Revert the last update on '{self.branch_combo.currentText()}'?\n\n"
            f"This restores {last.get('files', 0)} file(s) to "
            f"{last.get('version_before') or 'the pre-update state'} from the snapshot taken "
            f"{last.get('created', '?')}. Files added by the update are removed.\n\n"
            "Restart Systema Auxilium afterwards.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self._status("Reverting last update...")
        self.service.rollback(self.branch_combo.currentText())

    # ── baseline ──────────────────────────────────────────────────────────────
    def _refresh_baseline(self):
        has = self.service.has_baseline(self.branch_combo.currentText())
        self.baseline_btn.setEnabled(not self._busy)
        self.baseline_btn.setText("Baseline Set ✓" if has else "Establish Baseline")

    def _on_baseline_clicked(self):
        if self._busy:
            return
        has = self.service.has_baseline(self.branch_combo.currentText())
        if has:
            box = QMessageBox(self)
            box.setWindowTitle("Re-establish baseline?")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(
                "A merge baseline already exists for this branch. Re-establishing it "
                "records the CURRENT upstream as the new common ancestor.\n\n"
                "This changes no files, but any local edits you've made relative to "
                "the old baseline will be re-measured against the new one. Continue?")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        self._set_busy(True)
        self._status("Establishing baseline from upstream (no files change)...")
        self.service.seed_baseline(self.branch_combo.currentText(), force=has)

    def _on_baseline_seeded(self, status):
        self._set_busy(False)
        if isinstance(status, dict) and status.get("seeded"):
            self._status(
                f"Baseline established at {status.get('version', '?')} "
                f"({status.get('files', 0)} file(s)). Three-way review is now active — "
                "re-run a check to see your local edits coloured separately.")
        elif isinstance(status, dict):
            self._status(f"Baseline already set ({status.get('version', '?')}).")
        else:
            self._status("Baseline established.")
        self._refresh_version_label()
        self._refresh_baseline()

    def _on_rolled_back(self, result):
        self._set_busy(False)
        if result.success:
            self._status(result.message + "  |  Restart Systema Auxilium to run the reverted version.")
            self.summary_lbl.setText("Reverted to the previous version.")
            self.file_list.clear()
            self.review_pane.clear()
        else:
            self._status(f"Revert failed: {result.message}")
        self._refresh_version_label()
        self._refresh_revert()

    # ── check / plan ────────────────────────────────────────────────────────────
    def _on_check_clicked(self):
        if self._busy:
            return
        self._plan = None
        self._reviews.clear()
        self._resolved.clear()
        self.file_list.clear()
        self.review_pane.clear()
        self.deps_lbl.clear()
        self.commits_view.setVisible(False)
        self.apply_btn.setEnabled(False)
        self._set_busy(True)
        self.service.check(self.branch_combo.currentText())
        self.service.fetch_pending_commits(self.branch_combo.currentText())

    def _build_note_card(self, note):
        """One dismissible developer notice from a commit's <update_message>."""
        p = self._p
        card = QFrame()
        card.setObjectName("updNote")
        card.setStyleSheet(
            f"QFrame#updNote {{ background: {p['surface']}; "
            f"border: 1px solid {p['accent']}; border-left: 3px solid {p['accent']}; "
            f"border-radius: 6px; }}")
        row = QHBoxLayout(card)
        row.setContentsMargins(10, 7, 6, 7)
        row.setSpacing(8)

        col = QVBoxLayout()
        col.setSpacing(2)
        head = QLabel(f"Note from {note.sha[:7]}"
                      + (f" - {note.subject[:60]}" if note.subject else ""))
        head.setStyleSheet(
            f"color: {p['muted']}; font-size: 9px; font-weight: 600; "
            f"background: transparent; border: none;")
        body = QLabel(note.text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(
            f"color: {p['text']}; font-size: 11px; background: transparent; "
            f"border: none;")
        col.addWidget(head)
        col.addWidget(body)
        row.addLayout(col, 1)

        close = QPushButton("x")
        close.setFixedSize(20, 20)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("Dismiss this note")
        close.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"color: {p['muted']}; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {p['text']}; }}")

        def _dismiss():
            # Persist BEFORE removing the widget: if the write fails the note
            # should still be on screen rather than silently gone for this run
            # and back on the next one.
            try:
                upd_messages.dismiss(note)
            except Exception as e:
                log.warning(f"[UpdateWindow] could not persist dismissal: {e}")
            card.setParent(None)
            card.deleteLater()
            self._sync_notes_visibility()

        close.clicked.connect(_dismiss)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        return card

    def _sync_notes_visibility(self):
        """Hide the container once its last note is dismissed."""
        try:
            self.notes_box.setVisible(self.notes_layout.count() > 0)
        except RuntimeError:
            pass

    def _render_notes(self, commits):
        """Stack every undismissed <update_message> in the pending range.

        Newest first, and ALL of them: a user three versions behind needs all
        three notes, not just the latest.
        """
        try:
            while self.notes_layout.count():
                item = self.notes_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            for note in upd_messages.pending(commits):
                self.notes_layout.addWidget(self._build_note_card(note))
            self._sync_notes_visibility()
        except Exception as e:
            log.warning(f"[UpdateWindow._render_notes] {type(e).__name__}: {e}")

    def _on_commits(self, commits):
        self._render_notes(commits)
        if not commits:
            self.commits_view.setVisible(False)
            return
        p = self._p
        n = len(commits)
        head = (f'<div style="color:{p["muted"]};font-size:10px;padding-bottom:3px">'
                f'{n} pending commit(s), newest first</div>')
        rows = []
        for c in commits[:40]:
            # strip: the note is rendered as its own card above, so leaving it
            # in the commit body would print it twice.
            msg = upd_messages.strip(c.get("message", "") or "").splitlines()
            subj = _esc(msg[0]) if msg else "(no message)"
            sha = _esc((c.get("sha", "") or "")[:7])
            author = _esc(c.get("author", "") or "")
            meta = f'<span style="color:{p["muted"]}">{sha}{("  " + author) if author else ""}</span>'
            rows.append(f'<div style="padding:1px 0">'
                        f'<span style="color:{p["accent"]}">•</span> '
                        f'<span style="color:{p["text"]}">{subj}</span>  {meta}</div>')
        self.commits_view.setHtml(head + "".join(rows))
        self.commits_view.setVisible(True)

    def _on_plan(self, plan):
        self._set_busy(False)
        self._plan = plan
        self.summary_lbl.setText(plan.summary())
        self._status("")
        if not plan.has_update:
            self.summary_lbl.setText("You are up to date. Nothing to update.")
            self._update_sel_count()
            return

        from systema.updater.service import is_sensitive_path
        self.file_list.clear()
        branch = self.branch_combo.currentText()
        settled = self.service.prune_settled_against_plan(branch, plan)
        risky_count = 0
        settled_count = 0
        conflict_count = 0
        for fc in plan.file_changes:
            if fc.change.value == "unchanged":
                continue
            is_text = getattr(fc, "is_text", True)
            is_change = is_text and fc.change.value in _TEXTDIFF_CHANGES
            sensitive = is_sensitive_path(fc.path)
            risky_protected = sensitive and fc.change.value != "added"
            is_settled = fc.path in settled
            is_conflict = fc.change.value == "conflict"
            if is_conflict and not is_settled:
                conflict_count += 1
            if is_settled:
                settled_count += 1
            elif risky_protected:
                risky_count += 1

            item = QListWidgetItem()
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            eff_is_change = is_change and not is_settled
            # Auto-check textual-diff files, never a risky-protected one; a
            # conflict stays UNCHECKED until the user resolves + Confirms it.
            auto = eff_is_change and not risky_protected and not is_conflict
            item.setCheckState(Qt.CheckState.Checked if auto else Qt.CheckState.Unchecked)
            item.setData(_ROLE_PATH, fc.path)
            item.setData(_ROLE_IS_CHANGE, eff_is_change)
            item.setData(_ROLE_SENSITIVE, sensitive)
            item.setData(_ROLE_PROTECTED_RISKY, risky_protected)
            item.setData(_ROLE_SETTLED, is_settled)
            item.setData(_ROLE_CHANGE, fc.change.value)
            item.setData(_ROLE_DECIDED, False)
            item.setData(Qt.ItemDataRole.UserRole + 7,
                         "" if is_text else "   [binary]")   # suffix
            self._style_row(item, settled)
            self.file_list.addItem(item)

        if risky_count:
            self._banner(
                f"{risky_count} PROTECTED change(s) would overwrite or delete files "
                "in folders you own (providers / skills). They are unticked and "
                "highlighted — resolve them or tick them yourself.")
        if settled_count:
            self.summary_lbl.setText(
                self.summary_lbl.text()
                + f"   ·   {settled_count} settled (won't notify until changed upstream)")
        if conflict_count:
            self._status(
                f"{conflict_count} conflict(s): you and upstream changed the same lines. "
                "Click each CONFLICT file, resolve it hunk-by-hunk (default: take the "
                "update), and Confirm — then Apply.")

        if plan.dependency_changes:
            try:
                summ = plan.dependency_summary()
                rows = "   ".join(d.describe() for d in plan.dependency_changes)
                txt = (f"Dependency changes — {summ}:   {rows}" if summ
                       else f"Dependency changes:   {rows}")
                if plan.removed_dependencies:
                    txt += "   (removed deps are reported only, not uninstalled)"
            except AttributeError:
                names = ", ".join(d.requirement for d in plan.dependency_changes)
                txt = f"Dependencies to install for selected files: {names}"
            self.deps_lbl.setText(txt)

        if not self.service.is_dev_environment:
            modified = sum(1 for fc in plan.file_changes if fc.change.value == "modified")
            if modified >= 15:
                self._banner(f"Heads up: {modified} files differ from the repo. If your local "
                             "copy is ahead (a development copy), enable Developer mode above.")

        self._apply_filter()
        if self.file_list.count():
            for i in range(self.file_list.count()):
                if not self.file_list.item(i).isHidden():
                    self.file_list.setCurrentRow(i)
                    break
        self._update_sel_count()

    # ── row rendering / state ─────────────────────────────────────────────────
    def _style_row(self, item, settled):
        """(Re)build a row's label, colour, and check-state affordance from its
        current state — the single place row visuals are decided, so a state
        change (e.g. CONFLICT -> DECIDED) always re-renders truthfully."""
        path = item.data(_ROLE_PATH)
        change = item.data(_ROLE_CHANGE)
        sensitive = item.data(_ROLE_SENSITIVE)
        risky = item.data(_ROLE_PROTECTED_RISKY)
        is_settled = item.data(_ROLE_SETTLED)
        decided = item.data(_ROLE_DECIDED)
        suffix = item.data(Qt.ItemDataRole.UserRole + 7) or ""
        prot = "PROT" if sensitive else "    "

        if is_settled:
            short = (settled.get(path, {}).get("commit", "") or "").split("+")[-1][:7] or "?"
            item.setText(f"{'SETTLED':8} {prot}  {path}   (from {short})")
            item.setForeground(QColor(self._p["muted"]))
            item.setBackground(QColor(0, 0, 0, 0))
            item.setToolTip(f"SETTLED — you decided on this at commit {short}; unchanged upstream since.")
            return

        if decided:
            colour, tag = _CHANGE_STYLE["decided"]
            item.setText(f"{tag:8} {prot}  {path}")
            item.setForeground(QColor(colour))
            hl = QColor(colour); hl.setAlpha(45); item.setBackground(hl)
            item.setToolTip("DECIDED — you resolved this conflict; it will be applied. "
                            "Click to review or Reopen.")
            return

        colour, tag = _CHANGE_STYLE.get(change, (None, str(change).upper()))
        item.setText(f"{tag:8} {prot}  {path}{suffix}")
        if risky:
            item.setForeground(QColor(_SENSITIVE_COLOUR))
            hl = QColor(_SENSITIVE_COLOUR); hl.setAlpha(40); item.setBackground(hl)
            verb = "delete" if change == "deleted" else "overwrite"
            item.setToolTip(f"PROTECTED — applying will {verb} your local version (providers/skills). "
                            "Resolve it or tick it only if you really want the upstream version.")
        elif change == "conflict":
            item.setForeground(QColor(colour))
            hl = QColor(colour); hl.setAlpha(48); item.setBackground(hl)
            item.setToolTip("CONFLICT — you and upstream changed the same lines. Click to resolve "
                            "hunk-by-hunk, then Confirm. Applying without resolving is blocked.")
        else:
            if colour:
                item.setForeground(QColor(colour))
            if item.data(_ROLE_IS_CHANGE):
                hl = QColor(self._p["accent"]); hl.setAlpha(55); item.setBackground(hl)

    def _item_for_path(self, path):
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if it.data(_ROLE_PATH) == path:
                return it
        return None

    def _on_row_changed(self, current, _previous):
        if current is None or self._plan is None:
            return
        path = current.data(_ROLE_PATH)
        is_conflict = current.data(_ROLE_CHANGE) == "conflict"
        decided = current.data(_ROLE_DECIDED)
        settled = current.data(_ROLE_SETTLED)
        # An unresolved conflict opens the inline editor; everything else (incl.
        # a decided conflict and settled files) shows the read-only diff.
        if is_conflict and not decided and not settled:
            review = self._ensure_review(path)
            if review is not None and any(h.kind == "conflict" for h in review.hunks):
                self.review_pane.edit_conflict(path, review)
                return
        self.review_pane.show_diff(path, self.service.review(path))

    def _ensure_review(self, path) -> FileReview | None:
        """Build (once) and cache the live FileReview for a conflict path."""
        if path in self._reviews:
            return self._reviews[path]
        from systema.updater.service import is_sensitive_path
        tagged = self.service.review(path)
        if len(tagged) == 1 and tagged[0][0] == "info":
            return None
        session = ReviewSession()
        session.add(path, tagged, sensitive=is_sensitive_path(path))
        fr = session.files.get(path)
        self._reviews[path] = fr
        return fr

    def _on_file_confirmed(self, path):
        """User Confirmed a conflict resolution: assemble + store, flip the row
        to DECIDED, auto-tick it for apply."""
        fr = self._reviews.get(path)
        if fr is None:
            return
        self._resolved[path] = fr.assembled()
        it = self._item_for_path(path)
        if it is not None:
            it.setData(_ROLE_DECIDED, True)
            it.setData(_ROLE_IS_CHANGE, True)
            it.setCheckState(Qt.CheckState.Checked)
            self._style_row(it, {})
        self._status(f"Resolved {path}. It will be applied with the update.")
        # Re-show it as a decided diff preview (of the resolved text is overkill;
        # show the normal 3-way so they can still eyeball it).
        self.review_pane.show_diff(path, self.service.review(path))
        self._update_sel_count()

    def _resolve_colour(self, key: str) -> str:
        if key == "text":
            return self._p["text"]
        if key == "muted":
            return self._p["muted"]
        if key == "accent":
            return self._p["accent"]
        return key

    # ── filter ────────────────────────────────────────────────────────────────
    def _apply_filter(self):
        mode = self.filter_combo.currentData() or "all"
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            change = it.data(_ROLE_CHANGE)
            decided = it.data(_ROLE_DECIDED)
            settled = it.data(_ROLE_SETTLED)
            sensitive = it.data(_ROLE_SENSITIVE)
            if mode == "all":
                show = True
            elif mode == "unresolved":
                show = change == "conflict" and not decided and not settled
            elif mode == "conflict":
                show = change == "conflict"
            elif mode == "decided":
                show = bool(decided)
            elif mode == "protected":
                show = bool(sensitive)
            elif mode == "settled":
                show = bool(settled)
            else:
                show = True
            it.setHidden(not show)

    # ── expand pop-out ──────────────────────────────────────────────────────────
    def _open_expanded(self):
        from PyQt6.QtWidgets import QDialog
        if self._expanded_dlg is not None:
            self._expanded_dlg.raise_()
            self._expanded_dlg.activateWindow()
            return
        cur = self.file_list.currentItem()
        if cur is None:
            self._status("Select a file to expand.")
            return
        p = self._p
        dlg = QDialog(self)
        dlg.setWindowTitle("Review changes")
        dlg.resize(940, 700)
        dlg.setStyleSheet(f"QDialog {{ background-color: {p['bg']}; }}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)
        pane = ReviewPane(p, self._resolve_colour)
        pane.confirmed.connect(self._on_file_confirmed)
        lay.addWidget(pane, stretch=1)
        self._expanded_dlg = dlg
        self._expanded_pane = pane

        # Mirror the current selection into the pop-out (shares FileReview objects).
        path = cur.data(_ROLE_PATH)
        if (cur.data(_ROLE_CHANGE) == "conflict" and not cur.data(_ROLE_DECIDED)
                and not cur.data(_ROLE_SETTLED)):
            review = self._ensure_review(path)
            if review is not None:
                pane.edit_conflict(path, review)
            else:
                pane.show_diff(path, self.service.review(path))
        else:
            pane.show_diff(path, self.service.review(path))

        def _clear():
            self._expanded_dlg = None
            self._expanded_pane = None
        dlg.finished.connect(lambda *_: _clear())
        dlg.show()

    # ── selection ────────────────────────────────────────────────────────────
    def _checked_paths(self) -> list[str]:
        out = []
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(_ROLE_PATH))
        return out

    def _set_all_checked(self, checked: bool):
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if it.isHidden():
                continue
            if checked and (it.data(_ROLE_PROTECTED_RISKY) or it.data(_ROLE_SETTLED)):
                it.setCheckState(Qt.CheckState.Unchecked)
            elif checked and it.data(_ROLE_CHANGE) == "conflict" and not it.data(_ROLE_DECIDED):
                it.setCheckState(Qt.CheckState.Unchecked)   # unresolved conflict never auto-ticks
            else:
                it.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._update_sel_count()

    def _select_changes(self):
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            unresolved_conflict = it.data(_ROLE_CHANGE) == "conflict" and not it.data(_ROLE_DECIDED)
            pick = (it.data(_ROLE_IS_CHANGE) and not it.data(_ROLE_PROTECTED_RISKY)
                    and not unresolved_conflict)
            it.setCheckState(Qt.CheckState.Checked if pick else Qt.CheckState.Unchecked)
        self._update_sel_count()

    def _dismiss_settle(self):
        if self._plan is None or self._busy:
            self._status("Run a check first.")
            return
        branch = self.branch_combo.currentText()
        already = set(self.service.settled_files(branch).keys())
        items = [(fc.path, getattr(fc, "remote_hash", None))
                 for fc in self._plan.file_changes
                 if fc.change.value != "unchanged" and fc.path not in already]
        if not items:
            self._status("Nothing to settle — everything shown is already settled.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Ignore these changes?")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"Mark {len(items)} shown change(s) as settled WITHOUT applying them?\n\n"
            "They stop appearing as pending and won't trigger update notifications, "
            "until a NEW commit changes one of them upstream.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        n = self.service.settle_files(branch, items, self._plan.target_version)
        self.service.set_acknowledged(branch, self._plan.target_version)
        self._on_plan(self._plan)
        self._status(f"Settled {n} change(s). You won't be notified until a new commit changes them.")

    def _checked_risky_protected_paths(self) -> list[str]:
        out = []
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if it.checkState() == Qt.CheckState.Checked and it.data(_ROLE_PROTECTED_RISKY):
                out.append(it.data(_ROLE_PATH))
        return out

    def _unresolved_conflicts(self) -> list[str]:
        """Conflict rows that are neither Confirmed nor settled — Apply is
        blocked while any of these remain."""
        out = []
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if (it.data(_ROLE_CHANGE) == "conflict" and not it.data(_ROLE_DECIDED)
                    and not it.data(_ROLE_SETTLED)):
                out.append(it.data(_ROLE_PATH))
        return out

    def _update_sel_count(self):
        total = sum(1 for i in range(self.file_list.count())
                    if not self.file_list.item(i).isHidden())
        n = len(self._checked_paths())
        self.sel_count_lbl.setText(f"{n} of {total} shown selected")
        self.apply_btn.setEnabled(n > 0 and not self._busy)

    def _on_apply_clicked(self):
        if self._busy or self._plan is None:
            return
        selected = self._checked_paths()
        if not selected:
            return

        # Gate: unresolved conflicts must be resolved first. Prompt, then focus
        # the list on exactly those so the user can knock them out.
        unresolved = [p for p in self._unresolved_conflicts()]
        if unresolved:
            box = QMessageBox(self)
            box.setWindowTitle("Resolve conflicts first")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(
                f"{len(unresolved)} conflict(s) aren't resolved yet. Applying now would "
                "leave them conflicted.\n\nResolve each CONFLICT file (take/keep per hunk, "
                "then Confirm) — press OK to filter the list to just those.")
            box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(QMessageBox.StandardButton.Ok)
            if box.exec() == QMessageBox.StandardButton.Ok:
                self.filter_combo.setCurrentIndex(
                    next(i for i, (_, v) in enumerate(_FILTERS) if v == "unresolved"))
                self._apply_filter()
            return

        try:
            busy, reason = self.controller.agent_activity()
        except Exception:
            busy, reason = False, ""
        if busy:
            warn = QMessageBox(self)
            warn.setWindowTitle("Assistant is busy")
            warn.setIcon(QMessageBox.Icon.Warning)
            warn.setText(
                f"The assistant is currently active ({reason}).\n\n"
                "Applying an update now rewrites source files while it runs, which "
                "can corrupt the in-progress task or crash the app.\n\n"
                "Wait for it to finish, or apply anyway?")
            warn.addButton("Apply anyway", QMessageBox.ButtonRole.DestructiveRole)
            wait_btn = warn.addButton("Wait", QMessageBox.ButtonRole.RejectRole)
            warn.setDefaultButton(wait_btn)
            warn.exec()
            if warn.clickedButton() is wait_btn:
                return

        sens = self._checked_risky_protected_paths()
        if sens:
            listing = "\n".join(f"  • {s}" for s in sens[:12])
            more = f"\n  … and {len(sens) - 12} more" if len(sens) > 12 else ""
            sbox = QMessageBox(self)
            sbox.setWindowTitle("Overwrite protected files?")
            sbox.setIcon(QMessageBox.Icon.Critical)
            sbox.setText(
                f"{len(sens)} selected file(s) are PROTECTED — they live in folders "
                "you own (provider scripts with your accounts/keys, or your skills):\n\n"
                f"{listing}{more}\n\n"
                "Applying the upstream version will OVERWRITE or DELETE your local "
                "copy. A backup is written to data/updates/, but this is not "
                "reversible from inside the app beyond that snapshot.\n\n"
                "Overwrite these anyway?")
            sbox.addButton("Overwrite them", QMessageBox.ButtonRole.DestructiveRole)
            keep_btn = sbox.addButton("Keep mine", QMessageBox.ButtonRole.RejectRole)
            sbox.setDefaultButton(keep_btn)
            sbox.exec()
            if sbox.clickedButton() is keep_btn:
                return

        # Count every actionable change: a FULL apply (advances the version
        # marker + snapshots the baseline) only happens when nothing is left
        # behind — which is what makes a resolved conflict stick permanently.
        actionable = [fc.path for fc in self._plan.file_changes
                      if fc.change.value != "unchanged"
                      and fc.path not in self.service.settled_files(self.branch_combo.currentText())]
        full = set(selected) >= set(actionable)
        only = None if full else selected
        resolved = {p: t for p, t in self._resolved.items() if p in selected}

        msg = (f"Update {len(selected)} changed file(s) to {self._plan.target_version}?\n\n"
               "A backup is written to data/updates/ before anything changes.\n"
               "Restart Systema Auxilium afterwards.")
        if resolved:
            msg += f"\n\n{len(resolved)} resolved conflict(s) will be applied with your chosen merge."
        msg += ("\n\n(Applying ALL changes advances the version marker.)" if full
                else "\n\n(Partial update - version marker stays unchanged.)")
        box = QMessageBox(self)
        box.setWindowTitle("Apply update?")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(msg)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self.service.apply(only=only, resolved=resolved)

    def _on_applied(self, result):
        self._set_busy(False)
        self._plan = None
        if result.success:
            parts = [result.message]
            if result.installed_deps:
                parts.append("installed: " + ", ".join(result.installed_deps))
            if result.backup_path:
                parts.append(f"backup: {result.backup_path}")
            parts.append("Restart Systema Auxilium to run the updated version.")
            self._status("  |  ".join(parts))
            self.summary_lbl.setText("Update applied.")
            self.file_list.clear()
            self.review_pane.clear()
            self._reviews.clear()
            self._resolved.clear()
            self.apply_btn.setEnabled(False)
            self._offer_restart()
        else:
            self._status(f"Update failed: {result.message}"
                         + ("  (changes rolled back)" if result.rolled_back else ""))
        self._refresh_version_label()
        self._refresh_revert()

    def _offer_restart(self):
        box = QMessageBox(self)
        box.setWindowTitle("Restart to finish updating")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            "The update was applied. Systema Auxilium needs to restart to run the "
            "new version.\n\nRestart now? The app will reopen automatically.")
        now_btn = box.addButton("Restart now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(now_btn)
        box.exec()
        if box.clickedButton() is now_btn:
            ok = False
            try:
                ok = bool(self.controller.restart_app())
            except Exception as e:
                ok = False
                self._status(f"Could not restart automatically: {e}. Please restart manually.")
            if not ok:
                self._status("Restart it manually to run the updated version.")

    def _on_error(self, msg: str):
        self._set_busy(False)
        self._status(f"Error: {msg}")

    def _on_progress(self, name: str, payload: dict):
        if name == "download.progress":
            total = payload.get("total") or 0
            recv = payload.get("received") or 0
            if total:
                self._status(f"Downloading... {recv * 100 // total}%")
        elif name == "dep.install":
            mark = "ok" if payload.get("ok") else "failed"
            self._status(f"pip install {payload.get('requirement','')} [{mark}]")
        elif name == "backup":
            self._status(f"backup -> {payload.get('path','')}")

    # ── helpers ───────────────────────────────────────────────────────────────
    def _status(self, text: str):
        self.status_lbl.setText(text)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.check_btn.setEnabled(not busy)
        self.branch_combo.setEnabled(not busy)
        self.check_btn.setText("Checking..." if busy else "Check for Updates")
        if busy:
            self.apply_btn.setEnabled(False)
            self.revert_btn.setEnabled(False)
            self.baseline_btn.setEnabled(False)
            self._start_spinner()
        else:
            self._stop_spinner()
            self._update_sel_count()
            self._refresh_revert()
            self._refresh_baseline()

    _SPIN = ("◐", "◓", "◑", "◒")

    def _start_spinner(self):
        self._stop_spinner()
        self._spin_i = 0
        self._loading_item = QListWidgetItem(f"{self._SPIN[0]}   Loading changes…")
        self._loading_item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._loading_item.setForeground(QColor(self._p["muted"]))
        self.file_list.addItem(self._loading_item)
        if self._spin_timer is None:
            self._spin_timer = QTimer(self)
            self._spin_timer.timeout.connect(self._tick_spin)
        self._spin_timer.start(120)

    def _tick_spin(self):
        if self._loading_item is None:
            return
        self._spin_i = (self._spin_i + 1) % len(self._SPIN)
        self._loading_item.setText(f"{self._SPIN[self._spin_i]}   Loading changes…")

    def _stop_spinner(self):
        if self._spin_timer is not None:
            self._spin_timer.stop()
        if self._loading_item is not None:
            row = self.file_list.row(self._loading_item)
            if row >= 0:
                self.file_list.takeItem(row)
            self._loading_item = None

    def _refresh_version_label(self):
        branch = self.branch_combo.currentText() if hasattr(self, "branch_combo") else "main"
        ver = self.service.installed_version(branch)
        self.version_lbl.setText(
            f"Repository  uukjtisa/systema-auxilium      "
            f"Installed  {ver or 'not yet updated via the updater'}")

    def showEvent(self, event):
        super().showEvent(event)
        self.center_on_screen()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self._stop_spinner()
        try:
            if self.service.has_pending_plan:
                self.service.discard()
        except Exception:
            pass
        super().closeEvent(event)
