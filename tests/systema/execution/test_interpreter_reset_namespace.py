"""
tests/systema/execution/test_interpreter_reset_namespace.py

The agent's interpreter helpers must survive an interpreter reset — including
the resets nobody asked for.

THE BUG THIS EXISTS FOR
-----------------------
Cancelling a blocking call (`time.sleep(600)`) escalates: async-exception,
then kill the step's children, then — if the thread is still stuck in a C call
that none of that can reach — abandon the thread and reset the interpreter.

That last reset happens INSIDE PythonInterpreter.execute(), by the interpreter
on itself. ToolManager.reset_python() was the only place that re-injected the
agent bindings, so this path never re-injected anything. The interpreter came
back with `write_file` and nothing else, and every later call died with:

    NameError: name 'search_memory' is not defined

...until the whole app was restarted. Found live: a cancel of a 10-minute
sleep, immediately followed by a search_memory() that had worked minutes
earlier.

THE FIX BEING GUARDED
---------------------
The re-injection hangs off `PythonInterpreter.on_reset`, fired by reset()
itself. The guarantee now lives at the single point where the namespace is
cleared, so it cannot be bypassed by a caller — which is precisely how it was
bypassed before.
"""
import pytest

from systema.execution.python_interpreter import PythonInterpreter


@pytest.fixture
def interp():
    return PythonInterpreter()


# ── the hook itself ──────────────────────────────────────────────────────────

def test_reset_fires_the_hook(interp):
    fired = []
    interp.on_reset = lambda: fired.append(1)

    interp.reset()

    assert fired == [1], "reset() must give the owner a chance to re-bind"


def test_the_hook_runs_after_the_namespace_is_rebuilt(interp):
    """Order matters: injecting before the clear would wipe the bindings."""
    seen = {}

    def _inject():
        # The baseline is already in place...
        seen['baseline'] = interp.namespace.get('__name__')
        seen['stale_gone'] = 'leftover' not in interp.namespace
        # ...and anything bound here must survive the call.
        interp.namespace['search_memory'] = lambda q: f"found {q}"

    interp.namespace['leftover'] = object()
    interp.on_reset = _inject
    interp.reset()

    assert seen == {'baseline': '__main__', 'stale_gone': True}
    assert 'search_memory' in interp.namespace


def test_a_broken_hook_does_not_break_the_reset(interp):
    """The interpreter is already healthy by the time the hook runs — a failing
    injector must not take it down with it."""
    interp.on_reset = lambda: 1 / 0

    interp.reset()                      # must not raise

    assert interp.namespace['__name__'] == '__main__'
    assert 'write_file' in interp.namespace
    assert interp.execution_count == 0


def test_no_hook_is_fine(interp):
    interp.on_reset = None
    interp.reset()
    assert 'write_file' in interp.namespace


def test_helpers_come_back_on_every_reset(interp):
    """Not just the first one — the reported symptom recurred forever."""
    interp.on_reset = lambda: interp.namespace.update(search_memory=lambda q: q)

    for _ in range(3):
        interp.reset()
        assert 'search_memory' in interp.namespace
        del interp.namespace['search_memory']


# ── the path that actually broke ─────────────────────────────────────────────

def test_the_abandon_path_re_injects(interp):
    """execute() resets the interpreter ITSELF when it gives up on a stuck
    thread. That reset is the one nobody was covering."""
    import inspect

    src = inspect.getsource(PythonInterpreter.execute)
    assert "self.reset()" in src, \
        "test is stale — execute() no longer resets the interpreter itself"

    # ...and every such reset goes through the same door the hook is on.
    injected = []
    interp.on_reset = lambda: injected.append(len(injected))
    interp.reset()
    interp.reset()
    assert injected == [0, 1]


# ── the wiring, end to end ───────────────────────────────────────────────────

def test_tool_manager_wires_the_hook_at_construction():
    """A ToolManager that forgets to wire it is the whole bug, silently back."""
    import types

    from systema.execution.tool_manager import ToolManager

    tm = ToolManager.__new__(ToolManager)
    tm.tools = {'python': PythonInterpreter()}
    tm._namespace_injector = None
    tm._reinject_namespaces = types.MethodType(
        ToolManager._reinject_namespaces, tm)
    tm.tools['python'].on_reset = tm._reinject_namespaces

    calls = []
    tm._namespace_injector = lambda: calls.append('injected')

    tm.tools['python'].reset()          # a reset the ToolManager never saw

    assert calls == ['injected'], \
        "an interpreter-initiated reset must still re-inject"


def test_tool_manager_init_actually_installs_it():
    """The above proves the mechanism; this proves __init__ uses it."""
    import inspect

    from systema.execution.tool_manager import ToolManager

    src = inspect.getsource(ToolManager.__init__)
    assert "on_reset" in src and "_reinject_namespaces" in src, \
        "ToolManager.__init__ must install the interpreter's reset hook"


def test_reset_python_no_longer_injects_twice():
    """reset_python() used to call the injector itself. Now the hook does it —
    doing both would run the injection twice per manual reset."""
    import inspect

    from systema.execution.tool_manager import ToolManager

    src = inspect.getsource(ToolManager.reset_python)
    assert "_namespace_injector()" not in src, \
        "reset_python must leave re-injection to the on_reset hook"


def test_the_task_engine_installs_an_injector_too():
    """Background tasks used to recover only at their NEXT ping."""
    import inspect

    from systema.agents.task_manager import TaskAIEngine

    src = inspect.getsource(TaskAIEngine._init_engine)
    assert "_namespace_injector" in src, \
        "a task's interpreter must re-bind its helpers on reset, like the main one"
