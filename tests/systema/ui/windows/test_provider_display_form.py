"""
tests/systema/ui/windows/test_provider_display_form.py

The dynamic per-provider settings form (Settings ▸ AI Provider) built from a
provider script's `Display` dict. Locks the widget chosen per field type, the
dropdown affordance (an editable, stylesheet-themed QComboBox renders as a
bare text box unless it paints its own chevron — that regression shipped once),
secret masking, and value collection round-trips.
"""
import pytest

from systema.ui.windows.settings_window import SettingsWindow

_STYLES = {
    'input': "QLineEdit, QTextEdit { border: 1px solid #2A313C; }",
    'combo': "QComboBox { border: 1px solid #2A313C; }",
    'combo_edit': ("QComboBox { border: 1px solid #2A313C; padding-right: 26px; }"
                   "QComboBox QLineEdit { background: transparent; border: none; }"),
    'btn': "QPushButton { border: 1px solid #2A313C; }",
    'check': "QCheckBox { color: #E6EDF3; }",
    'text': '#E6EDF3', 'muted': '#8B949E', 'border': '#2A313C',
}


class _Host:
    """Minimal stand-in carrying only what _build_display_row touches."""
    _prov_form_styles = _STYLES

    def __init__(self):
        self._prov_display_widgets = {}
        self._rows = []          # keep rows alive; in the app the layout does

    def build(self, *args):
        row = SettingsWindow._build_display_row(self, *args)
        self._rows.append(row)
        return row


@pytest.fixture
def host(qapp):
    h = _Host()
    h.collect = SettingsWindow._collect_provider_display_values.__get__(h, SettingsWindow)
    return h


def _widget(host, var):
    return host._prov_display_widgets[var][1]


# ── widget selection per type ────────────────────────────────────────────────

@pytest.mark.parametrize("ftype,extra,current,expected", [
    ("input", None, "v", "QLineEdit"),
    ("number", None, 5, "QLineEdit"),
    ("file_path", None, "c:/x", "QLineEdit"),
    ("textarea", None, "t", "QPlainTextEdit"),
    ("checkbox", None, True, "QCheckBox"),
    ("list_dropdown", ["a", "b"], "a", "ChevronCombo"),
])
def test_row_builds_expected_editor(host, ftype, extra, current, expected):
    host.build("VAR", "Label", ftype, extra, {}, current)
    assert type(_widget(host, "VAR")).__name__ == expected


def test_info_box_renders_note_and_registers_nothing(host):
    row = host.build("NOTE_1", "NOTE: read me.", "info_box", None, {}, "")
    assert type(row).__name__ == "QLabel"
    assert row.text() == "NOTE: read me."
    assert host._prov_display_widgets == {}          # never persisted


# ── dropdown affordance + behaviour ──────────────────────────────────────────

def test_dropdown_behaves_as_a_dropdown_not_a_text_box(host):
    host.build("MODEL", "Model", "list_dropdown", ["a", "b"], {}, "a")
    combo = _widget(host, "MODEL")
    # NEVER editable: an editable combo drops a text cursor on click instead
    # of opening the list — that regression shipped once.
    assert not combo.isEditable()
    assert combo.lineEdit() is None
    assert [combo.itemText(i) for i in range(combo.count())] == ["a", "b", "Custom…"]
    assert combo.currentText() == "a"
    # The chevron is painted by the widget: QSS ::down-arrow needs an image
    # resource and styling ::drop-down kills the native arrow, so a plain
    # QComboBox here would render with no dropdown affordance at all.
    from PyQt6.QtWidgets import QComboBox
    from systema.ui.widgets.painted_icons import ChevronCombo
    assert isinstance(combo, ChevronCombo)
    assert ChevronCombo.paintEvent is not QComboBox.paintEvent
    assert combo.ARROW_W > 0
    assert "padding-right" in combo.styleSheet()


def test_preset_value_collects_and_hides_the_custom_input(host):
    host.build("MODEL", "Model", "list_dropdown", ["a", "b"], {}, "b")
    combo, custom = host._prov_display_widgets["MODEL"][1:3]
    assert combo.currentText() == "b"
    assert custom.isHidden()
    assert host.collect()["MODEL"] == "b"


def test_saved_value_outside_presets_selects_custom_and_reveals_input(host):
    host.build("MODEL", "Model", "list_dropdown", ["a", "b"], {}, "vendor/custom-id")
    combo, custom = host._prov_display_widgets["MODEL"][1:3]
    assert combo.currentText() == "Custom…"
    assert not custom.isHidden()
    assert custom.text() == "vendor/custom-id"
    assert host.collect()["MODEL"] == "vendor/custom-id"


def test_switching_to_custom_reveals_the_input_and_back_hides_it(host):
    row = host.build("MODEL", "Model", "list_dropdown", ["a", "b"], {}, "a")
    row.show()                       # visibility is only meaningful once shown
    combo, custom = host._prov_display_widgets["MODEL"][1:3]
    assert not custom.isVisible()
    combo.setCurrentIndex(combo.count() - 1)      # Custom…
    assert custom.isVisible()
    combo.setCurrentIndex(0)
    assert not custom.isVisible()


def test_blank_custom_value_is_not_persisted(host):
    host.build("MODEL", "Model", "list_dropdown", ["a", "b"], {}, "a")
    combo = _widget(host, "MODEL")
    combo.setCurrentIndex(combo.count() - 1)      # Custom…, input left empty
    assert "MODEL" not in host.collect()


def test_dropdown_item_tooltips_attach_in_order(host):
    from PyQt6.QtCore import Qt
    host.build("MODEL", "Model", "list_dropdown", ["a", "b"],
               {"item_tooltips": ["first", "second"]}, "a")
    combo = _widget(host, "MODEL")
    assert combo.itemData(0, Qt.ItemDataRole.ToolTipRole) == "first"
    assert combo.itemData(1, Qt.ItemDataRole.ToolTipRole) == "second"


# ── tooltips / placeholders / masking ────────────────────────────────────────

def test_tooltip_and_placeholder_applied(host):
    host.build("API_URL", "API URL", "input", None,
               {"tooltip": "where to call", "placeholder": "https://..."}, "")
    w = _widget(host, "API_URL")
    assert w.toolTip() == "where to call"
    assert w.placeholderText() == "https://..."


def test_masking_follows_the_declared_type_not_the_variable_name(host):
    """secure_input masks; input never does — even when the name screams
    'key'. Name-sniffing masked innocent fields and missed real secrets."""
    from PyQt6.QtWidgets import QLineEdit
    host.build("API_KEY", "API Key", "secure_input", None, {}, "sk-secret")
    host.build("API_KEY_NAME", "Key name", "input", None, {}, "prod-key")
    host.build("MODEL_NAME", "Model", "input", None, {}, "gpt")
    assert _widget(host, "API_KEY").echoMode() == QLineEdit.EchoMode.Password
    assert _widget(host, "API_KEY_NAME").echoMode() == QLineEdit.EchoMode.Normal
    assert _widget(host, "MODEL_NAME").echoMode() == QLineEdit.EchoMode.Normal


def test_secure_input_collects_its_value_like_a_plain_input(host):
    host.build("API_KEY", "API Key", "secure_input", None, {}, "sk-live-value")
    assert host.collect()["API_KEY"] == "sk-live-value"


def test_masked_field_gets_a_painted_eye_toggle_not_a_text_button(host):
    """The reveal control is icon-only: the old text button clipped its
    letters in this narrow row."""
    from PyQt6.QtWidgets import QLineEdit
    from systema.ui.widgets.painted_icons import EyeButton

    row = host.build("API_KEY", "API Key", "secure_input", None, {}, "sk-secret")
    eyes = row.findChildren(EyeButton)
    assert len(eyes) == 1
    eye = eyes[0]
    assert eye.isCheckable()
    assert eye.text() == ""                    # no letters to clip
    assert eye.toolTip() == "Show"

    field = _widget(host, "API_KEY")
    eye.setChecked(True)
    assert field.echoMode() == QLineEdit.EchoMode.Normal
    assert eye.toolTip() == "Hide"
    eye.setChecked(False)
    assert field.echoMode() == QLineEdit.EchoMode.Password


def test_plain_input_has_no_eye_toggle(host):
    from systema.ui.widgets.painted_icons import EyeButton
    assert host.build("MODEL", "Model", "input", None, {}, "gpt").findChildren(EyeButton) == []
    assert host.build("KEYWORD", "Keyword", "input", None, {}, "x").findChildren(EyeButton) == []


# ── collection ───────────────────────────────────────────────────────────────

def test_collect_returns_typed_values(host):
    host.build("API_KEY", "API Key", "input", None, {}, "sk-1")
    host.build("MODEL", "Model", "list_dropdown", ["a", "b"], {}, "b")  # preset
    host.build("MAX_TOKENS", "Max tokens", "number", None, {}, 16384)
    host.build("TEMP", "Temperature", "number", None, {}, 0.7)
    host.build("FLAG", "Flag", "checkbox", None, {}, True)
    host.build("TXT", "Text", "textarea", None, {}, "hello")
    host.build("NOTE_1", "note", "info_box", None, {}, "")

    vals = host.collect()
    assert vals == {"API_KEY": "sk-1", "MODEL": "b", "MAX_TOKENS": 16384,
                    "TEMP": 0.7, "FLAG": True, "TXT": "hello"}
    assert isinstance(vals["MAX_TOKENS"], int)
    assert isinstance(vals["TEMP"], float)


def test_collect_skips_blank_number(host):
    host.build("MAX_TOKENS", "Max tokens", "number", None, {}, "")
    assert "MAX_TOKENS" not in host.collect()


# ── streaming dependent-visibility (System ▸ AI Response × UI ▸ typing) ───────

class _VisStub:
    """Stand-in for the settings window's visibility-driven widgets."""
    class _W:
        def __init__(self): self.visible = None
        def setVisible(self, b): self.visible = b

    class _Chk(_W):
        def __init__(self, on): super().__init__(); self._on = on
        def isChecked(self): return self._on

    def __init__(self, supported, enabled):
        self._streaming_widget = self._W()
        self._streaming_unsupported_note = self._W()
        self._typing_reveal_widget = self._W()
        self._typing_streaming_note = self._W()
        self.streaming_checkbox = self._Chk(enabled)
        self._active_provider_streams = lambda: supported
        SettingsWindow._update_streaming_visibility(self)


def test_streaming_toggle_hidden_when_provider_cannot_stream(qapp):
    s = _VisStub(supported=False, enabled=True)
    assert s._streaming_widget.visible is False        # hidden, not greyed
    assert s._streaming_unsupported_note.visible is True
    # typing reveal is still the only animation available → stays visible
    assert s._typing_reveal_widget.visible is True
    assert s._typing_streaming_note.visible is False


def test_typing_reveal_hidden_while_streaming_is_live(qapp):
    s = _VisStub(supported=True, enabled=True)
    assert s._streaming_widget.visible is True
    assert s._typing_reveal_widget.visible is False    # the stream IS the reveal
    assert s._typing_streaming_note.visible is True


def test_typing_reveal_returns_when_streaming_is_switched_off(qapp):
    s = _VisStub(supported=True, enabled=False)
    assert s._streaming_widget.visible is True
    assert s._typing_reveal_widget.visible is True
    assert s._typing_streaming_note.visible is False
