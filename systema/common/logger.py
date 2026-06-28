"""
systema/common/logger.py
Advanced colored terminal logger.

Every module builds its logger via _make_logger(name).  Each logger name
(Controller, AIEngine, ToolManager, …) is assigned its own stable color, and
every line shows the originating source location — relative file path, line
number and function — so you can jump straight to where a log came from.

Output layout (ANSI-colored on the terminal; the session _Tee strips the codes
for the plain-text log file):

  HH:MM:SS.mmm │ LEVEL    │ Controller        │ systema/app/controller.py:118 init() │ message
"""

import logging as _logging
import zlib as _zlib
from pathlib import Path as _Path

# Project root = parent of the `systema` package (this file: systema/common/logger.py)
_ROOT = _Path(__file__).resolve().parents[2]

# ── ANSI helpers ─────────────────────────────────────────────────────────────
_RESET = "\033[0m"
_BOLD  = "\033[1m"
_DIM   = "\033[2m"


def _fg(code: int) -> str:
    """256-color foreground escape."""
    return f"\033[38;5;{code}m"


# Per-level colors (256-color palette for richer hues than the basic 8)
_LEVEL_COLORS = {
    "DEBUG":    _fg(245),   # grey
    "INFO":     _fg(40),    # green
    "WARNING":  _fg(214),   # amber
    "ERROR":    _fg(196),   # red
    "CRITICAL": _BOLD + _fg(201),  # bright magenta
}

# Curated palette of distinct, dark-background-friendly hues for logger names.
_NAME_PALETTE = [
    81, 114, 180, 209, 141, 222, 117, 211, 150, 173,
    110, 218, 156, 186, 147, 215, 45, 213, 119, 203,
    159, 229, 135, 49,
]

# name -> color cache (stable per process)
_name_color_cache: dict = {}


def _name_color(name: str) -> str:
    c = _name_color_cache.get(name)
    if c is None:
        idx = _zlib.crc32(name.encode("utf-8")) % len(_NAME_PALETTE)
        c = _fg(_NAME_PALETTE[idx])
        _name_color_cache[name] = c
    return c


def _rel_location(record: _logging.LogRecord) -> str:
    """`<path relative to project root>:<line>` — falls back to the bare filename
    for anything outside the project (e.g. site-packages)."""
    try:
        p = _Path(record.pathname).resolve()
        rel = p.relative_to(_ROOT).as_posix()
    except Exception:
        rel = record.filename
    return f"{rel}:{record.lineno}"


class _TeeBypassHandler(_logging.StreamHandler):
    """StreamHandler that tells _Tee "this write is logging infrastructure,
    not user code — never route it into the capture buffer."

    Increments the per-thread bypass depth before emit() and decrements
    it in finally, so the protection holds even if the formatter or
    the write itself raises.  Falls back to plain StreamHandler behaviour
    when the stream is not a _Tee (unit tests, no _setup_session_logger)."""
    def emit(self, record: _logging.LogRecord) -> None:
        stream = self.stream
        tee = stream if hasattr(stream, 'set_bypass') else None
        if tee is not None:
            tee.set_bypass(True)
        try:
            super().emit(record)
        finally:
            if tee is not None:
                tee.set_bypass(False)


class _ColorFmt(_logging.Formatter):
    """Rich, aligned, per-class-colored formatter with source location."""

    def __init__(self, logger_name: str):
        super().__init__()
        self._name_c = _name_color(logger_name)

    def format(self, record: _logging.LogRecord) -> str:
        lvl_c = _LEVEL_COLORS.get(record.levelname, _RESET)
        sep   = f"{_DIM}│{_RESET}"

        ts   = f"{_DIM}{self.formatTime(record, '%H:%M:%S')}.{int(record.msecs):03d}{_RESET}"
        lvl  = f"{_BOLD}{lvl_c}{record.levelname:<8}{_RESET}"
        name = f"{_BOLD}{self._name_c}{record.name:<16}{_RESET}"
        loc  = (f"{_fg(73)}{_rel_location(record)}{_RESET} "
                f"{_DIM}{record.funcName}(){_RESET}")
        # Message: tinted by level so warnings/errors pop; info/debug stay calm.
        if record.levelno >= _logging.WARNING:
            msg = f"{lvl_c}{record.getMessage()}{_RESET}"
        else:
            msg = record.getMessage()

        return f"{ts} {sep} {lvl} {sep} {name} {sep} {loc} {sep} {msg}"


def _make_logger(name: str) -> _logging.Logger:
    """Create (or return) a vivid, colored terminal logger keyed by `name`.

    The logger name gets its own stable color and every line carries the
    source file:line and function it was emitted from."""
    logger = _logging.getLogger(name)
    if logger.handlers:
        return logger

    h = _TeeBypassHandler()
    h.setFormatter(_ColorFmt(name))
    logger.addHandler(h)
    logger.setLevel(_logging.DEBUG)
    logger.propagate = False
    return logger


class _NoOpLogger:
    def debug(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass
    def critical(self, *args, **kwargs): pass
    def exception(self, *args, **kwargs): pass
