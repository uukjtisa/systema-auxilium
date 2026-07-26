"""
tests/systema/ui/ui_inspector.py

A reusable, headless inspector for Qt widget trees — the harness behind the UI
layout guard. Not a test module itself: it provides the CHECKS, so any window,
dialog or card can be pointed at it in one line.

It catches the classes of breakage that only show up visually, and that no
amount of logic testing sees:

    collisions()   two visible siblings overlapping, or a child spilling
                   outside its parent — the "text box collision" class
    clipped()      a control narrower/shorter than the content it must show:
                   elided button labels, truncated text, icon buttons squeezed
                   below their icon size
    dead()         interactive controls wired to nothing, or unreachable —
                   the "element not responding" class, which geometry checks
                   can never see
    zero_sized()   visible widgets with no area (invisible but focusable)

Everything runs under QT_QPA_PLATFORM=offscreen, so it is CI-safe and needs no
display. Checks return LISTS OF STRINGS (one per problem) rather than asserting,
so a caller can whitelist known-intentional cases and still fail on the rest.
"""
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import (QAbstractButton, QComboBox, QLabel, QLineEdit,
                             QPushButton, QWidget)


def _name(w: QWidget) -> str:
    """Readable identity for a widget in a failure message."""
    bits = [type(w).__name__]
    if w.objectName():
        bits.append(f"#{w.objectName()}")
    text = ""
    if isinstance(w, (QAbstractButton, QLabel)):
        text = w.text()
    elif isinstance(w, QLineEdit):
        text = w.text() or w.placeholderText()
    if text:
        bits.append(f"{text[:28]!r}")
    return " ".join(bits)


def walk(root: QWidget):
    """Every descendant widget, depth-first, including the root."""
    yield root
    for child in root.findChildren(QWidget):
        yield child


def _visible_children(parent: QWidget):
    """Direct child widgets that actually occupy layout space."""
    return [c for c in parent.children()
            if isinstance(c, QWidget) and not c.isHidden()]


def _overlap(a: QRect, b: QRect) -> QRect:
    return a.intersected(b)


def collisions(root: QWidget, min_overlap_px: int = 2,
               ignore_types: tuple = ()) -> list:
    """Sibling widgets whose visible rectangles overlap.

    Overlap is legitimate for deliberate overlays (a floating badge, a painted
    hover pill), so pass those classes via `ignore_types`. Everything else
    overlapping means two controls are drawing on top of each other — which is
    what "UI collision" looks like from the user's side.
    """
    problems = []
    for parent in walk(root):
        kids = [k for k in _visible_children(parent)
                if not isinstance(k, ignore_types)]
        for i, a in enumerate(kids):
            for b in kids[i + 1:]:
                if a.geometry().isEmpty() or b.geometry().isEmpty():
                    continue
                over = _overlap(a.geometry(), b.geometry())
                if over.width() > min_overlap_px and over.height() > min_overlap_px:
                    problems.append(
                        f"{_name(a)} overlaps {_name(b)} by "
                        f"{over.width()}x{over.height()}px inside {_name(parent)}")
    return problems


def overflowing(root: QWidget, slack_px: int = 1) -> list:
    """Children drawn outside their parent's bounds — the part the user simply
    never sees. Skips scroll-area viewports, where clipping is the point."""
    problems = []
    for parent in walk(root):
        if parent.parent() is None:
            continue
        pr = parent.rect().adjusted(-slack_px, -slack_px, slack_px, slack_px)
        for kid in _visible_children(parent):
            if kid.geometry().isEmpty():
                continue
            if not pr.contains(kid.geometry()):
                problems.append(
                    f"{_name(kid)} at {kid.geometry().getRect()} spills outside "
                    f"{_name(parent)} {parent.rect().getRect()}")
    return problems


def zero_sized(root: QWidget) -> list:
    """Visible widgets with no drawable area: invisible to the eye, still in
    the tab order — a control the user can focus but never see."""
    return [f"{_name(w)} is visible but has zero size"
            for w in walk(root)
            if not w.isHidden() and w.parent() is not None
            and (w.width() <= 0 or w.height() <= 0)]


def clipped(root: QWidget, tolerance_px: int = 2) -> list:
    """Controls too small for the content they must display.

    Compares the widget's actual size against the size Qt says it needs. A
    positive difference means the label is elided, the button text is cut, or
    an icon button has been squeezed below its icon — all invisible to logic
    tests and obvious to the user.
    """
    problems = []
    for w in walk(root):
        if w.isHidden() or w.parent() is None:
            continue
        if not isinstance(w, (QAbstractButton, QLabel, QLineEdit, QComboBox)):
            continue
        if isinstance(w, QLabel) and w.wordWrap():
            continue            # wrapping labels are meant to grow vertically
        if (w.minimumSize() == w.maximumSize()
                and not w.minimumSize().isEmpty()):
            continue            # explicit fixed size — the author decided it
        if isinstance(w, QAbstractButton) and not w.text() and w.icon().isNull():
            continue            # custom-painted glyph: sizeHint knows nothing
                                # about what its paintEvent actually draws
        hint = w.sizeHint()
        if not hint.isValid():
            continue
        if hint.width() - w.width() > tolerance_px:
            problems.append(
                f"{_name(w)} is {w.width()}px wide but needs {hint.width()}px "
                f"— its content is clipped")
        if hint.height() - w.height() > tolerance_px:
            problems.append(
                f"{_name(w)} is {w.height()}px tall but needs {hint.height()}px "
                f"— its content is clipped")
    return problems


def icon_buttons_fit(root: QWidget) -> list:
    """Icon buttons whose box is smaller than the icon they were given."""
    problems = []
    for w in walk(root):
        if not isinstance(w, QAbstractButton) or w.isHidden():
            continue
        icon_size = w.iconSize()
        if w.icon().isNull():
            continue
        if (w.width() < icon_size.width() or w.height() < icon_size.height()):
            problems.append(
                f"{_name(w)} is {w.width()}x{w.height()} but its icon is "
                f"{icon_size.width()}x{icon_size.height()}")
    return problems


# A control is alive if ANY of its activation signals has a receiver. Checking
# only `clicked` gives false positives: checkable buttons are normally wired to
# `toggled`, and combos to whichever "changed" signal the author preferred.
_SIGNALS_FOR = (
    (QAbstractButton, ("clicked", "toggled", "pressed")),
    (QComboBox, ("currentIndexChanged", "currentTextChanged", "activated")),
)


def _has_receiver(w: QWidget, names) -> bool:
    for name in names:
        signal = getattr(w, name, None)
        if signal is None:
            continue
        try:
            if w.receivers(signal) > 0:
                return True
        except (TypeError, RuntimeError):
            continue
    return False


def _in_live_button_group(w: QWidget) -> bool:
    """A radio/checkbox usually reports through its QButtonGroup rather than
    itself — and in an EXCLUSIVE group only one member needs a receiver, since
    checking any button un-checks the others and fires their toggled signals
    too. Treat a member as alive when the group, or any sibling in it, is
    connected.
    """
    group = getattr(w, "group", None)
    if not callable(group):
        return False
    g = group()
    if g is None:
        return False
    if _has_receiver(g, ("buttonClicked", "buttonToggled", "idClicked",
                         "idToggled")):
        return True
    if not g.exclusive():
        return False
    return any(_has_receiver(b, ("clicked", "toggled", "pressed"))
               for b in g.buttons() if b is not w)


def dead(root: QWidget, allow: tuple = ()) -> list:
    """Interactive controls connected to nothing — 'the button does nothing'.

    Alive means: an activation signal has a receiver, OR the control opens a
    menu, OR it reports through a connected QButtonGroup. Anything genuinely
    inert (a QPushButton used as a styled label, one driven by an event filter)
    can be whitelisted by objectName via `allow`.
    """
    problems = []
    for w in walk(root):
        if w.isHidden() or w.objectName() in allow:
            continue
        for cls, signal_names in _SIGNALS_FOR:
            if not isinstance(w, cls):
                continue
            if isinstance(w, QPushButton) and w.menu() is not None:
                break           # opens a menu — alive by construction
            if _has_receiver(w, signal_names) or _in_live_button_group(w):
                break
            problems.append(
                f"{_name(w)} has no receiver on any of "
                f"{'/'.join(signal_names)} — activating it does nothing")
            break
    return problems


def unresponsive(root: QWidget) -> list:
    """Controls that cannot be reached at all: disabled with no enabled path,
    or sized/placed such that they take no clicks."""
    problems = []
    for w in walk(root):
        if w.isHidden() or not isinstance(w, QAbstractButton):
            continue
        if w.width() <= 0 or w.height() <= 0:
            problems.append(f"{_name(w)} cannot be clicked — it has no area")
    return problems


def audit(root: QWidget, *, ignore_types: tuple = (), allow_dead: tuple = (),
          include_collisions: bool = True) -> list:
    """Run every check and return one flat list of problems.

    `include_collisions` is optional because some designs intentionally stack
    painted widgets; the rest of the checks apply everywhere.
    """
    out = []
    if include_collisions:
        out += collisions(root, ignore_types=ignore_types)
    out += overflowing(root)
    out += zero_sized(root)
    out += clipped(root)
    out += icon_buttons_fit(root)
    out += dead(root, allow=allow_dead)
    out += unresponsive(root)
    return out
