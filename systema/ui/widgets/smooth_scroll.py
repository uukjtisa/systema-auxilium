"""
systema/ui/widgets/smooth_scroll.py

ONE smooth-scrolling implementation for every scrollable view in the app.

Replaces the old per-view velocity+friction inertia, which was smooth but not
*aimable*: a single wheel notch added 120*0.38 = 45.6 px/tick of velocity which,
under 0.86 friction, integrated to roughly 326 px — about a third of a viewport.
Anything less than a third of a screen away was unreachable: nudge up and you
overshot past it, nudge back down and you overshot again. Long outputs had spots
you simply could not park on.

This is TARGET-based instead, driven by a continuous exponential approach: a
notch moves the TARGET, and a 60fps loop eases the real position toward it. Two
consequences that matter:

  * every position on the page is reachable — a gentle, isolated notch travels
    exactly one small step, so you can park anywhere;
  * motion never restarts. An earlier attempt started a fresh QPropertyAnimation
    per notch, which reset the easing curve mid-glide and read as choppy. Here a
    new notch only enlarges the remaining distance, so the glide stays smooth
    however fast the notches arrive.

Fast flicks still cross long pages quickly via rate-based acceleration, and the
position is tracked as a FLOAT so slow scrolling is not lost to integer rounding.

Usage:
    from systema.ui.widgets.smooth_scroll import install_smooth_scroll
    install_smooth_scroll(some_scroll_area)

    # a view that pins itself to the bottom (the chat) passes a callback so it
    # can drop the pin the instant the user scrolls UP:
    install_smooth_scroll(chat_area, on_user_scroll=self._on_user_scrolled)
"""
from PyQt6.QtCore import QEvent, QObject, QTimer, Qt
from PyQt6.QtWidgets import QAbstractScrollArea

# A single DELIBERATE notch — roughly two text lines. Deliberately small: slow,
# gentle scrolling is where precision matters, and a big fixed step is what made
# positions unreachable in the first place.
WHEEL_STEP_PX = 90
# Frame interval for the glide loop (~60fps).
TICK_MS = 16
# Fraction of the remaining distance covered per tick. This is an EXPONENTIAL
# approach, not a fixed-duration animation: adding to the target mid-glide just
# makes the remaining distance larger, so motion stays continuous instead of
# restarting its easing curve — restarting is what made it feel choppy.
# 0.22 settles a gentle notch in ~290ms: unhurried, but without the long
# sub-pixel tail where the view is technically still moving and visibly is not.
SMOOTHING = 0.22
# Below this the remainder is not worth another frame — snap and stop.
SNAP_PX = 1.0
# Rate-based acceleration. Notches further apart than IDLE_GAP_S are treated as
# separate, deliberate movements and always travel exactly one step. Faster
# flicks scale up smoothly to MAX_ACCEL so long pages stay quick to cross.
IDLE_GAP_S = 0.22
MAX_ACCEL = 3.0


class SmoothScroller(QObject):
    """Target-based smooth wheel scrolling for one QAbstractScrollArea."""

    def __init__(self, area: QAbstractScrollArea, step_px: int = WHEEL_STEP_PX,
                 on_user_scroll=None):
        super().__init__(area)
        self._area = area
        self._step = int(step_px)
        self._on_user_scroll = on_user_scroll
        self._target = None
        self._pos = None            # float mirror of the bar (sub-pixel)
        self._last_wheel = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        area.viewport().installEventFilter(self)

    # ── internals ────────────────────────────────────────────────────────────

    def _bar(self):
        return self._area.verticalScrollBar()

    def _running(self) -> bool:
        return self._timer.isActive()

    def _current_target(self, bar) -> float:
        """Where we are heading. If no glide is in flight, that is simply where
        we are — this is what makes consecutive notches ADD UP instead of each
        one restarting from the current (mid-glide) position."""
        if self._target is None or not self._running():
            return float(bar.value())
        return self._target

    def stop(self):
        self._timer.stop()
        self._target = None
        self._pos = None

    def _accel(self) -> float:
        """1.0 for a deliberate, isolated notch; up to MAX_ACCEL for a flick.

        Without this, one step small enough to aim with would make long pages
        painful to cross — and one big enough to cross them fast is exactly the
        unaimable behaviour this class replaced.
        """
        import time
        now = time.monotonic()
        gap = now - self._last_wheel
        self._last_wheel = now
        if gap >= IDLE_GAP_S:
            return 1.0
        return 1.0 + (1.0 - gap / IDLE_GAP_S) * (MAX_ACCEL - 1.0)

    def _tick(self):
        """One frame of the exponential approach."""
        try:
            bar = self._bar()
        except RuntimeError:
            self.stop()
            return
        if self._target is None or self._pos is None:
            self.stop()
            return

        # Something else moved the bar (keyboard, programmatic scroll, a resize
        # clamping the range) — follow it rather than fighting it.
        if abs(self._pos - bar.value()) > max(2.0, self._step):
            self._pos = float(bar.value())

        remaining = self._target - self._pos
        if abs(remaining) <= SNAP_PX:
            bar.setValue(int(round(self._target)))
            self.stop()
            return

        self._pos += remaining * SMOOTHING
        new_value = int(round(self._pos))
        if new_value != bar.value():
            bar.setValue(new_value)
        # Clamped by the widget (hit an edge) — nothing more to travel.
        if bar.value() in (bar.minimum(), bar.maximum()) and (
                (remaining < 0 and bar.value() == bar.minimum())
                or (remaining > 0 and bar.value() == bar.maximum())):
            self.stop()

    def scroll_by(self, pixels: float):
        """Glide by a number of pixels, accumulating with any glide already in
        flight. Continuous: the target moves, the motion never restarts."""
        bar = self._bar()
        base = self._current_target(bar)
        target = max(float(bar.minimum()),
                     min(float(bar.maximum()), base + float(pixels)))
        if abs(target - bar.value()) <= SNAP_PX:
            self.stop()
            return
        if self._pos is None or not self._running():
            self._pos = float(bar.value())
        self._target = target
        if not self._timer.isActive():
            self._timer.start()

    # ── event filter ─────────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        try:
            if obj is not self._area.viewport() or event.type() != QEvent.Type.Wheel:
                return False
        except RuntimeError:
            return False

        # Ctrl+wheel belongs to whoever owns zoom — never consume it here.
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False

        bar = self._bar()
        if bar.maximum() <= bar.minimum():
            return False                      # nothing to scroll

        pixel = event.pixelDelta()
        angle = event.angleDelta()

        if not pixel.isNull():
            # Trackpads / precision wheels already report exact pixels and are
            # continuous — applying them directly IS the smooth path; animating
            # would add lag on top of an already-fluid gesture.
            dy = -pixel.y()
            if self._on_user_scroll:
                self._on_user_scroll(dy)
            self.stop()
            bar.setValue(max(bar.minimum(), min(bar.maximum(), bar.value() + dy)))
            return True

        notches = angle.y() / 120.0
        if not notches:
            return False
        dy = -notches * self._step * self._accel()
        if self._on_user_scroll:
            self._on_user_scroll(dy)
        self.scroll_by(dy)
        return True


def install_smooth_scroll(area: QAbstractScrollArea, step_px: int = WHEEL_STEP_PX,
                          on_user_scroll=None):
    """Attach smooth scrolling to a scroll area. Returns the SmoothScroller
    (kept alive as a child of the area). Safe to call once per area."""
    existing = getattr(area, "_smooth_scroller", None)
    if existing is not None:
        return existing
    scroller = SmoothScroller(area, step_px, on_user_scroll)
    area._smooth_scroller = scroller
    return scroller
