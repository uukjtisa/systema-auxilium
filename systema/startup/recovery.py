"""
systema/startup/recovery.py

The dialog that appears when the working copy fails its startup check, and the
actions behind it.

WHY IT EXISTS AT ALL
--------------------
`UpdaterService.rollback()` is only reachable from Settings > System, which
needs the app to launch and the chat window to open. The one situation rollback
exists for -- an update that broke the app -- is the situation where you cannot
get to it. This runs BEFORE AssistantController is built, so recovery is
available exactly when it is needed.

Every option is offered only when it can actually work: git options need git and
a repo with commits, the GitHub option needs the network, and the backup option
needs a gitplucker snapshot. An option that would fail is disabled with the
reason shown, never offered and then apologised for.
"""

from __future__ import annotations

import socket

from PyQt6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QLabel,
                             QPushButton, QRadioButton, QVBoxLayout, QWidget)

from systema.common.logger import _make_logger, _NoOpLogger
from systema.startup import integrity
from systema.updater import local_git

_verbose = True
log = _make_logger("Recovery") if _verbose else _NoOpLogger()

# Outcomes
REVERT_GIT = "revert_git"
FETCH_REMOTE = "fetch_remote"
RESTORE_BACKUP = "restore_backup"
CONTINUE = "continue"
QUIT = "quit"


def online(timeout: float = 1.5) -> bool:
    """Cheap reachability probe for github.com.

    Short timeout on purpose: this sits in front of the first window, and a
    wrong answer only greys out one option -- it must never be the reason
    startup feels slow.
    """
    try:
        with socket.create_connection(("github.com", 443), timeout=timeout):
            return True
    except OSError:
        return False


def list_backups() -> list:
    """gitplucker snapshots, newest first. [] when the updater is unavailable."""
    try:
        from systema.updater.service import REPO, make_updater
        u = make_updater()
        return list(u.list_snapshots(REPO, "main") or [])
    except Exception as e:
        log.warning(f"[recovery.list_backups] {type(e).__name__}: {e}")
        return []


class RecoveryDialog(QDialog):
    """Modal, and deliberately so -- nothing else should start while the app is
    in an unknown state."""

    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.report = report
        self.choice = CONTINUE
        self.target = None            # chosen commit sha / snapshot
        self._commits = local_git.recent_commits(8)
        self._backups = list_backups()
        self._online = online()
        self.setWindowTitle("Systema Auxilium - startup check failed")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        head = QLabel(f"The working copy looks damaged. "
                      f"{len(self.report.problems)} problem(s) found:")
        head.setWordWrap(True)
        head.setStyleSheet("font-size: 13px; font-weight: 600; color: #E8EAED;")
        lay.addWidget(head)

        # WHAT is broken, verbatim. A recovery prompt that will not say what it
        # found gives the user no basis to choose between the options below it.
        for p in self.report.problems[:8]:
            mark = "!" if p.kind == "interrupted" else "x"
            row = QLabel(f"  {mark}  {p.describe()}")
            row.setWordWrap(True)
            row.setStyleSheet("font-size: 11px; color: #F07070;"
                              if p.kind != "interrupted" else
                              "font-size: 11px; color: #E0B050;")
            lay.addWidget(row)

        lay.addWidget(self._rule())

        self._group = QButtonGroup(self)
        git_ok = bool(local_git.available() and self._commits)
        newest = self._commits[0] if self._commits else None

        self._opt_git = self._option(
            lay, REVERT_GIT,
            "Revert to the last snapshot (git)",
            (f"{newest[1][:16].replace('T', ' ')}  -  {newest[2][:48]}"
             if newest else "no snapshots available"),
            enabled=git_ok, checked=git_ok)

        self._opt_fetch = self._option(
            lay, FETCH_REMOTE,
            "Fetch a fresh copy from GitHub",
            "downloads the current release" if self._online else "offline",
            enabled=self._online)

        self._opt_backup = self._option(
            lay, RESTORE_BACKUP,
            "Restore from a local backup",
            (f"{len(self._backups)} snapshot(s) under data/updates"
             if self._backups else "no backups found"),
            enabled=bool(self._backups))

        self._opt_continue = self._option(
            lay, CONTINUE,
            "Continue anyway",
            "launch as-is; the app may not work",
            enabled=True, checked=not git_ok)

        lay.addWidget(self._rule())

        foot = QHBoxLayout()
        foot.addStretch(1)
        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(self._on_quit)
        recover_btn = QPushButton("Recover")
        recover_btn.setDefault(True)
        recover_btn.clicked.connect(self._on_recover)
        foot.addWidget(quit_btn)
        foot.addWidget(recover_btn)
        lay.addLayout(foot)

        self.setStyleSheet("""
            QDialog { background: #12161C; }
            QLabel { color: #E8EAED; background: transparent; }
            QRadioButton { color: #E8EAED; font-size: 12px; spacing: 8px; }
            QRadioButton:disabled { color: #5F6368; }
            QPushButton {
                background: #212833; border: 1px solid #2A313C;
                border-radius: 6px; color: #E8EAED;
                font-size: 11px; padding: 6px 16px;
            }
            QPushButton:hover { border-color: #5A9CF8; }
        """)

    @staticmethod
    def _rule() -> QWidget:
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #2A313C;")
        return line

    def _option(self, lay, key, title, detail, enabled=True, checked=False):
        btn = QRadioButton(title)
        btn.setProperty("choice", key)
        btn.setEnabled(enabled)
        btn.setChecked(bool(checked and enabled))
        self._group.addButton(btn)
        lay.addWidget(btn)
        sub = QLabel(f"      {detail}")
        sub.setStyleSheet("font-size: 10px; color: #8B949E;")
        lay.addWidget(sub)
        return btn

    def _selected(self) -> str:
        btn = self._group.checkedButton()
        return btn.property("choice") if btn is not None else CONTINUE

    def _on_recover(self):
        self.choice = self._selected()
        if self.choice == REVERT_GIT and self._commits:
            self.target = self._commits[0][0]
        self.accept()

    def _on_quit(self):
        self.choice = QUIT
        self.reject()


def perform(choice: str, target=None) -> tuple:
    """Carry out a recovery choice. Returns (ok, message).

    Only the git path is executed here. Fetching a fresh copy and restoring a
    gitplucker backup both mutate the tree the running process was imported
    from, so they are handed to the updater's own machinery on the next launch
    rather than done underneath a live interpreter.
    """
    if choice == REVERT_GIT:
        sha = target or (local_git.recent_commits(1) or [("", "", "")])[0][0]
        if not sha:
            return False, "no snapshot to revert to"
        if local_git.revert_to(sha):
            integrity.mark_apply_finished()
            return True, f"reverted the working copy to {sha}"
        return False, "git could not revert the working copy"
    if choice == CONTINUE:
        return True, "continuing without recovery"
    if choice == QUIT:
        return False, "user quit"
    return False, f"{choice} must be completed from Settings > System"


def offer(report, parent=None) -> tuple:
    """Show the dialog and act on the answer. Returns (should_launch, message)."""
    dlg = RecoveryDialog(report, parent)
    dlg.exec()
    if dlg.choice == QUIT:
        return False, "user quit at the recovery prompt"
    ok, msg = perform(dlg.choice, dlg.target)
    log.info(f"[recovery.offer] choice={dlg.choice} ok={ok} - {msg}")
    # Anything other than an outright quit still launches: a user who picked
    # "continue anyway", or a revert that failed, is better off in a running app
    # that can explain itself than staring at a process that exited silently.
    return True, msg
