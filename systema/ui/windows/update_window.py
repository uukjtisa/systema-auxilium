"""
systema/ui/windows/update_window.py
UpdateWindow - review-and-select self-update UI (Settings > System).

Built on BaseWindow's shared shell (custom title bar + button styles). Talks to
controller.updater_service (UpdaterService) purely over Qt signals so all
network/disk work stays off the GUI thread. Flow:

    Check  ->  review the file list + per-file diff  ->  tick the files to
    update  ->  Apply Selected (with confirmation + backup).

No emojis anywhere - clean monochrome glyphs / text only.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QMessageBox, QSplitter, QTextEdit,
                             QVBoxLayout, QWidget)

from systema.ui import theme as _theme
from systema.ui.base_window import BaseWindow

# Per-change-type accent colours + a short tag shown in the list.
_CHANGE_STYLE = {
    "added":    ("#6fcf7f", "NEW"),
    "modified": (None,      "MOD"),   # None -> use palette text
    "merged":   ("#5fb0e6", "MERGE"),
    "conflict": ("#e0655a", "CONFLICT"),
    "deleted":  ("#d98b3f", "DEL"),
}

# Change types that carry a reviewable TEXTUAL difference (auto-selected on
# refresh + highlighted). Conflicts are text too but stay opt-in.
_TEXTDIFF_CHANGES = ("added", "modified", "merged")

# Origin colours for the 3-way review. Four distinct hues so you can tell, at a
# glance, what the UPDATE changed vs what YOU changed locally:
#   green  = added by the update       red    = removed by the update
#   cyan   = added by your local edits purple = removed by your local edits
# (fg, bg, gutter). Conflict blocks reuse the local/update hues with a neutral
# base band between them.
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

# Which tags to advertise in the legend (label + swatch colour key).
_LEGEND = [
    ("update_add", "added by update"),
    ("update_del", "removed by update"),
    ("local_add",  "added locally"),
    ("local_del",  "removed locally"),
]

# Height (px) of the Files / Review header rows. Pinned equal on both panes so
# the two content boxes line up — the right row holds 34px-tall buttons.
_HDR_HEIGHT = 36

# Extra item roles.
_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_IS_CHANGE = Qt.ItemDataRole.UserRole + 1   # True => textual-diff file
_ROLE_SENSITIVE = Qt.ItemDataRole.UserRole + 2   # True => in a user-owned data folder
_ROLE_PROTECTED_RISKY = Qt.ItemDataRole.UserRole + 3  # True => protected AND overwrites/deletes (MOD/DEL) — manual-only

# Warning colour for protected (sensitive) files in the list.
_SENSITIVE_COLOUR = "#e0a24e"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class UpdateWindow(BaseWindow):
    _header_height = 44

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.service = controller.updater_service
        self._plan = None
        self._busy = False
        self._loading_item = None      # spinner placeholder row in the file list
        self._spin_timer = None
        self._spin_i = 0
        self._diff_html = ""
        self._tagged = None
        self._expanded_dlg = None
        self._expanded_view = None
        self._p = _theme.current_palette(controller)

        body = self.build_shell(self._p, "Software Updates",
                                min_size=(680, 520), buttons=("minimize", "close"))
        self.resize(860, 620)
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

        # Developer-mode banner (shown when this copy is a dev / ahead-of-repo copy).
        self.banner_lbl = QLabel("")
        self.banner_lbl.setWordWrap(True)
        self.banner_lbl.setVisible(False)
        self.banner_lbl.setStyleSheet(
            f"color: {p['text']}; font-size: 11px; padding: 8px 10px;"
            f" border: 1px solid {p['border']}; border-radius: 7px;"
            f" background: rgba(224,160,80,0.14);")
        body.addWidget(self.banner_lbl)

        # Developer-mode toggle
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
        self.branch_combo.setStyleSheet(
            f"QComboBox {{ background-color: {p['surface2']}; border: 1px solid {p['border']};"
            f" border-radius: 6px; padding: 6px 10px; font-size: 11px; color: {p['text']}; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background-color: {p['surface2']};"
            f" border: 1px solid {p['border']}; color: {p['text']};"
            f" selection-background-color: {p['accent']}; selection-color: #05070a; }}")
        row.addWidget(self.branch_combo)
        row.addStretch()
        self.check_btn = self.make_button("Check for Updates", p, kind="primary")
        self.check_btn.clicked.connect(self._on_check_clicked)
        row.addWidget(self.check_btn)
        body.addLayout(row)

        # Summary line
        self.summary_lbl = QLabel("Run a check to see available updates.")
        self.summary_lbl.setWordWrap(True)
        self.summary_lbl.setStyleSheet(
            f"color: {p['text']}; font-size: 12px; background: transparent;")
        body.addWidget(self.summary_lbl)

        # Stacked commit messages ("what's changed since your version").
        self.commits_view = QTextEdit()
        self.commits_view.setReadOnly(True)
        self.commits_view.setMaximumHeight(96)
        self.commits_view.setVisible(False)
        self.commits_view.setStyleSheet(
            f"QTextEdit {{ background-color: {p['surface']}; border: 1px solid {p['border']};"
            f" border-radius: 8px; padding: 6px 8px; font-size: 11px; color: {p['text']}; }}")
        body.addWidget(self.commits_view)

        # Select all / none row
        sel_row = QHBoxLayout()
        sel_row.setSpacing(6)
        self.select_changes_btn = self.make_button("Select files with changes", p, kind="ghost")
        self.select_changes_btn.clicked.connect(self._select_changes)
        self.select_all_btn = self.make_button("Select all", p, kind="ghost")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_btn = self.make_button("Select none", p, kind="ghost")
        self.select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        sel_row.addWidget(self.select_changes_btn)
        sel_row.addWidget(self.select_all_btn)
        sel_row.addWidget(self.select_none_btn)
        sel_row.addStretch()
        self.sel_count_lbl = QLabel("")
        self.sel_count_lbl.setStyleSheet(
            f"color: {p['muted']}; font-size: 11px; background: transparent;")
        sel_row.addWidget(self.sel_count_lbl)
        body.addLayout(sel_row)

        # Split: file list (left) + diff review (right)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setStyleSheet(
            f"QSplitter::handle {{ background: {p['border']}; width: 2px; }}")

        # Left pane: a header row PINNED to the exact same height as the review
        # toolbar (which is as tall as its buttons), so both panes' content areas
        # line up vertically. Without the fixed height the bare "Files" label is
        # shorter than the button row on the right and the two boxes misalign.
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

        # Right pane: a small toolbar (wrap toggle + expand) above the diff view,
        # in a container fixed to the same height as the Files header so the two
        # content boxes line up.
        diff_pane = QWidget()
        diff_pane.setStyleSheet("background: transparent;")
        dp = QVBoxLayout(diff_pane)
        dp.setContentsMargins(0, 0, 0, 0)
        dp.setSpacing(6)

        tools_hdr = QWidget()
        tools_hdr.setStyleSheet("background: transparent;")
        tools_hdr.setFixedHeight(_HDR_HEIGHT)
        tools = QHBoxLayout(tools_hdr)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(8)
        review_lbl = QLabel("Review")
        review_lbl.setStyleSheet(f"color: {p['text']}; font-size: 12px; background: transparent;")
        tools.addWidget(review_lbl)
        tools.addStretch()
        self.wrap_check = QCheckBox("Wrap text")
        self.wrap_check.setStyleSheet(
            f"QCheckBox {{ color: {p['muted']}; font-size: 11px; background: transparent; }}")
        self.wrap_check.toggled.connect(self._on_wrap_toggled)
        tools.addWidget(self.wrap_check)
        self.manage_btn = self.make_button("Manage", p, kind="ghost")
        self.manage_btn.setToolTip(
            "Resolve changes hunk-by-hunk (or line-by-line) yourself — the safe way "
            "to handle PROTECTED files (your providers / skills) without losing your work. "
            "Includes the current file and every protected file.")
        self.manage_btn.clicked.connect(self._open_manage)
        tools.addWidget(self.manage_btn)
        self.expand_btn = self.make_button("Expand", p, kind="ghost")
        self.expand_btn.setToolTip("Open this review in a larger, resizable window.")
        self.expand_btn.clicked.connect(self._open_expanded)
        tools.addWidget(self.expand_btn)
        dp.addWidget(tools_hdr)

        self.diff_view = QTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.diff_view.setPlaceholderText("Select a file to review the exact changes.")
        self.diff_view.setStyleSheet(
            f"QTextEdit {{ background-color: {p['surface']}; border: 1px solid {p['border']};"
            f" border-radius: 8px; padding: 8px; font-family: Consolas, monospace;"
            f" font-size: 11px; color: {p['text']}; }}")
        dp.addWidget(self.diff_view, stretch=1)
        split.addWidget(diff_pane)
        split.setSizes([330, 520])
        body.addWidget(split, stretch=1)

        # Dependencies + status line
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

        # Footer
        foot = QHBoxLayout()
        self.revert_btn = self.make_button("Revert Last Update", p, kind="secondary")
        self.revert_btn.setEnabled(False)
        self.revert_btn.clicked.connect(self._on_revert_clicked)
        foot.addWidget(self.revert_btn)
        self.baseline_btn = self.make_button("Establish Baseline", p, kind="secondary")
        self.baseline_btn.setToolTip(
            "Record the current upstream as the merge base — without changing any "
            "files — so future updates can show YOUR local edits separately from "
            "the update's (the coloured 3-way review). Runs automatically on normal "
            "installs; use this to set it up now.")
        self.baseline_btn.clicked.connect(self._on_baseline_clicked)
        foot.addWidget(self.baseline_btn)
        foot.addStretch()
        self.apply_btn = self.make_button("Apply Selected", p, kind="primary")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        foot.addWidget(self.apply_btn)
        close_btn = self.make_button("Close", p, kind="secondary")
        close_btn.clicked.connect(self.close)
        foot.addWidget(close_btn)
        body.addLayout(foot)
        self._refresh_revert()

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
        self.dev_check.setEnabled(not auto)   # can't disable in an auto-detected dev dir
        self.dev_check.blockSignals(False)
        if auto:
            self._banner("Developer working copy detected (.dev-copy present). Auto-update is "
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
        # Persist as the subscribed branch and refresh version + revert state.
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
            self.diff_view.clear()
        else:
            self._status(f"Revert failed: {result.message}")
        self._refresh_version_label()
        self._refresh_revert()

    # ── actions ───────────────────────────────────────────────────────────────
    def _on_check_clicked(self):
        if self._busy:
            return
        self._plan = None
        self.file_list.clear()
        self.diff_view.clear()
        self.deps_lbl.clear()
        self.commits_view.setVisible(False)
        self.apply_btn.setEnabled(False)
        self._set_busy(True)
        self.service.check(self.branch_combo.currentText())
        # Fetch the stacked commit messages in parallel (best-effort).
        self.service.fetch_pending_commits(self.branch_combo.currentText())

    def _on_commits(self, commits):
        """Render the stacked commit messages (newest first) above the file list."""
        if not commits:
            self.commits_view.setVisible(False)
            return
        p = self._p
        n = len(commits)
        head = (f'<div style="color:{p["muted"]};font-size:10px;padding-bottom:3px">'
                f'{n} pending commit(s), newest first</div>')
        rows = []
        for c in commits[:40]:
            msg = (c.get("message", "") or "").splitlines()
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

        p = self._p
        from PyQt6.QtGui import QColor
        from systema.updater.service import is_sensitive_path
        risky_count = 0
        for fc in plan.file_changes:
            if fc.change.value == "unchanged":
                continue
            colour, tag = _CHANGE_STYLE.get(fc.change.value, (None, fc.change.value.upper()))
            is_text = getattr(fc, "is_text", True)
            # A file has a reviewable textual difference if it's a text file with
            # a content change (added / modified / merged).
            is_change = is_text and fc.change.value in _TEXTDIFF_CHANGES
            # In a user-owned data folder (providers/skills)?
            sensitive = is_sensitive_path(fc.path)
            # Risky-protected = a protected file that would OVERWRITE or DELETE the
            # user's local version (MOD / DEL / MERGE / CONFLICT). A protected NEW
            # only creates a file, so it can't cause data loss and is treated as a
            # normal addition (auto-selectable).
            risky_protected = sensitive and fc.change.value != "added"
            if risky_protected:
                risky_count += 1

            # Two aligned columns: the change tag, then a PROT marker for anything
            # in a protected folder — so both dimensions are visible at a glance.
            prot = "PROT" if sensitive else "    "
            label = f"{tag:8} {prot}  {fc.path}" + ("" if is_text else "   [binary]")
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # Auto-check textual-diff files, but NEVER a risky-protected one — those
            # (a MOD/DEL of a providers/skills file) must be ticked by hand so an
            # update can't silently overwrite or delete the user's work.
            auto = is_change and not risky_protected
            item.setCheckState(Qt.CheckState.Checked if auto else Qt.CheckState.Unchecked)
            item.setData(_ROLE_PATH, fc.path)
            item.setData(_ROLE_IS_CHANGE, is_change)
            item.setData(_ROLE_SENSITIVE, sensitive)
            item.setData(_ROLE_PROTECTED_RISKY, risky_protected)
            if risky_protected:
                # Red warning — this is where silent overwrite / data loss lives.
                item.setForeground(QColor(_SENSITIVE_COLOUR))
                hl = QColor(_SENSITIVE_COLOUR)
                hl.setAlpha(40)
                item.setBackground(hl)
            else:
                if colour:
                    item.setForeground(QColor(colour))
                # Highlight the textual-diff files so they stand out in the list.
                if is_change:
                    hl = QColor(self._p["accent"])
                    hl.setAlpha(55)
                    item.setBackground(hl)
            tip = fc.note or ""
            if risky_protected:
                verb = "delete" if fc.change.value == "deleted" else "overwrite"
                tip = (f"PROTECTED — this file lives in a folder you own "
                       f"(providers / skills). Applying will {verb} your local "
                       "version (possible data loss). It is unticked; review the "
                       "diff and use Manage to resolve it hunk-by-hunk, or tick it "
                       "only if you really want the upstream version. "
                       + (tip if tip else "")).strip()
            elif sensitive:  # protected NEW — additive, safe
                tip = ("New file in a folder you own (providers / skills). It only "
                       "adds a file — nothing of yours is overwritten or deleted. "
                       + (tip if tip else "")).strip()
            if not is_text:
                tip = (tip + "  " if tip else "") + "binary file - no text diff to review"
            if tip:
                item.setToolTip(tip)
            self.file_list.addItem(item)

        if risky_count:
            self._banner(
                f"{risky_count} PROTECTED change(s) would overwrite or delete files "
                "in folders you own (providers / skills). They are unticked and "
                "highlighted — an update won't touch them unless you tick them "
                "yourself, or resolve them hunk-by-hunk via Manage.")

        if plan.dependency_changes:
            # requirements.txt diff (gitplucker >= 0.7): show added / changed /
            # removed distinctly. Older lib versions lack these helpers, so fall
            # back to a flat requirement list.
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
        if plan.conflicts:
            self._status(
                f"{len(plan.conflicts)} conflict(s): you and upstream edited the same lines. "
                "These start unticked - applying one inserts <<<<<<< markers to resolve by hand.")

        # Heuristic: if the local tree diverges a lot from the repo and we're not
        # already in dev mode, the user may be running an ahead-of-repo dev copy.
        if not self.service.is_dev_environment:
            modified = sum(1 for fc in plan.file_changes if fc.change.value == "modified")
            if modified >= 15:
                self._banner(f"Heads up: {modified} files differ from the repo. If your local "
                             "copy is ahead (a development copy), enable Developer mode above so "
                             "the updater stops offering to overwrite your files.")

        if self.file_list.count():
            self.file_list.setCurrentRow(0)
        self._update_sel_count()

    def _on_row_changed(self, current, _previous):
        if current is None or self._plan is None:
            return
        path = current.data(_ROLE_PATH)
        tagged = self.service.review(path)
        self._render_review(tagged)

    def _resolve_colour(self, key: str) -> str:
        """Map a style token to a concrete colour (palette keys stay live)."""
        if key == "text":
            return self._p["text"]
        if key == "muted":
            return self._p["muted"]
        if key == "accent":
            return self._p["accent"]
        return key

    def _legend_html(self, tags: set[str]) -> str:
        """Compact colour legend, only for origins actually present."""
        chips = []
        for tag, label in _LEGEND:
            if tag not in tags:
                continue
            _, bg, _g = _REVIEW_STYLE[tag]
            fg = self._resolve_colour(_REVIEW_STYLE[tag][0])
            chips.append(
                f'<span style="background:{bg};color:{fg};padding:1px 7px;'
                f'border-radius:4px;margin-right:6px">{_esc(label)}</span>')
        if not chips:
            return ""
        return ('<div style="font-family:Consolas,monospace;font-size:10px;'
                'padding:2px 8px 8px">' + "".join(chips) + "</div>")

    def _render_review(self, tagged: list[tuple[str, str]]):
        p = self._p
        self._tagged = tagged   # remembered so the wrap toggle can re-render
        # Single info note (binary / no textual difference / no plan) -> centered.
        if len(tagged) == 1 and tagged[0][0] == "info":
            self._diff_html = (
                f'<div style="color:{p["muted"]};font-family:Consolas,monospace;'
                f'font-size:11px;padding:14px 10px">{_esc(tagged[0][1])}</div>')
            self.diff_view.setHtml(self._diff_html)
            self._sync_expanded()
            return

        # QTextEdit's line-wrap mode alone can't wrap cells styled white-space:pre,
        # so the wrap actually comes from pre-wrap here (kept in sync below).
        ws = "pre-wrap" if self.wrap_check.isChecked() else "pre"
        present = {t for t, _ in tagged}
        rows = []
        for tag, line in tagged:
            fg_key, bg, gutter = _REVIEW_STYLE.get(tag, ("text", "transparent", " "))
            fg = self._resolve_colour(fg_key)
            bold = ";font-weight:600" if tag == "conflict_marker" else ""
            safe = _esc(line.rstrip("\n")) or "&nbsp;"
            rows.append(
                f'<tr><td style="background:{bg};color:{fg};white-space:pre;'
                f'padding:1px 4px;text-align:center;width:14px;opacity:0.7">{gutter}</td>'
                f'<td style="background:{bg};color:{fg};white-space:{ws};word-break:break-all;'
                f'padding:1px 8px{bold}">{safe}</td></tr>')
        self._diff_html = (
            self._legend_html(present)
            + '<table width="100%" cellspacing="0" cellpadding="0" '
            'style="font-family:Consolas,monospace;font-size:11px">'
            + "".join(rows) + "</table>")
        self.diff_view.setHtml(self._diff_html)
        self._sync_expanded()

    # ── manage (line-level review of protected files) ──────────────────────────
    def _open_manage(self):
        if self._plan is None:
            self._status("Run a check first.")
            return
        from systema.updater.service import is_sensitive_path
        # Target files: the currently-selected one + every protected file in the plan.
        paths = []
        cur = self.file_list.currentItem()
        if cur is not None:
            paths.append(cur.data(_ROLE_PATH))
        for i in range(self.file_list.count()):
            pth = self.file_list.item(i).data(_ROLE_PATH)
            if is_sensitive_path(pth) and pth not in paths:
                paths.append(pth)

        from systema.updater.hunks import ReviewSession
        session = ReviewSession()
        for pth in paths:
            tagged = self.service.review(pth)
            if len(tagged) == 1 and tagged[0][0] == "info":
                continue    # binary / no textual diff — nothing to manage
            session.add(pth, tagged, sensitive=is_sensitive_path(pth))
        if not session.files:
            self._status("No text changes to manage for the selected/protected files.")
            return

        from systema.ui.dialogs.update_manage_dialog import UpdateManageDialog
        dlg = UpdateManageDialog(self.controller, session, self._p,
                                 on_save=self._on_managed_save, parent=self)
        dlg.exec()

    def _on_managed_save(self, resolved: dict):
        """Persist the hand-resolved files and drop them from the plan list."""
        written, last_backup = [], ""
        for path, text in resolved.items():
            last_backup = self.service.write_managed_file(path, text)
            written.append(path)
            # Untick + relabel the row so it isn't re-applied by gitplucker.
            for i in range(self.file_list.count()):
                it = self.file_list.item(i)
                if it.data(_ROLE_PATH) == path:
                    it.setCheckState(Qt.CheckState.Unchecked)
                    it.setData(_ROLE_IS_CHANGE, False)
        self._update_sel_count()
        self._status(f"Saved your resolved version of {len(written)} file(s). "
                     f"Backup: {last_backup}. Restart to run them.")
        self._offer_restart()

    # ── viewer: wrap toggle + expand pop-out ───────────────────────────────────
    def _on_wrap_toggled(self, on: bool):
        mode = (QTextEdit.LineWrapMode.WidgetWidth if on
                else QTextEdit.LineWrapMode.NoWrap)
        self.diff_view.setLineWrapMode(mode)
        if getattr(self, "_expanded_view", None) is not None:
            self._expanded_view.setLineWrapMode(mode)
        # Re-render so the HTML white-space matches (pre-wrap vs pre).
        if getattr(self, "_tagged", None):
            self._render_review(self._tagged)

    def _sync_expanded(self):
        """Keep a live pop-out window mirroring the main review."""
        if getattr(self, "_expanded_view", None) is not None:
            self._expanded_view.setHtml(self._diff_html)

    def _open_expanded(self):
        from PyQt6.QtWidgets import QDialog
        if getattr(self, "_expanded_dlg", None) is not None:
            self._expanded_dlg.raise_()
            self._expanded_dlg.activateWindow()
            return
        p = self._p
        dlg = QDialog(self)
        dlg.setWindowTitle("Review changes")
        dlg.resize(900, 680)
        dlg.setStyleSheet(f"QDialog {{ background-color: {p['bg']}; }}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth
                             if self.wrap_check.isChecked()
                             else QTextEdit.LineWrapMode.NoWrap)
        view.setStyleSheet(
            f"QTextEdit {{ background-color: {p['surface']}; border: 1px solid {p['border']};"
            f" border-radius: 8px; padding: 8px; font-family: Consolas, monospace;"
            f" font-size: 12px; color: {p['text']}; }}")
        view.setHtml(getattr(self, "_diff_html", "") or "")
        lay.addWidget(view, stretch=1)
        self._expanded_dlg = dlg
        self._expanded_view = view

        def _clear():
            self._expanded_dlg = None
            self._expanded_view = None
        dlg.finished.connect(lambda *_: _clear())
        dlg.show()

    def _checked_paths(self) -> list[str]:
        out = []
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(_ROLE_PATH))
        return out

    def _set_all_checked(self, checked: bool):
        # "Select all" never auto-ticks a risky-protected file (a MOD/DEL of a
        # providers/skills file) — those stay manual-only. A protected NEW is
        # additive and ticks like any other addition.
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if checked and it.data(_ROLE_PROTECTED_RISKY):
                it.setCheckState(Qt.CheckState.Unchecked)
            else:
                it.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._update_sel_count()

    def _select_changes(self):
        """Tick only the files that have a textual difference (the highlighted ones).
        Risky-protected files (a MOD/DEL of a providers/skills file) are skipped —
        they must be approved by hand — but a protected NEW is additive and ticks."""
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            pick = it.data(_ROLE_IS_CHANGE) and not it.data(_ROLE_PROTECTED_RISKY)
            it.setCheckState(Qt.CheckState.Checked if pick else Qt.CheckState.Unchecked)
        self._update_sel_count()

    def _checked_risky_protected_paths(self) -> list[str]:
        """Selected protected files whose change would overwrite/delete local work
        (MOD/DEL). A protected NEW is additive and is deliberately excluded — it
        needs no scary confirmation."""
        out = []
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if it.checkState() == Qt.CheckState.Checked and it.data(_ROLE_PROTECTED_RISKY):
                out.append(it.data(_ROLE_PATH))
        return out

    def _update_sel_count(self):
        total = self.file_list.count()
        n = len(self._checked_paths())
        self.sel_count_lbl.setText(f"{n} of {total} selected")
        self.apply_btn.setEnabled(n > 0 and not self._busy)
        self.apply_btn.setText("Apply Selected" if n != total or total == 0 else "Apply All")

    def _on_apply_clicked(self):
        if self._busy or self._plan is None:
            return
        selected = self._checked_paths()
        if not selected:
            return

        # Warn if the agent is mid-task — applying rewrites source files the
        # running assistant may be relying on.
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

        # Strong, explicit confirmation when a risky-protected file (a MOD/DEL of a
        # providers/skills file) is selected — this is where silent data loss would
        # happen. Protected NEW files are additive and skip this warning.
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
                "copy — the account or skill you set up can be lost. A backup is "
                "still written to data/updates/, but this is not reversible from "
                "inside the app beyond that snapshot.\n\n"
                "Overwrite these anyway?")
            sbox.addButton("Overwrite them", QMessageBox.ButtonRole.DestructiveRole)
            keep_btn = sbox.addButton("Keep mine", QMessageBox.ButtonRole.RejectRole)
            sbox.setDefaultButton(keep_btn)
            sbox.exec()
            if sbox.clickedButton() is keep_btn:
                return

        total = self.file_list.count()
        only = None if len(selected) == total else selected
        conflicts = [fc.path for fc in self._plan.conflicts if fc.path in selected]

        msg = (f"Update {len(selected)} of {total} changed file(s) "
               f"to {self._plan.target_version}?\n\n"
               "A backup is written to data/updates/ before anything changes.\n"
               "Restart Systema Auxilium afterwards.")
        if conflicts:
            msg += (f"\n\nNote: {len(conflicts)} selected file(s) have conflicts and will be "
                    "written with <<<<<<< markers for you to resolve.")
        if only is None:
            msg += "\n\n(Applying ALL changes advances the version marker.)"
        else:
            msg += "\n\n(Partial update - version marker stays unchanged.)"

        box = QMessageBox(self)
        box.setWindowTitle("Apply update?")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(msg)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self.service.apply(only=only)

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
            self.diff_view.clear()
            self.apply_btn.setEnabled(False)
            self._offer_restart()
        else:
            self._status(f"Update failed: {result.message}"
                         + ("  (changes rolled back)" if result.rolled_back else ""))
        self._refresh_version_label()
        self._refresh_revert()

    def _offer_restart(self):
        """After a successful apply, offer to restart now (auto-reopens) or later."""
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

    # ── loading spinner (shown in the file list while a check runs) ──────────
    _SPIN = ("◐", "◓", "◑", "◒")

    def _start_spinner(self):
        """Show an animated placeholder row so an empty list never looks stuck."""
        self._stop_spinner()
        self._spin_i = 0
        self._loading_item = QListWidgetItem(f"{self._SPIN[0]}   Loading changes…")
        self._loading_item.setFlags(Qt.ItemFlag.NoItemFlags)  # non-selectable
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
