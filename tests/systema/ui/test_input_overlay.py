"""
Tests for the floating input overlay's bottom-anchored auto-grow
(systema/ui/chat_window.py :: ChatWindow._position_input_overlay).

The input pill is a floating overlay parented to the chat container. When the
typed text auto-grows the pill it must stay glued to the chat's bottom edge and
grow UPWARD (top moves up, bottom fixed) — never spill downward off the window.
Growth re-anchors synchronously via MultiLineInput.heightChanged.
"""
import types

import pytest

pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import (  # noqa: E402
    QFrame, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from systema.ui.chat_window import ChatWindow  # noqa: E402
from systema.ui.widgets.inputs import ResizableInput  # noqa: E402


def _build(qapp, cc_h=500, cc_w=600):
    """Replicate the real overlay nesting: chat_container > input_container(VBox)
    > combined(VBox SetMin) > row(HBox SetMin) > ResizableInput."""
    cc = QWidget()
    cc.resize(cc_w, cc_h)
    ic = QFrame()
    ic.setParent(cc)
    il = QVBoxLayout(ic)
    il.setContentsMargins(14, 8, 14, 12)
    il.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
    combined = QFrame()
    combined.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    cbl = QVBoxLayout(combined)
    cbl.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
    cbl.setContentsMargins(0, 0, 0, 0)
    row = QWidget()
    rl = QHBoxLayout(row)
    rl.setContentsMargins(16, 10, 16, 4)
    rl.setSizeConstraint(QHBoxLayout.SizeConstraint.SetMinimumSize)
    inp = ResizableInput()
    rl.addWidget(inp, 1)
    cbl.addWidget(row)
    # A bottom action row of fixed-size buttons — like the real pill.
    brow = QWidget()
    brl = QHBoxLayout(brow)
    brl.setContentsMargins(10, 0, 10, 8)
    for _ in range(2):
        b = QPushButton("x")
        b.setFixedSize(30, 30)
        brl.addWidget(b)
    brl.addStretch()
    cbl.addWidget(brow)
    il.addWidget(combined)

    inner = QWidget()
    cl = QVBoxLayout(inner)
    cl.setContentsMargins(0, 16, 0, 16)

    stub = types.SimpleNamespace(
        input_container=ic, _input_card=combined, _chat_container=cc, chat_layout=cl,
        input_field=inp,
        _position_input_handles=lambda: None,  # width grab-bars (no-op in the mock)
        # Empty-session opener absent in the mock, so the pill anchors to the
        # bottom edge exactly as it does in a session with messages.
        _session_intro_showing=lambda: False,
        _chat_layout_host=inner,  # keep the layout's widget alive (GC → dead layout)
    )
    # The geometry math lives in ONE place now (_measure_input_overlay); both
    # the eager pass and the settle pass call it, so the mock binds it too.
    stub._measure_input_overlay = types.MethodType(
        ChatWindow._measure_input_overlay, stub)
    stub._position_input_overlay = types.MethodType(
        ChatWindow._position_input_overlay, stub)
    stub._position_input_overlay_settle = types.MethodType(
        ChatWindow._position_input_overlay_settle, stub)
    # Wire growth exactly like the real app: synchronous re-anchor.
    inp.text_input.heightChanged.connect(lambda: stub._position_input_overlay())
    cc.show()
    qapp.processEvents()
    stub._position_input_overlay()
    qapp.processEvents()
    return stub, cc, ic, inp


def test_overlay_bottom_anchored_when_empty(qapp):
    stub, cc, ic, inp = _build(qapp)
    g = ic.geometry()
    assert g.y() + g.height() == cc.height()  # glued to bottom edge


def test_overlay_grows_upward_not_downward(qapp):
    stub, cc, ic, inp = _build(qapp)
    bottom0 = ic.geometry().y() + ic.geometry().height()
    top0 = ic.geometry().y()

    inp.text_input.setPlainText("a\nb\nc\nd\ne\nf")
    qapp.processEvents()

    g = ic.geometry()
    # Bottom stays pinned to the chat's bottom edge...
    assert g.y() + g.height() == cc.height()
    assert g.y() + g.height() == bottom0
    # ...the container got taller...
    assert g.height() > 24
    # ...and it grew UPWARD (top moved up, never down/off the bottom).
    assert g.y() < top0


def test_overlay_stays_compact_after_vertical_snap(qapp):
    """Vertical-maximize (drag-to-top) makes the chat area much taller. The empty
    pill must NOT stretch to fill it — it stays compact and pinned to the bottom
    (the reported 'pill balloons and clips off the bottom' bug)."""
    stub, cc, ic, inp = _build(qapp, cc_h=500)
    compact = ic.geometry().height()

    cc.resize(cc.width(), 900)
    qapp.processEvents()
    stub._position_input_overlay()
    qapp.processEvents()

    g = ic.geometry()
    assert g.height() == compact          # did not balloon
    assert g.y() + g.height() == cc.height()  # still glued to the (new) bottom


def test_overlay_self_corrects_from_oversized_rect(qapp):
    """Even if the container is handed a too-tall rect mid-resize, the next
    re-anchor shrinks it back to the pill's content height."""
    stub, cc, ic, inp = _build(qapp, cc_h=900)
    compact = ic.geometry().height()

    ic.setGeometry(0, 400, cc.width(), 300)  # bogus tall rect
    qapp.processEvents()
    stub._position_input_overlay()
    qapp.processEvents()

    g = ic.geometry()
    assert g.height() == compact
    assert g.y() + g.height() == cc.height()


def test_overlay_grows_upward_on_manual_drag(qapp):
    """Dragging the resize handle taller must also grow the pill UPWARD (not
    spill downward). The drag sets a temporary floor and emits heightChanged
    directly — mirror that sequence here."""
    stub, cc, ic, inp = _build(qapp)
    top0 = ic.geometry().y()

    # Reproduce ResizableInput.eventFilter's drag branch:
    inp.text_input._user_floor = 160
    inp.text_input.setFixedHeight(160)
    inp.updateGeometry()
    inp.parent().updateGeometry()
    inp.parent().parent().updateGeometry()
    inp.text_input.heightChanged.emit()
    qapp.processEvents()

    g = ic.geometry()
    assert g.y() + g.height() == cc.height()  # bottom stays pinned
    assert g.height() > 100                    # got taller
    assert g.y() < top0                        # grew upward


def test_overlay_reanchors_synchronously_on_growth(qapp):
    """heightChanged fires a direct (non-deferred) re-anchor, so the overlay is
    correct without waiting for a queued event."""
    stub, cc, ic, inp = _build(qapp)
    inp.text_input.setPlainText("one\ntwo\nthree")
    # NOTE: no processEvents here — must already be re-anchored synchronously.
    g = ic.geometry()
    assert g.y() + g.height() == cc.height()
    assert g.height() > 24


def test_overlay_collapses_after_send_clear(qapp):
    """Paste-long-then-send regression: send_message calls ResizableInput.clear(),
    which shrinks the text box — the OVERLAY must collapse with it instead of
    staying suspended mid-screen on stale layout caches."""
    stub, cc, ic, inp = _build(qapp)
    h0 = ic.geometry().height()

    inp.text_input.setPlainText("line\n" * 12)   # auto-grow tall
    qapp.processEvents()
    assert ic.geometry().height() > h0

    inp.clear()                                   # exactly what send_message calls
    qapp.processEvents()

    g = ic.geometry()
    assert g.height() == h0                       # pill back to one line
    assert g.y() + g.height() == cc.height()      # glued to the bottom again
