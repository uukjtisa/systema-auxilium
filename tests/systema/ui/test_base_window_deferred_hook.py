"""
tests/systema/ui/test_base_window_deferred_hook.py

`BaseWindow.showEvent` defers work with `QTimer.singleShot(0, self._install_
house_scrolling)`. That resolves the attribute on the INSTANCE, so any subclass
defining the same name wins — and if its signature differs, the zero-argument
call raises TypeError on the GUI thread. An unhandled main-thread exception
takes the whole app down.

That is not hypothetical: the hook was originally called
`_install_smooth_scrolling`, which `ChatWindow` already used for something else
entirely (one scroll area plus the sticky-bottom callback). The app crashed
five times on 2026-08-18 — mid-reply, while the streamed response was being
re-rendered — before the collision was spotted.

So: any zero-arg method BaseWindow defers to must stay zero-arg in every
subclass, and must not collide with an existing name.
"""
import importlib
import inspect
import pkgutil

import pytest

import systema.ui as ui_pkg
from systema.ui.base_window import BaseWindow

DEFERRED_HOOKS = ("_install_house_scrolling",)


# startup_notif is a standalone tkinter SCRIPT, not a module — importing it
# runs a mainloop and then sys.exit(0). SystemExit is a BaseException, so a
# plain `except Exception` around the import does not stop it taking pytest
# with it.
_NOT_IMPORTABLE = ("systema.ui.startup_notif",)


def _load_every_ui_module():
    """Import the UI tree so every BaseWindow subclass is registered."""
    for mod in pkgutil.walk_packages(ui_pkg.__path__, ui_pkg.__name__ + "."):
        if mod.name in _NOT_IMPORTABLE:
            continue
        try:
            importlib.import_module(mod.name)
        except BaseException:
            continue        # optional deps / platform-specific modules


def _subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


@pytest.mark.parametrize("hook", DEFERRED_HOOKS)
def test_base_window_defines_the_hook(hook):
    assert callable(getattr(BaseWindow, hook, None)), (
        f"showEvent defers to {hook!r} but BaseWindow does not define it")


@pytest.mark.parametrize("hook", DEFERRED_HOOKS)
def test_no_subclass_shadows_a_deferred_hook_with_a_different_signature(hook):
    _load_every_ui_module()
    offenders = []
    for cls in _subclasses(BaseWindow):
        fn = cls.__dict__.get(hook)
        if fn is None:
            continue        # inherits the base — fine
        params = [p for name, p in inspect.signature(fn).parameters.items()
                  if name != "self"
                  and p.default is inspect.Parameter.empty
                  and p.kind not in (inspect.Parameter.VAR_POSITIONAL,
                                     inspect.Parameter.VAR_KEYWORD)]
        if params:
            offenders.append(f"{cls.__module__}.{cls.__name__}{inspect.signature(fn)}")
    assert not offenders, (
        f"{hook!r} is called with no arguments from a deferred QTimer, but "
        f"these subclasses require arguments — the call would raise TypeError "
        f"on the GUI thread and crash the app: {offenders}")


def test_the_hook_name_does_not_collide_with_chat_windows_own_installer():
    """ChatWindow._install_smooth_scrolling(scroll_area) is a DIFFERENT thing
    and must keep its name; the base hook must keep out of its way."""
    from systema.ui.chat_window import ChatWindow
    assert "_install_smooth_scrolling" not in DEFERRED_HOOKS
    own = ChatWindow.__dict__.get("_install_smooth_scrolling")
    assert own is not None, "ChatWindow lost its per-area installer"
    assert "scroll_area" in inspect.signature(own).parameters


def test_the_hook_swallows_errors(monkeypatch):
    """A window that cannot get house scrolling must still open."""
    win = BaseWindow.__new__(BaseWindow)

    def boom(*_a, **_k):
        raise RuntimeError("widget tree exploded")

    monkeypatch.setattr(BaseWindow, "findChildren", boom, raising=False)
    win._install_house_scrolling()      # must not raise
