"""
core/logger.py
"""

import logging as _logging


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


def _make_logger(name: str) -> _logging.Logger:
    """Create a vivid, colored terminal logger."""
    logger = _logging.getLogger(name)
    if logger.handlers:
        return logger

    _RESET        = "\033[0m"
    _BOLD         = "\033[1m"
    _LEVEL_COLORS = {
        "DEBUG":    "\033[36m",   # Cyan
        "INFO":     "\033[32m",   # Green
        "WARNING":  "\033[33m",   # Yellow
        "ERROR":    "\033[31m",   # Red
        "CRITICAL": "\033[35m",   # Magenta
    }
    _NAME_COLOR = "\033[34m"      # Blue
    _TIME_COLOR = "\033[90m"      # Dark grey
    _SEP_COLOR  = "\033[90m"      # Dark grey

    class _ColorFmt(_logging.Formatter):
        def format(self, record: _logging.LogRecord) -> str:
            lvl_color = _LEVEL_COLORS.get(record.levelname, _RESET)
            lvl  = f"{_BOLD}{lvl_color}{record.levelname:<8}{_RESET}"
            ts   = f"{_TIME_COLOR}{self.formatTime(record, '%H:%M:%S')}{_RESET}"
            mod  = f"{_NAME_COLOR}{record.name}{_RESET}"
            sep  = f"{_SEP_COLOR}│{_RESET}"
            msg  = f"{lvl_color}{record.getMessage()}{_RESET}"
            return f"{ts} {sep} {lvl} {sep} {mod} {sep} {msg}"

    h = _TeeBypassHandler()
    h.setFormatter(_ColorFmt())
    logger.addHandler(h)
    logger.setLevel(_logging.DEBUG)
    logger.propagate = False
    return logger

class _NoOpLogger:
    def debug(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass