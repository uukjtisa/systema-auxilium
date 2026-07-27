"""
tests/systema/app/test_exit_pipeline.py

Restart and Shutdown: ONE gate, asked once, and a cancel that actually cancels.

THE BUG THIS EXISTS FOR
restart_app() spawned the detached relauncher and armed a 3-second os._exit
BEFORE calling ui.shutdown_app() — which is where the "a response is still
generating, continue?" prompt lived. Declining it returned early exactly as
written, but by then a relauncher was already waiting on this pid and the kill
timer was already ticking. The app restarted anyway: the cancel was honoured
and ignored at the same time.

The confirm had also drifted across three files (controller, floating window,
and the slash command), each with its own idea of whether it had already been
asked, coordinated by an `_exit_confirmed` flag that was never reset on a
failure path.

RULE NOW: nothing irreversible happens until the exit has been agreed.
"""
import pytest

import systema.app.controller as controller_mod
from systema.app.controller import AssistantController


@pytest.fixture
def pipeline(monkeypatch):
    """(make_controller, actions) — actions records what actually happened."""
    actions = []
    monkeypatch.setattr(controller_mod, "_spawn_relauncher",
                        lambda pid, root: (actions.append("SPAWN"), True)[1])

    class _UI:
        def __init__(self, answer):
            self.answer = answer
            self.asked = []

        def _confirm_exit_if_busy(self, action):
            self.asked.append(action)
            return self.answer

        def perform_teardown(self):
            actions.append("TEARDOWN")

    class _Ctrl:
        request_exit = AssistantController.request_exit
        _confirm_exit = AssistantController._confirm_exit
        restart_app = AssistantController.restart_app
        shutdown_app = AssistantController.shutdown_app

        def _arm_force_exit(self, delay=3.0):
            # NOT the real one. The real one arms a 3-second os._exit(0), which
            # in a test run kills PYTEST — see the module docstring.
            actions.append("ARM")

    def make(answer=True):
        c = _Ctrl()
        c.ui = _UI(answer)
        return c

    return make, actions


# ── the cancel ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["restart", "shutdown"])
def test_cancelling_does_absolutely_nothing(pipeline, mode):
    """No relauncher, no teardown, no kill timer. The whole point."""
    make, actions = pipeline
    ctrl = make(answer=False)

    assert ctrl.request_exit(mode) is False
    assert actions == [], f"{mode} did something after being cancelled"
    assert ctrl.ui.asked == [mode.capitalize()]


def test_the_app_stays_usable_after_a_cancel(pipeline):
    """A cancelled exit must leave no latch behind — the next attempt has to
    prompt again. The old `_exit_confirmed` flag was never reset, so a
    cancelled Restart could let a later Shutdown skip its prompt entirely."""
    make, actions = pipeline
    ctrl = make(answer=False)

    ctrl.request_exit("restart")
    ctrl.request_exit("shutdown")

    assert ctrl.ui.asked == ["Restart", "Shutdown"], "both must be asked"
    assert actions == []


# ── the commit ───────────────────────────────────────────────────────────────

def test_restart_spawns_then_tears_down_in_that_order(pipeline):
    make, actions = pipeline
    ctrl = make(answer=True)

    assert ctrl.request_exit("restart") is True
    assert actions == ["SPAWN", "ARM", "TEARDOWN"]


def test_shutdown_never_spawns_or_arms_anything(pipeline):
    """Nothing waits on this pid, so there is nothing to force-exit for."""
    make, actions = pipeline
    ctrl = make(answer=True)

    assert ctrl.request_exit("shutdown") is True
    assert actions == ["TEARDOWN"]


def test_a_relauncher_that_will_not_spawn_keeps_the_app_up(pipeline, monkeypatch):
    """Better to stay running than to quit into nothing."""
    make, actions = pipeline
    monkeypatch.setattr(controller_mod, "_spawn_relauncher",
                        lambda pid, root: False)
    ctrl = make(answer=True)

    assert ctrl.request_exit("restart") is False
    assert "TEARDOWN" not in actions


# ── one prompt, one gate ─────────────────────────────────────────────────────

def test_the_prompt_is_asked_exactly_once(pipeline):
    make, actions = pipeline
    ctrl = make(answer=True)

    ctrl.request_exit("restart")

    assert len(ctrl.ui.asked) == 1, "the busy prompt must not be asked twice"


def test_re_entrant_requests_are_ignored_once_committed(pipeline):
    """Tray, menu and command can all fire during teardown."""
    make, actions = pipeline
    ctrl = make(answer=True)

    ctrl.request_exit("restart")
    committed = list(actions)

    ctrl.request_exit("restart")
    ctrl.request_exit("shutdown")

    assert actions == committed, "a second request re-ran the teardown"
    assert len(ctrl.ui.asked) == 1


def test_confirm_false_skips_the_prompt_but_not_the_teardown(pipeline):
    """For callers that already asked (the updater's apply flow)."""
    make, actions = pipeline
    ctrl = make(answer=False)          # would refuse IF asked

    assert ctrl.request_exit("shutdown", confirm=False) is True
    assert ctrl.ui.asked == []
    assert actions == ["TEARDOWN"]


# ── the aliases every caller uses ────────────────────────────────────────────

def test_restart_app_and_shutdown_app_route_through_the_one_gate(pipeline):
    make, actions = pipeline

    ctrl = make(answer=True)
    assert ctrl.restart_app() is True
    assert actions == ["SPAWN", "ARM", "TEARDOWN"]

    actions.clear()
    ctrl = make(answer=True)
    assert ctrl.shutdown_app() is True
    assert actions == ["TEARDOWN"]


def test_headless_controllers_do_not_block_on_a_missing_prompt(pipeline):
    """No UI (tests, tray-less runs) means nothing to ask — proceed."""
    make, actions = pipeline
    ctrl = make(answer=True)
    ctrl.ui = None

    assert ctrl.request_exit("shutdown") is True


# ── the split that makes it safe ─────────────────────────────────────────────

def test_the_window_keeps_policy_out_of_the_teardown():
    """perform_teardown is MECHANISM: it must not prompt, and the menu entries
    must be thin routes into the controller's gate."""
    import inspect

    from systema.ui.windows.floating_window import FloatingWindow

    teardown = inspect.getsource(FloatingWindow.perform_teardown)
    assert "_confirm_exit_if_busy" not in teardown, \
        "teardown must not decide whether to exit"

    for name in ("restart_app", "shutdown_app"):
        src = inspect.getsource(getattr(FloatingWindow, name))
        assert "request_exit" in src, f"{name} must route through the gate"

    # The flag that coordinated the old scattered confirms is gone for good.
    whole = inspect.getsource(FloatingWindow)
    assert "_exit_confirmed" not in whole


def test_the_force_exit_fallback_is_not_armed_inline():
    """THE TEST SUITE'S OWN SURVIVAL DEPENDS ON THIS.

    request_exit used to build the 3-second `os._exit(0)` timer inline. Every
    restart test therefore armed a REAL process kill, and three seconds later
    it fired — inside pytest. The suite died partway through, mid-file, with no
    summary and no report written. Because os._exit(0) reports success, the run
    looked green: exit code 0, hundreds of tests silently never run.

    Keeping the arming behind an overridable method is what lets the pipeline
    be tested at all. If it moves back inline, this fails loudly instead of the
    suite failing quietly.
    """
    import inspect

    from systema.app.controller import AssistantController

    src = inspect.getsource(AssistantController.request_exit)
    assert "os._exit" not in src, \
        "request_exit must not hard-exit inline — call _arm_force_exit()"
    assert "threading.Timer" not in src, \
        "request_exit must not build the kill timer inline"
    assert "_arm_force_exit" in src

    # ...and the real thing must still be a DAEMON timer, or it would keep a
    # healthy process alive for three seconds on every restart.
    arm = inspect.getsource(AssistantController._arm_force_exit)
    assert "daemon = True" in arm and "os._exit(0)" in arm
