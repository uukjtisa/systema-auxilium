"""systema/common/perf_monitor.py

GUI-responsiveness instrumentation (perf initiative Phase 0).

HitchMonitor
    A 100 ms heartbeat QTimer stamps a shared timestamp on the GUI thread.
    A daemon WATCHDOG thread checks that stamp every 50 ms; when the GUI
    thread stops beating (event loop blocked), the watchdog repeatedly
    samples the GUI thread's live Python stack via sys._current_frames().
    When the stall ends it:
      - writes a full report (blocked ms, sampled stacks with counts, every
        frame as ``path.py:line func()``) to data/logs/perf_reports/
      - logs one WARNING to the session log naming the top culprit frame
        (the most-sampled deepest frame inside systema/) + the report path.
    Sampling only runs while blocked — idle cost is a timestamp compare.

span(label)
    Context manager that times a suspect operation and logs it when slow:

        from systema.common.perf_monitor import span
        with span("load_session"):
            ...

Pure logging — no UI. Cross-OS, stdlib + QtCore only.
"""
from __future__ import annotations

import sys
import functools
import threading
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer

from systema import APP_ROOT
from systema.common.logger import _make_logger

log = _make_logger("Perf")

# A span slower than this logs at INFO; 4x slower logs at WARNING.
SLOW_MS = 50
# Heartbeat gap (beyond the interval itself) considered a GUI hitch.
HITCH_MS = 200

REPORT_DIR = Path(APP_ROOT) / "data" / "logs" / "perf_reports"
MAX_REPORTS = 50          # oldest pruned beyond this
MAX_SAMPLES = 60          # stack samples kept per hitch (~3 s of stall)

# Open `span(...)` labels per thread, innermost last. The watchdog reads the
# GUI thread's entry to answer the question a Python stack cannot when the
# block is inside Qt/C++: 30 of 50 reports on 2026-08-02 named nothing but
# `main.py:main()`, which made the single largest category of hang
# unattributable. list.append/pop are atomic under the GIL, and this is
# diagnostics — a torn read costs a slightly wrong label, never correctness.
_OPEN_SPANS: "dict[int, list[str]]" = {}

_NL = chr(10)


def _rel(path: str) -> str:
    """Logger-style relative path (systema/ui/chat/bubbles.py) when inside the
    app root; the absolute path otherwise."""
    try:
        return Path(path).resolve().relative_to(Path(APP_ROOT).resolve()).as_posix()
    except Exception:
        return path


class HitchMonitor(QObject):
    """Heartbeat + stack-sampling GUI-thread block detector.
    Create AND start it on the GUI thread."""

    INTERVAL_MS = 100
    _WATCH_TICK = 0.05        # watchdog check cadence (s)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gui_thread_id = threading.get_ident()
        self._last_beat = time.perf_counter()
        self._worst_ms = 0.0
        self._hitches = 0
        self._running = False
        # Uptime + per-hitch history. The "the UI gets worse the longer it runs"
        # report was argued about for weeks because no report carried the one
        # number that settles it: the hitch rate EARLY vs LATE in the same run.
        # Each entry is (uptime_seconds, blocked_ms); the header renders both
        # windows so a future reader answers the question from the file alone.
        self._t0 = time.perf_counter()
        self._events: "list[tuple[float, float]]" = []
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._beat)

    def start(self):
        self._last_beat = time.perf_counter()
        self._t0 = time.perf_counter()
        self._running = True
        self._timer.start()
        threading.Thread(target=self._watchdog, daemon=True,
                         name="hitch-watchdog").start()
        log.info(f"[HitchMonitor.start] heartbeat {self.INTERVAL_MS} ms, "
                 f"hitch threshold {HITCH_MS} ms, reports -> "
                 f"{_rel(str(REPORT_DIR))}/")

    def stop(self):
        self._running = False
        self._timer.stop()

    def stats(self) -> dict:
        return {"hitches": self._hitches, "worst_ms": round(self._worst_ms)}

    # ── GUI thread: just stamp the clock ─────────────────────────────────────

    def _beat(self):
        self._last_beat = time.perf_counter()

    # ── Watchdog thread: detect, sample, report ──────────────────────────────

    def _watchdog(self):
        samples: list[tuple] = []          # stack tuples sampled during a stall
        stall_started = None
        while self._running:
            time.sleep(self._WATCH_TICK)
            gap = time.perf_counter() - self._last_beat
            blocked_ms = (gap * 1000.0) - self.INTERVAL_MS
            if blocked_ms > HITCH_MS / 2:
                # Mid-stall: sample the GUI thread's live Python stack.
                if stall_started is None:
                    stall_started = self._last_beat
                if len(samples) < MAX_SAMPLES:
                    stack = self._sample_gui_stack()
                    if stack is not None:
                        samples.append(stack)
            elif stall_started is not None:
                # Stall just ended — the heartbeat is stamping again.
                total_ms = (time.perf_counter() - stall_started) * 1000.0 \
                    - self.INTERVAL_MS
                if total_ms >= HITCH_MS and samples:
                    self._report(total_ms, samples)
                samples = []
                stall_started = None

    def _sample_gui_stack(self):
        """One (file, line, func) tuple-stack of the GUI thread, or None."""
        try:
            frame = sys._current_frames().get(self._gui_thread_id)
            if frame is None:
                return None
            return tuple((f.filename, f.lineno, f.name)
                         for f in traceback.extract_stack(frame))
        except Exception:
            return None

    def _open_span(self):
        """Innermost span open on the GUI thread, or None."""
        try:
            stack = _OPEN_SPANS.get(self._gui_thread_id)
            return stack[-1] if stack else None
        except (IndexError, AttributeError):
            return None

    @staticmethod
    def _is_native_stall(culprit_frame) -> bool:
        """True when the deepest APP frame is the entry point itself — i.e. the
        block is inside a native/Qt call with no Python frame above it."""
        try:
            f, _, fn = culprit_frame
            return fn == "main" and Path(f).name == "main.py"
        except Exception:
            return False

    def _culprit(self, stack: tuple) -> tuple:
        """The deepest frame inside the app (else the deepest frame)."""
        app_root = str(Path(APP_ROOT).resolve()).lower()
        for f in reversed(stack):
            try:
                if str(Path(f[0]).resolve()).lower().startswith(app_root):
                    return f
            except Exception:
                continue
        return stack[-1] if stack else ("<native/Qt internals>", 0, "?")

    # ── Degradation trend (the "does it get worse with uptime?" answer) ─────
    _TREND_WINDOW_S = 600.0        # 10 minutes per comparison window
    _WARMUP_S = 60.0               # startup import tax — not part of any decay curve

    def _trend_lines(self, up_s: float) -> list:
        """Header lines that make the degradation claim falsifiable from the file.

        Compares the hitch rate AND the mean stall in an EARLY window against a
        LATE one. Two deliberate choices, both learned from the 2026-08-19 data:

        - The first _WARMUP_S of a run are excluded. Every run's worst hitches
          are cold imports at startup; counting them as the "early" baseline
          makes a healthy run look like it is improving and hides real decay.
        - A verdict is only issued when BOTH axes agree. On 2026-08-19 the rate
          rose 0.40 -> 0.90/min while the mean stall FELL 2248 -> 531 ms; that is
          a user interacting more, not an app rotting, and an instrument that
          calls it "degrading" is worse than no instrument.
        """
        ev = self._events
        rate = (60.0 * len(ev) / up_s) if up_s > 0 else 0.0
        head = (f"Uptime {self._fmt_uptime(up_s)} | {len(ev)} hitch(es) | "
                f"{rate:.2f}/min overall")
        if len(ev) >= 2:
            head += f" | {ev[-1][0] - ev[-2][0]:.1f}s since previous"
        out = [head]

        w, warm = self._TREND_WINDOW_S, self._WARMUP_S
        if up_s < warm + 2 * w:
            need = self._fmt_uptime(warm + 2 * w)
            out.append(f"Trend: needs {need} uptime to compare early vs late "
                       f"(have {self._fmt_uptime(up_s)})")
            return out

        early = [d for (t, d) in ev if warm <= t <= warm + w]
        late = [d for (t, d) in ev if t >= up_s - w]
        e_rate, l_rate = 60.0 * len(early) / w, 60.0 * len(late) / w
        e_mean = (sum(early) / len(early)) if early else 0.0
        l_mean = (sum(late) / len(late)) if late else 0.0
        out.append(f"Trend (excl. first {int(warm)}s warm-up): "
                   f"early {e_rate:.2f}/min mean {e_mean:.0f} ms | "
                   f"late {l_rate:.2f}/min mean {l_mean:.0f} ms")

        if len(late) < 3 or len(early) < 3:
            out.append("Verdict: too few hitches in a window to call it")
            return out
        rate_worse = l_rate > e_rate * 1.5
        rate_better = l_rate * 1.5 < e_rate
        dur_worse = l_mean > e_mean * 1.5
        dur_better = l_mean * 1.5 < e_mean
        if rate_worse and not dur_better:
            out.append("Verdict: DEGRADING with uptime (hitches more frequent)")
        elif dur_worse and not rate_better:
            out.append("Verdict: DEGRADING with uptime (hitches longer)")
        elif (rate_worse and dur_better) or (dur_worse and rate_better):
            out.append("Verdict: MIXED — one axis worse, the other better; "
                       "most likely a change in activity, not decay")
        else:
            out.append("Verdict: no degradation with uptime (late ~= early)")
        return out

    @staticmethod
    def _fmt_uptime(sec: float) -> str:
        sec = int(max(0.0, sec))
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"

    def _report(self, blocked_ms: float, samples: list):
        self._hitches += 1
        self._worst_ms = max(self._worst_ms, blocked_ms)
        up_s = time.perf_counter() - self._t0
        self._events.append((up_s, float(blocked_ms)))

        # Aggregate identical stacks; order by sample count.
        counts: dict[tuple, int] = {}
        for s in samples:
            counts[s] = counts.get(s, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])

        top_stack = ranked[0][0]
        cframe = self._culprit(top_stack)
        cf, cl, cn = cframe
        culprit = f"{_rel(cf)}:{cl} {cn}()"

        # Attribution for the no-Python-frame case: name the operation that was
        # in flight, since the stack cannot.
        open_span = self._open_span()
        native = self._is_native_stall(cframe)
        if native:
            culprit = (f"[native/Qt call] in span '{open_span}'" if open_span
                       else f"{culprit} [native/Qt call, no span open]")

        ts = time.strftime("%Y-%m-%d_%H-%M-%S")
        name = f"hitch_{ts}_{int(blocked_ms)}ms.txt"
        path = REPORT_DIR / name
        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            lines = [
                f"UI HITCH REPORT — {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"GUI thread blocked ~{blocked_ms:.0f} ms "
                f"(hitch #{self._hitches} this run, worst {self._worst_ms:.0f} ms)",
                f"Stack samples: {len(samples)} at ~{int(self._WATCH_TICK*1000)} ms cadence",
                *self._trend_lines(up_s),
                "Culprit (deepest app frame of the most-sampled stack):",
                f"    {culprit}",
                "",
            ]
            if native:
                lines += [
                    "The GUI thread was inside a NATIVE (Qt/C++) call — there is no",
                    "Python frame above main(), so the stack below cannot name it.",
                    (f"Operation in flight: span '{open_span}'." if open_span else
                     "No span was open. Wrap the suspect operation in "
                     "perf_monitor.span('label') to attribute the next one."),
                    "",
                ]
            elif open_span:
                lines += [f"Innermost open span: '{open_span}'", ""]
            for i, (stack, n) in enumerate(ranked, 1):
                pct = 100.0 * n / len(samples)
                lines.append(f"── Stack {i} — {n}/{len(samples)} samples "
                             f"({pct:.0f}%) " + "─" * 30)
                for f, l, fn in stack:
                    lines.append(f"    {_rel(f)}:{l} {fn}()")
                lines.append("")
            path.write_text("\n".join(lines), encoding="utf-8")
            self._prune_reports()
            where = _rel(str(path))
        except Exception as e:
            where = f"(report write failed: {e})"

        log.warning(f"[HitchMonitor] GUI thread blocked ~{blocked_ms:.0f} ms — "
                    f"culprit {culprit} | report: {where}")

    @staticmethod
    def _prune_reports():
        try:
            files = sorted(REPORT_DIR.glob("hitch_*.txt"),
                           key=lambda p: p.stat().st_mtime)
            for p in files[:-MAX_REPORTS]:
                p.unlink(missing_ok=True)
        except Exception:
            pass


class WindowWatch(QObject):
    """Names any widget that briefly becomes a TOP-LEVEL WINDOW.

    Chasing a reported "small empty window flashes whenever the agent works",
    the usual suspects were all ruled out by reading: every subprocess in the
    tree already passes CREATE_NO_WINDOW, matplotlib is pinned to Agg, and each
    widget shown on the per-tool-call path is parented into a layout first. A
    parentless QWidget that gets shown IS a window on Windows, complete with the
    open/close animation, so this filter catches the moment it happens and logs
    WHO created it — which beats guessing at a flicker nobody can screenshot.

    Cost is one integer compare per event; top-level shows are rare.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._seen = set()

    def eventFilter(self, obj, event):
        try:
            from PyQt6.QtCore import QEvent
            from PyQt6.QtWidgets import QWidget
            if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
                cls = type(obj).__name__
                name = obj.objectName() or "-"
                size = obj.size()
                key = (cls, name)
                # Frames of OUR code that led here — the creator is in there.
                frames = [f"{_rel(f.filename)}:{f.lineno} {f.name}()"
                          for f in traceback.extract_stack()[:-1]
                          if str(Path(f.filename).resolve()).lower().startswith(
                              str(Path(APP_ROOT).resolve()).lower())]
                first = key not in self._seen
                self._seen.add(key)
                log.warning(
                    f"[WindowWatch] TOP-LEVEL SHOWN: {cls}(objectName={name!r}) "
                    f"{size.width()}x{size.height()}"
                    + (" [first time]" if first else "")
                    + (_NL + "    " + (_NL + "    ").join(frames[-6:]) if frames
                       else " (no app frame — created from Qt/C++)"))
        except Exception:
            pass
        return False


def install_window_watch(app) -> bool:
    """Attach WindowWatch to the QApplication. Returns True when armed."""
    try:
        watch = WindowWatch(app)
        app.installEventFilter(watch)
        app._systema_window_watch = watch     # keep a strong ref
        log.info("[WindowWatch] armed — every top-level show will be logged")
        return True
    except Exception as e:
        log.warning(f"[WindowWatch] not armed: {e}")
        return False


class span:
    """``with span("label"):`` — logs the elapsed time when it crosses SLOW_MS.
    Safe on any thread (it only logs); nested spans are fine.

    Also REGISTERS itself while open, so HitchMonitor can name the operation
    in flight when the GUI thread is blocked inside a native call that leaves
    no Python frame to blame."""

    __slots__ = ("label", "_t0", "_tid")

    def __init__(self, label: str):
        self.label = label

    def __enter__(self):
        self._t0 = time.perf_counter()
        self._tid = threading.get_ident()
        _OPEN_SPANS.setdefault(self._tid, []).append(self.label)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            stack = _OPEN_SPANS.get(self._tid)
            if stack:
                stack.pop()
        except (IndexError, AttributeError):
            pass
        ms = (time.perf_counter() - self._t0) * 1000.0
        if ms >= SLOW_MS * 4:
            log.warning(f"[span] {self.label}: {ms:.0f} ms")
        elif ms >= SLOW_MS:
            log.info(f"[span] {self.label}: {ms:.0f} ms")
        return False


def spanned(label: str):
    """Decorator form of ``span`` — wraps a whole method in one line.

    Exists because the operations that leave NO Python frame to blame (a
    setStyleSheet sweep over a large tree, a font/metrics reflow, a window
    build) are exactly the ones whose entire body needs covering. Doing that
    with a bare ``with`` block means reindenting the method; doing it with
    ``__enter__`` by hand leaks the span on an exception. This does neither.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            with span(label):
                return fn(*a, **kw)
        return wrapper
    return deco
