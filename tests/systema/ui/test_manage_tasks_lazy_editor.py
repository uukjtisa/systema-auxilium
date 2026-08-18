"""
tests/systema/ui/test_manage_tasks_lazy_editor.py

The task editor is built on first use, not on window open.

`_build_editor_page` is 809 lines and 463 widgets — measured at 100 ms of a
176 ms window construction offscreen, and 1151 ms in a real hitch report — while
the list and viewer pages are 16 widgets and under 7 ms each. It was built
eagerly in `__init__` AND rebuilt by `apply_theme`, so opening the task list or
saving a new theme charged the full cost to users who never open the editor.

Slot 1 keeps a placeholder rather than the stack getting shorter: page indices
are addressed positionally and `apply_theme` saves/restores `currentIndex()`.
"""
import types

import pytest

pytest.importorskip("PyQt6")

from systema.ui.windows.manage_tasks_window import ManageTasksWindow  # noqa: E402


@pytest.fixture
def win(qapp):
    controller = types.SimpleNamespace(
        task_manager=None, settings={}, theme="obsidian_blue",
        get_user_name=lambda: "Thirdy", get_assistant_name=lambda: "Kimi")
    window = ManageTasksWindow(controller)
    yield window
    window.deleteLater()


def test_the_editor_is_not_built_on_window_open(win):
    assert win._editor_page is None


def test_the_stack_still_has_three_pages(win):
    """Indices are positional — the viewer must stay at 2 whether or not the
    editor exists."""
    assert win._stack.count() == 3
    assert win._stack.currentIndex() == 0


def test_new_task_builds_it_and_lands_on_the_editor(win):
    win._new_task()
    assert win._editor_page is not None
    assert win._stack.currentIndex() == 1
    assert win._stack.widget(1) is win._editor_page


def test_edit_task_builds_it_too(win):
    """The other door. Guarding only one leaves an AttributeError on the ~55
    form widgets the moment someone edits an existing task."""
    win._edit_task({"id": "t1", "name": "Nightly", "instruction": "do a thing"})
    assert win._editor_page is not None
    assert win._stack.currentIndex() == 1


def test_building_twice_is_a_no_op(win):
    win._new_task()
    page = win._editor_page
    win._new_task()
    assert win._editor_page is page, "the editor must not be rebuilt per visit"
    assert win._stack.count() == 3


def test_a_theme_change_does_not_build_an_unopened_editor(win):
    """This was the second half of the cost: apply_theme rebuilt all three
    pages, so every theme save paid for the editor even if it was never seen."""
    win.apply_theme()
    assert win._editor_page is None
    assert win._stack.count() == 3


def test_a_theme_change_rebuilds_an_editor_that_was_open(win):
    """It must still be re-tinted — the whole point of apply_theme."""
    win._new_task()
    old = win._editor_page
    win.apply_theme()
    assert win._editor_page is not None
    assert win._editor_page is not old, "a stale page would keep the old palette"
    assert win._stack.currentIndex() == 1
    assert win._stack.count() == 3


def test_the_placeholder_is_replaced_not_appended(win):
    """insertWidget + removeWidget, in that order. Getting it wrong leaves a
    fourth page and shifts the viewer off index 2."""
    stand_in = win._stack.widget(1)
    win._new_task()
    assert win._stack.count() == 3
    assert win._stack.widget(1) is not stand_in
