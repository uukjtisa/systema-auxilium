"""
systema/ui/widgets/smooth_scroll.py

ONE smooth-scrolling implementation for every scrollable view in the app, and
the SINGLE OWNER of a view's vertical position while motion is in flight.

History of what did not work, so it is not tried again:

  1. Per-view velocity + friction inertia. Smooth but not *aimable*: one notch
     integrated to ~326px, so anything less than a third of a screen away was
     unreachable — nudge up, overshoot; nudge back, overshoot again.
  2. A fresh QPropertyAnimation per notch. Reset the easing curve mid-glide,
     which read as choppy.
  3. A target-based EXPONENTIAL approach (pos += remaining * 0.22). Aimable and
     continuous, but it reaches MAXIMUM VELOCITY ON FRAME ONE — position moves
     19px in the first 16ms and decays from there. That instant start is what
     read as "snappy" on slow, deliberate scrolling, and no amount of constant
     tuning removes it: it is inherent to the model.

This is a CRITICALLY-DAMPED SPRING (Unity's SmoothDamp, from Game Programming
Gems 4). Velocity is carried as state between frames, which buys three things
the exponential approach cannot:

  * motion RAMPS IN — the first frames are slow, so a single gentle notch eases
    instead of snapping. This is the actual fix for the reported problem;
  * re-targeting mid-flight PRESERVES VELOCITY, so a fast flick accumulates
    into continuous motion instead of restarting a curve;
  * it mathematically cannot overshoot, so no rubber-band wobble at the ends.

One spring serves wheel, trackpad, and programmatic scrolls, each with its own
smoothing time — which is why nothing fights anything else any more. A
programmatic glide that lands while the user is flicking simply moves the
target the spring is already chasing.

Usage:
    from systema.ui.widgets.smooth_scroll import install_smooth_scroll
    install_smooth_scroll(some_scroll_area)

    # a view that pins itself to the bottom (the chat) passes a callback so it
    # can drop the pin the instant the user scrolls UP:
    s = install_smooth_scroll(chat_area, on_user_scroll=self._on_user_scrolled)
    s.scroll_to(target)          # animated programmatic scroll
    s.follow_bottom()            # track a growing bottom while streaming
"""
import time

from PyQt6.QtCore import QEvent, QObject, QTimer, Qt
from PyQt6.QtWidgets import QAbstractScrollArea

# A single DELIBERATE notch — roughly three text lines. Deliberately modest:
# slow, precise scrolling is where aiming matters, and a big fixed step is what
# made positions unreachable in the very first implementation.
WHEEL_STEP_PX = 100

# ── Smoothing times (seconds to cover ~63% of the remaining distance) ─────────
# Each surface gets its own, because "smooth" means something different for a
# discrete wheel notch than for a continuous trackpad gesture.
SMOOTH_TIME_WHEEL = 0.135        # discrete notches — the eased, gliding feel
SMOOTH_TIME_PROGRAM = 0.280      # scroll-to-message — a deliberate, visible glide
SMOOTH_TIME_FOLLOW = 0.090       # sticky-bottom while streaming — tight tracking

# Below this the remainder is not worth another frame — snap and stop.
SNAP_PX = 0.5
# Rate-based acceleration. Notches further apart than IDLE_GAP_S are separate,
# deliberate movements and always travel exactly one step; faster flicks scale
# smoothly to MAX_ACCEL so long pages stay quick to cross.
IDLE_GAP_S = 0.22
MAX_ACCEL = 3.2
# Frame budget fallback when the screen refresh rate cannot be read.
DEFAULT_TICK_MS = 16


def _tick_ms() -> int:
    """Frame interval matched to the display, so the glide is as smooth as the
    panel allows — 16ms on a 60Hz screen, ~7ms on 144Hz. A fixed 16ms leaves
    high-refresh monitors visibly steppier than they need to be."""
    try:
        from PyQt6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            hz = float(screen.refreshRate())
            if hz >= 24.0:
                return max(4, min(DEFAULT_TICK_MS, int(round(1000.0 / hz))))
    except Exception:
        pass
    return DEFAULT_TICK_MS


def _smooth_damp(current: float, target: float, velocity: float,
                 smooth_time: float, dt: float) -> tuple[float, float]:
    """One frame of a critically-damped spring. Returns (position, velocity).

    This is the closed-form solution, not an Euler step — it is stable at any
    dt, so a dropped frame or a slow repaint cannot make it explode or ring.
    """
    smooth_time = max(1e-4, smooth_time)
    omega = 2.0 / smooth_time
    x = omega * dt
    # Padé approximation of exp(-x) — the standard SmoothDamp cheap exponential.
    decay = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
    change = current - target
    temp = (velocity + omega * change) * dt
    velocity = (velocity - omega * temp) * decay
    return target + (change + temp) * decay, velocity


class SmoothScroller(QObject):
    """Spring-driven smooth scrolling for one QAbstractScrollArea.

    While a glide is in flight this object OWNS the vertical scrollbar value.
    Anything else that wants to move the view should go through scroll_to() /
    follow_bottom() rather than calling setValue(), or the two will fight.
    """

    def __init__(self, area: QAbstractScrollArea, step_px: int = WHEEL_STEP_PX,
                 on_user_scroll=None):
        super().__init__(area)
        self._area = area
        self._step = int(step_px)
        self._on_user_scroll = on_user_scroll
        self._target = None
        self._pos = None            # float mirror of the bar (sub-pixel)
        self._vel = 0.0             # carried between frames — the whole point
        self._smooth = SMOOTH_TIME_WHEEL
        self._following_bottom = False
        self._last_wheel = 0.0
        self._last_frame = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_tick_ms())
        self._timer.timeout.connect(self._tick)
        area.viewport().installEventFilter(self)

    # ── internals ────────────────────────────────────────────────────────────

    def _bar(self):
        return self._area.verticalScrollBar()

    def _running(self) -> bool:
        return self._timer.isActive()

    @property
    def is_animating(self) -> bool:
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
        self._vel = 0.0
        self._following_bottom = False

    def _accel(self, notches: float) -> float:
        """1.0 for a deliberate, isolated notch; up to MAX_ACCEL for a flick.

        Without this, a step small enough to aim with makes long pages painful
        to cross — and one big enough to cross them fast is exactly the
        unaimable behaviour this class was written to replace.

        High-resolution wheels (sub-notch deltas, common on modern mice) are
        excluded: they already report fine-grained movement, so scaling them
        by arrival rate would double-count the user's speed.
        """
        now = time.monotonic()
        gap = now - self._last_wheel
        self._last_wheel = now
        if abs(notches) < 0.99:
            return 1.0
        if gap >= IDLE_GAP_S:
            return 1.0
        return 1.0 + (1.0 - gap / IDLE_GAP_S) * (MAX_ACCEL - 1.0)

    def _start(self):
        if not self._timer.isActive():
            self._last_frame = time.monotonic()
            self._timer.start()

    def _tick(self):
        """One frame of the spring."""
        try:
            bar = self._bar()
        except RuntimeError:
            self.stop()
            return
        if self._target is None or self._pos is None:
            self.stop()
            return

        now = time.monotonic()
        dt = now - self._last_frame
        self._last_frame = now
        # A stalled GUI thread (a long repaint, a modal) must not teleport the
        # view when it comes back — clamp to a few frames' worth of time.
        dt = max(1e-4, min(dt, 0.05))

        # Sticky-bottom: the content is still growing under us, so re-aim at
        # the live maximum every frame instead of the value captured at start.
        if self._following_bottom:
            self._target = float(bar.maximum())

        # Something else moved the bar (keyboard, a widget scrolling itself
        # into view, a resize clamping the range) — follow it rather than
        # fighting it, and drop the stale velocity that belonged to the old
        # position.
        if abs(self._pos - bar.value()) > max(2.0, self._step):
            self._pos = float(bar.value())
            self._vel = 0.0

        self._pos, self._vel = _smooth_damp(
            self._pos, self._target, self._vel, self._smooth, dt)

        remaining = self._target - self._pos
        if abs(remaining) <= SNAP_PX and abs(self._vel) < 12.0:
            bar.setValue(int(round(self._target)))
            self.stop()
            return

        new_value = int(round(self._pos))
        if new_value != bar.value():
            bar.setValue(new_value)

        # Clamped by the widget (hit an edge) — nothing more to travel. Kill the
        # velocity too, or the spring keeps pushing into the wall.
        at_min = bar.value() == bar.minimum() and remaining < 0
        at_max = bar.value() == bar.maximum() and remaining > 0
        if at_min or (at_max and not self._following_bottom):
            self.stop()

    # ── public API ───────────────────────────────────────────────────────────

    def scroll_by(self, pixels: float, smooth_time: float = SMOOTH_TIME_WHEEL):
        """Glide by a number of pixels, accumulating with any glide already in
        flight. Continuous: the target moves, the motion never restarts."""
        bar = self._bar()
        base = self._current_target(bar)
        self._following_bottom = False
        target = max(float(bar.minimum()),
                     min(float(bar.maximum()), base + float(pixels)))
        if abs(target - bar.value()) <= SNAP_PX:
            # The new target IS where we already are — cancel any glide still
            # in flight rather than returning early. Without the stop(), a
            # notch down followed by a notch back up left the DOWNWARD target
            # live and the view kept travelling the wrong way.
            self.stop()
            return
        if self._pos is None or not self._running():
            self._pos = float(bar.value())
            self._vel = 0.0
        self._smooth = smooth_time
        self._target = target
        self._start()

    def scroll_to(self, value: float, smooth_time: float = SMOOTH_TIME_PROGRAM,
                  immediate: bool = False):
        """Animated programmatic scroll to an absolute bar value.

        Replaces the old per-call QPropertyAnimation: because it feeds the SAME
        spring the wheel does, a user notch arriving mid-glide blends into the
        motion instead of stopping one animation and starting another.
        """
        bar = self._bar()
        target = max(float(bar.minimum()), min(float(bar.maximum()), float(value)))
        self._following_bottom = False
        if immediate:
            self.stop()
            bar.setValue(int(round(target)))
            return
        if abs(target - bar.value()) <= SNAP_PX:
            self.stop()         # already there — drop any stale glide
            return
        if self._pos is None or not self._running():
            self._pos = float(bar.value())
            self._vel = 0.0
        self._smooth = smooth_time
        self._target = target
        self._start()

    def follow_bottom(self, smooth_time: float = SMOOTH_TIME_FOLLOW):
        """Track the bottom of GROWING content (streaming replies, work cards).

        Re-aims at bar.maximum() every frame, so text arriving in chunks reads
        as one continuous crawl rather than the per-chunk teleport that
        setValue(maximum()) produced.
        """
        bar = self._bar()
        if self._pos is None or not self._running():
            self._pos = float(bar.value())
            self._vel = 0.0
        self._smooth = smooth_time
        self._following_bottom = True
        self._target = float(bar.maximum())
        self._start()

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
            # CONTINUOUS — the finger is the animation. Applying them directly
            # IS the smooth path here; a spring on top would only add lag to a
            # gesture that is already fluid, which is why browsers do not
            # animate trackpad scrolling either. (Tried routing these through
            # the spring during the 2026-07-27 rewrite — it made the gesture
            # feel detached from the finger. Locked by a test.)
            #
            # A glide in flight is stopped first so the two never fight.
            dy = -pixel.y()
            if self._on_user_scroll:
                self._on_user_scroll(dy)
            self.stop()
            bar.setValue(max(bar.minimum(), min(bar.maximum(), bar.value() + dy)))
            return True

        notches = angle.y() / 120.0
        if not notches:
            return False
        dy = -notches * self._step * self._accel(notches)
        if self._on_user_scroll:
            self._on_user_scroll(dy)
        self.scroll_by(dy, SMOOTH_TIME_WHEEL)
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


def scroller_for(area: QAbstractScrollArea):
    """The SmoothScroller owning this area, installing one if needed. Use this
    from programmatic scroll paths so they share the wheel's spring instead of
    running a competing animation."""
    if area is None:
        return None
    try:
        return install_smooth_scroll(area)
    except (RuntimeError, AttributeError):
        return None
