"""
ui/dialogs/voice_setup_dialog.py
VoiceSetupDialog — the compact microphone picker shown when the user turns voice
mode on from the chat mic button.

Contents: a de-duplicated microphone dropdown, a live input-level meter (confirm
the mic is heard before committing), a "Don't show this again" toggle, and
Cancel / Save. Save persists the chosen mic (+ the toggle) and returns Accepted
so the caller starts voice mode; Cancel returns Rejected and changes nothing —
voice mode stays OFF. Whether this popup appears at all is governed by
settings['voice_setup_prompt_enabled'].
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)

from systema.ui import theme as _theme
from systema.ui.widgets.mic_level_meter import MicLevelMeter
from systema.ui.dialogs.dialog_utils import center_on_primary


class VoiceSetupDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        try:
            self._p = _theme.current_palette(controller)
        except Exception:
            self._p = _theme.resolve_palette(_theme.THEMES[_theme.DEFAULT_THEME_KEY])
        self._init_ui()
        # Rescan first so devices hotplugged since app start appear (PortAudio's
        # list is frozen until re-init; our meter isn't running yet).
        try:
            controller.rescan_audio_devices()
        except Exception:
            pass
        self._populate_devices()
        QTimer.singleShot(0, lambda: center_on_primary(self))
        QTimer.singleShot(0, self._start_meter)

        # Live hotplug watch while the dialog is open (e.g. pairing earbuds now).
        self._hotplug_signature = None
        self._poll_tick = 0
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._on_device_poll)
        self._poll_timer.start()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _init_ui(self):
        p = self._p
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(460, 300)
        self.setModal(True)

        container = QWidget()
        container.setObjectName("container")
        container.setStyleSheet(f"""
            QWidget#container {{
                background-color: {p['surface']}; border: 1px solid {p['border']};
                border-radius: 12px;
            }}
            QWidget {{ color: {p['text']}; font-family: 'Segoe UI', system-ui, sans-serif; }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(24, 20, 24, 20)
        inner.setSpacing(12)

        title = QLabel("Microphone setup")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {p['text']}; "
                            f"background: transparent;")
        inner.addWidget(title)

        desc = QLabel("Pick the microphone voice mode should use, and watch the "
                      "level bar to confirm it's being heard.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 12px; color: {p['muted']}; background: transparent;")
        inner.addWidget(desc)

        combo_qss = f"""
            QComboBox {{
                background-color: {p['surface2']}; color: {p['text']};
                border: 1px solid {p['border']}; border-radius: 8px;
                padding: 7px 10px; font-size: 12px;
            }}
            QComboBox:hover {{ border-color: {p['accent']}; }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: {p['surface2']}; border: 1px solid {p['border']};
                color: {p['text']}; selection-background-color: {p['accent']};
                selection-color: {p['bg']};
            }}
        """
        self.input_combo = QComboBox()
        self.input_combo.setStyleSheet(combo_qss)
        self.input_combo.currentIndexChanged.connect(self._on_input_changed)
        inner.addWidget(self.input_combo)

        self.meter = MicLevelMeter(self.controller)
        inner.addWidget(self.meter)

        self.dont_show_checkbox = QCheckBox("Don't show this again")
        self.dont_show_checkbox.setStyleSheet(
            f"QCheckBox {{ color: {p['muted']}; font-size: 12px; background: transparent; }}")
        inner.addWidget(self.dont_show_checkbox)

        inner.addStretch()

        _BTN = f"""
            QPushButton {{
                background-color: {p['surface2']}; border: 1px solid {p['border']};
                border-radius: 6px; padding: 8px 16px; font-size: 11px;
                font-weight: 500; color: {p['text']};
            }}
            QPushButton:hover {{ border-color: {p['accent']}; color: {p['accent']}; }}
        """
        _BTN_SAVE = f"""
            QPushButton {{
                background-color: {p['accent']}; border: 1px solid {p['accent']};
                border-radius: 6px; padding: 8px 16px; font-size: 11px;
                font-weight: 600; color: {p['bg']};
            }}
            QPushButton:hover {{ background-color: {p.get('accent_lt', p['accent'])}; }}
        """

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(_BTN)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setStyleSheet(_BTN_SAVE)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        inner.addLayout(btn_row)

    # ── devices + meter ────────────────────────────────────────────────────────

    def _populate_devices(self):
        try:
            inputs, _ = self.controller.get_voice_devices()
        except Exception:
            inputs = []
        self._inputs = inputs
        self.input_combo.blockSignals(True)
        self.input_combo.clear()
        self.input_combo.addItem("Default Microphone", None)
        saved = self.controller.settings.get('voice_input_device')
        for dev in inputs:
            label = dev['name'] + ("  - Default" if dev.get('is_default') else "")
            self.input_combo.addItem(label, dev['name'])
        idx = self.input_combo.findData(saved)
        self.input_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.input_combo.blockSignals(False)

    def _resolve_selected_id(self):
        name = self.input_combo.currentData()
        try:
            return self.controller.voice_handler._resolve_input_id(name)
        except Exception:
            return None

    def _start_meter(self):
        self.meter.start(self._resolve_selected_id())

    def _on_input_changed(self, _index):
        self._start_meter()

    def _on_device_poll(self):
        """Refresh the dropdown when the device population changes (hotplug).
        Mirrors settings_window._on_device_poll: live winmm probe on Windows,
        periodic rescan elsewhere; meter stopped across the PortAudio re-init."""
        try:
            sig = self.controller.audio_hotplug_signature()
            if sig is not None:
                if sig == self._hotplug_signature:
                    return
                changed = self._hotplug_signature is not None
                self._hotplug_signature = sig
                if not changed:
                    return  # first tick: baseline only
            else:
                self._poll_tick += 1
                if self._poll_tick % 5:
                    return
            self.meter.stop()
            self.controller.rescan_audio_devices()
            self._populate_devices()
            self._start_meter()
        except Exception:
            pass

    # ── actions ────────────────────────────────────────────────────────────────

    def _teardown(self):
        """Stop the hotplug poll + meter — a late poll tick after close would
        otherwise restart the mic stream on a dead dialog."""
        try:
            self._poll_timer.stop()
        except Exception:
            pass
        try:
            self.meter.stop()
        except Exception:
            pass

    def _save(self):
        name = self.input_combo.currentData()
        try:
            self.controller.set_voice_input_device(name)
            if self.dont_show_checkbox.isChecked():
                self.controller.settings['voice_setup_prompt_enabled'] = False
            self.controller.save_settings()
        except Exception:
            pass
        self._teardown()
        self.accept()

    def _cancel(self):
        self._teardown()
        self.reject()

    def closeEvent(self, event):
        # X button = Cancel: never leaks the stream, never enables voice.
        self._teardown()
        super().closeEvent(event)
