"""
tests/systema/execution/test_interpreter_output.py

run_python_interpreter reports a step as up to four labelled sections — STDOUT,
STDERR, RESULT, ERROR. A bare expression used to fill TWO of them with the same
thing:

    STDOUT:
    '[attach_image_to_context] Queued for ONE-TURN analysis: ...'
    RESULT:
    [attach_image_to_context] Queued for ONE-TURN analysis: ...

`CustomInterpreter.runcode` installs a displayhook to capture an expression's
value, and it also called the default hook — which echoes repr() to stdout the
way a REPL does. The value was then reported AGAIN as its own RESULT section.
Code that print()ed never fired the hook at all, so it only showed on steps
ending in a bare call, which is why it looked intermittent.

The observation is what the model reads and pays for, so a value must appear in
it exactly once.

These drive `runcode` directly. The end-to-end route runs the step on a worker
thread and captures through a stdout wrapper the app installs (main.py's
_LogSink) — under pytest's own capture that wrapper never sees the writes, so
an end-to-end stdout assertion would pass vacuously whether or not the bug is
back. The displayhook is where the duplication was and where it stays fixed.
"""
import io
import sys

import pytest

from systema.execution.python_interpreter import CustomInterpreter, PythonInterpreter


def _run(source: str):
    """Execute one statement through the real capture displayhook, with stdout
    pointed at a buffer we own. Returns (last_result, what_hit_stdout)."""
    interp = CustomInterpreter({'echo': lambda s: f"[tool] {s}"})
    sink = io.StringIO()
    real = sys.stdout
    sys.stdout = sink
    try:
        interp.runcode(compile(source, '<test>', 'single'))
    finally:
        sys.stdout = real
    return interp.last_result, sink.getvalue()


# ── the duplication ──────────────────────────────────────────────────────────

def test_a_bare_expression_is_captured_but_not_echoed():
    """The reported case: a step whose last line is a bare tool call."""
    result, out = _run('echo("queued")')

    assert result == "[tool] queued"
    assert out == "", (
        "the value was echoed to stdout as well as captured — it comes back to "
        "the model twice, once under STDOUT and once under RESULT")


def test_the_quoted_repr_form_is_gone():
    """The echo wrote repr(), so the duplicate arrived quoted — that exact
    shape is what showed up in the session file."""
    _, out = _run('echo("queued")')
    assert "'[tool] queued'" not in out


@pytest.mark.parametrize("source", [
    '40 + 2',
    '"a string"',
    '[1, 2, 3]',
    '{"k": "v"}',
])
def test_no_expression_value_ever_reaches_stdout(source):
    result, out = _run(source)
    assert result is not None
    assert out == ""


def test_printing_still_goes_to_stdout():
    """The fix must not cost real output — this is the common case."""
    result, out = _run('print("hello")')

    assert "hello" in out
    assert result is None


def test_a_printed_value_is_not_also_a_result():
    """print() returns None, so the two channels stay independent: the text is
    STDOUT's, and there is no RESULT section at all."""
    result, out = _run('print(echo("queued"))')

    assert "[tool] queued" in out
    assert result is None
    assert out.count("[tool] queued") == 1


def test_an_expression_value_survives_as_underscore():
    """The default hook also set `_`. The interpreter is persistent across
    steps, so that half of its job is kept — only the printing was dropped."""
    interp = CustomInterpreter({})
    real, sink = sys.stdout, io.StringIO()
    sys.stdout = sink
    try:
        interp.runcode(compile('40 + 2', '<test>', 'single'))
        interp.runcode(compile('_ * 2', '<test>', 'single'))
    finally:
        sys.stdout = real
    assert interp.last_result == 84


def test_the_displayhook_is_always_restored():
    """It is swapped per-execution; leaking it would silence the REPL for the
    whole process."""
    before = sys.displayhook
    _run('40 + 2')
    assert sys.displayhook is before


def test_it_is_restored_even_when_the_code_raises():
    before = sys.displayhook
    interp = CustomInterpreter({})
    real, sink = sys.stdout, io.StringIO()
    sys.stdout = sink
    try:
        interp.runcode(compile('1 / 0', '<test>', 'single'))
    finally:
        sys.stdout = real
    assert sys.displayhook is before


# ── the section rule this protects, end to end ───────────────────────────────

def test_a_none_value_produces_no_result_section():
    """run_python_interpreter only emits RESULT when the value is not None —
    so a print-only step has exactly one section."""
    pi = PythonInterpreter()
    assert pi.execute('x = 1')['result'] is None


def test_a_real_value_still_comes_back_as_the_result():
    pi = PythonInterpreter()
    pi.namespace['echo'] = lambda s: f"[tool] {s}"
    assert pi.execute('echo("queued")')['result'] == "[tool] queued"
