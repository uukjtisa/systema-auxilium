"""
tests/systema/ui/test_ui_layout_guard.py

Applies the headless UI inspector (ui_inspector.py) to the widgets that can be
built without a live controller: painted icon buttons, the input pill, code
blocks, the inline status labels and the inline conflict editor.

Covers the failure classes that logic tests structurally cannot see:
  * widgets colliding or spilling outside their parent,
  * buttons whose label/icon is clipped or truncated,
  * controls wired to nothing ("the button does nothing"),
  * visible widgets with zero area,
and re-runs the geometry checks across ZOOM levels, which is historically where
this app's cards break (the whole zoom_restyle convention exists because of it).
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget  # noqa: E402

from tests.systema.ui import ui_inspector as ui                      # noqa: E402


# ── painted icon buttons (the house icon set) ────────────────────────────────

def _icon_buttons():
    from systema.ui.widgets import painted_icons as pi
    return [pi.SendButton(), pi.MicButton(), pi.MuteButton(), pi.StopButton(),
            pi.PaperclipButton(), pi.MinimizeButton(), pi.MaximizeButton(),
            pi.CloseButton(), pi.TrashButton(), pi.RepeatButton(),
            pi.ClearButton(), pi.TerminalButton(), pi.EyeButton()]


def test_every_painted_button_constructs_and_has_area(qapp):
    """Smoke + zero-size: a painted button with no area is invisible but still
    focusable."""
    host = QWidget()
    lay = QHBoxLayout(host)
    for b in _icon_buttons():
        lay.addWidget(b)
    host.resize(800, 60)
    host.show()
    qapp.processEvents()

    assert ui.zero_sized(host) == []
    assert ui.unresponsive(host) == []
    host.hide()


def test_painted_buttons_are_not_clipped_at_their_natural_size(qapp):
    host = QWidget()
    lay = QHBoxLayout(host)
    for b in _icon_buttons():
        lay.addWidget(b)
    host.resize(900, 64)
    host.show()
    qapp.processEvents()

    problems = ui.clipped(host) + ui.icon_buttons_fit(host)
    assert problems == [], "painted buttons render clipped:\n  " + "\n  ".join(problems)
    host.hide()


def test_buttons_in_a_row_do_not_overlap_each_other(qapp):
    """The collision check proper: a row of controls must tile, not stack."""
    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setSpacing(4)
    for b in _icon_buttons():
        lay.addWidget(b)
    host.resize(800, 60)
    host.show()
    qapp.processEvents()

    problems = ui.collisions(host)
    assert problems == [], "controls overlap:\n  " + "\n  ".join(problems)
    host.hide()


@pytest.mark.parametrize("width", [1200, 800, 520, 360])
def test_a_button_row_survives_being_squeezed(qapp, width):
    """Narrow windows are where collisions actually appear."""
    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setSpacing(4)
    for b in _icon_buttons():
        lay.addWidget(b)
    host.resize(width, 60)
    host.show()
    qapp.processEvents()

    problems = ui.collisions(host) + ui.overflowing(host)
    assert problems == [], (f"at {width}px wide:\n  " + "\n  ".join(problems))
    host.hide()


# ── zoom sweep ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scale", [0.8, 1.0, 1.4, 2.0])
def test_geometry_holds_across_zoom_levels(qapp, scale):
    """Card/button chrome is re-styled per zoom (the zoom_restyle convention);
    a font that grows without its box growing is the classic truncation bug."""
    from systema.ui.widgets import painted_icons as pi

    host = QWidget()
    lay = QHBoxLayout(host)
    for b in (pi.SendButton(), pi.StopButton(), pi.ClearButton()):
        f = b.font()
        f.setPointSizeF(max(6.0, f.pointSizeF() * scale))
        b.setFont(f)
        lay.addWidget(b)
    host.resize(int(600 * scale), int(60 * scale))
    host.show()
    qapp.processEvents()

    problems = ui.collisions(host) + ui.clipped(host) + ui.overflowing(host)
    assert problems == [], (f"at zoom {scale}:\n  " + "\n  ".join(problems))
    host.hide()


# ── dead-control detection (the tester itself must work) ─────────────────────

def test_the_dead_control_check_catches_an_unwired_button(qapp):
    """Guard the guard: a button connected to nothing MUST be reported, or the
    whole 'not responding' check is decorative."""
    from PyQt6.QtWidgets import QPushButton

    host = QWidget()
    lay = QVBoxLayout(host)
    dud = QPushButton("Does nothing")
    dud.setObjectName("dudButton")
    lay.addWidget(dud)
    host.resize(200, 60)
    host.show()
    qapp.processEvents()

    problems = ui.dead(host)
    assert any("dudButton" in p for p in problems), \
        "an unwired button was not reported as dead"
    host.hide()


def test_a_wired_button_is_not_reported_as_dead(qapp):
    from PyQt6.QtWidgets import QPushButton

    host = QWidget()
    lay = QVBoxLayout(host)
    live = QPushButton("Works")
    live.setObjectName("liveButton")
    live.clicked.connect(lambda: None)
    lay.addWidget(live)
    host.resize(200, 60)
    host.show()
    qapp.processEvents()

    assert not any("liveButton" in p for p in ui.dead(host))
    host.hide()


def test_the_collision_check_catches_a_real_overlap(qapp):
    """Guard the guard, part two: deliberately stack two widgets."""
    host = QWidget()
    host.resize(200, 100)
    a = QFrame(host)
    a.setObjectName("boxA")
    a.setGeometry(10, 10, 100, 40)
    b = QFrame(host)
    b.setObjectName("boxB")
    b.setGeometry(50, 20, 100, 40)
    host.show()
    qapp.processEvents()

    problems = ui.collisions(host)
    assert any("boxA" in p and "boxB" in p for p in problems), \
        "two overlapping widgets were not reported"
    host.hide()


def test_the_clipping_check_catches_truncated_button_text(qapp):
    from PyQt6.QtWidgets import QPushButton

    host = QWidget()
    host.resize(300, 60)
    btn = QPushButton("A button label far too long for its box", host)
    btn.setObjectName("squeezed")
    btn.setGeometry(0, 0, 30, 20)          # deliberately far too small
    host.show()
    qapp.processEvents()

    assert any("squeezed" in p for p in ui.clipped(host)), \
        "clipped button text was not reported"
    host.hide()


# ── real app widgets ─────────────────────────────────────────────────────────

def test_inline_status_labels_pass_the_audit(qapp):
    from systema.ui.chat_window import InlineStatus

    host = QWidget()
    lay = QVBoxLayout(host)
    lbl = InlineStatus()
    lbl.setText("••• Working…")
    lay.addWidget(lbl)
    host.resize(300, 40)
    host.show()
    qapp.processEvents()

    problems = ui.overflowing(host) + ui.zero_sized(host)
    assert problems == [], "\n  ".join(problems)
    host.hide()


def test_the_inline_conflict_editor_lays_out_cleanly(qapp):
    """A real, controller-free piece of app UI end to end."""
    from systema.updater.hunks import ReviewSession
    from systema.ui.windows.update_window import ReviewPane

    palette = {"bg": "#0D1117", "surface": "#161B22", "surface2": "#21262D",
               "border": "#30363D", "accent": "#58A6FF", "text": "#E6EDF3",
               "muted": "#8B949E"}
    session = ReviewSession()
    session.add("engine/x.py", [("same", "a\n"), ("conflict_local", "mine\n"),
                                ("conflict_base", "base\n"),
                                ("conflict_remote", "theirs\n")], sensitive=False)
    pane = ReviewPane(palette, lambda k: palette.get(k, k))
    pane.edit_conflict("engine/x.py", session.files["engine/x.py"])
    pane.resize(760, 520)
    pane.show()
    qapp.processEvents()

    problems = ui.overflowing(pane) + ui.zero_sized(pane) + ui.unresponsive(pane)
    assert problems == [], "ReviewPane layout problems:\n  " + "\n  ".join(problems)
    pane.hide()


def test_the_conflict_editors_controls_are_all_wired(qapp):
    """Every button in the conflict editor must actually do something — this
    pane replaced the retired Manage dialog, so a dud control here means an
    unresolvable conflict."""
    from systema.updater.hunks import ReviewSession
    from systema.ui.windows.update_window import ReviewPane

    palette = {"bg": "#0D1117", "surface": "#161B22", "surface2": "#21262D",
               "border": "#30363D", "accent": "#58A6FF", "text": "#E6EDF3",
               "muted": "#8B949E"}
    session = ReviewSession()
    session.add("engine/x.py", [("same", "a\n"), ("conflict_local", "mine\n"),
                                ("conflict_base", "base\n"),
                                ("conflict_remote", "theirs\n")], sensitive=False)
    pane = ReviewPane(palette, lambda k: palette.get(k, k))
    pane.edit_conflict("engine/x.py", session.files["engine/x.py"])
    pane.resize(760, 520)
    pane.show()
    qapp.processEvents()

    problems = ui.dead(pane)
    assert problems == [], "unwired controls:\n  " + "\n  ".join(problems)
    pane.hide()
