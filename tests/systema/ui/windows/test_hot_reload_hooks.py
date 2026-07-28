"""
tests/systema/ui/windows/test_hot_reload_hooks.py

The Debug window's hot-reload post-hooks swap the LIVE window instance after a
module reloads. All three referenced the module path (`systema.ui.chat_window`)
where they meant the FloatingWindow attribute (`ui.chat_window`) — left behind
by the core/ -> systema/ rename. Since the hooks import with `as` aliases, the
name `systema` was never bound in that scope, so each hook raised NameError and
hot-reloading a window silently never swapped it.

These call the hooks unbound against stubs: no Qt widget is constructed, and
isVisible() is False throughout so the reopen timer never arms.
"""
import pytest

from systema.ui.windows.debug_window import DebugWindow


class _Win:
    """Stand-in for a live window instance."""
    def __init__(self):
        self.hidden = False
        self.deleted = False

    def isVisible(self):
        return False

    def hide(self):
        self.hidden = True

    def deleteLater(self):
        self.deleted = True


class _UI:
    def __init__(self, **kw):
        self.chat_window = kw.get('chat_window')
        self.settings_window = kw.get('settings_window')
        self.debug_window = kw.get('debug_window')


class _Controller:
    def __init__(self, ui):
        self.ui = ui
        self.settings = {}


@pytest.mark.parametrize("hook,attr", [
    ("_post_chat_window", "chat_window"),
    ("_post_settings_window", "settings_window"),
    ("_post_debug_window", "debug_window"),
])
def test_hook_survives_no_live_window(hook, attr):
    """The regression: this raised NameError before reaching any logic."""
    ctrl = _Controller(_UI(**{attr: None}))

    getattr(DebugWindow, hook)(object(), ctrl)

    assert getattr(ctrl.ui, attr) is None


@pytest.mark.parametrize("hook,attr", [
    ("_post_chat_window", "chat_window"),
    ("_post_settings_window", "settings_window"),
])
def test_hook_tears_down_and_clears_the_live_window(hook, attr):
    win = _Win()
    ctrl = _Controller(_UI(**{attr: win}))

    getattr(DebugWindow, hook)(object(), ctrl)

    assert win.hidden and win.deleted, "the stale instance must be torn down"
    assert getattr(ctrl.ui, attr) is None, "and the slot cleared for the new class"
