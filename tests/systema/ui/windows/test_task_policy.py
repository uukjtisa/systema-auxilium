"""
Tests for the per-task security policy grid + presets
(systema/ui/windows/manage_tasks_window.py).

Background tasks can't answer an approval prompt, so each code_guard category is
allow/deny only. The editor exposes built-in presets + a per-category grid;
these bind the real methods onto a lightweight stub (the full window needs a
controller).
"""
import types

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from systema.ui.windows import manage_tasks_window as M  # noqa: E402


def _grid_stub(qapp):
    class Stub:  # plain class -> weak-referenceable (needed for signal connects)
        pass
    stub = Stub()
    for m in ("_build_task_policy_grid", "_apply_task_policy_preset",
              "_apply_task_policy", "_read_task_policy"):
        setattr(stub, m, types.MethodType(getattr(M.ManageTasksWindow, m), stub))
    # Keep the box alive, else Qt garbage-collects it and its child combos.
    stub._policy_box = stub._build_task_policy_grid()
    return stub


def test_presets_cover_all_categories_allow_deny(qapp):
    cats = set(M._all_policy_categories())
    for name, pol in M._task_policy_presets():
        assert set(pol) == cats, f"{name} misses categories"
        assert all(v in ("allow", "deny") for v in pol.values())


def test_grid_has_one_combo_per_category(qapp):
    stub = _grid_stub(qapp)
    assert set(stub._f_policy_combos) == set(M._all_policy_categories())
    assert stub._f_policy_preset.count() == len(M._task_policy_presets())


def test_locked_down_and_trusted_presets(qapp):
    stub = _grid_stub(qapp)
    names = [n for n, _ in M._task_policy_presets()]
    stub._f_policy_preset.setCurrentIndex(names.index(
        next(n for n in names if n.startswith("Locked down"))))
    stub._apply_task_policy_preset()
    assert all(v == "deny" for v in stub._read_task_policy().values())

    stub._f_policy_preset.setCurrentIndex(names.index(
        next(n for n in names if n.startswith("Trusted"))))
    stub._apply_task_policy_preset()
    assert all(v == "allow" for v in stub._read_task_policy().values())


def test_fetch_preset_allows_only_network(qapp):
    fetch = dict(M._task_policy_presets())[
        next(n for n, _ in M._task_policy_presets() if n.startswith("Fetch"))]
    assert fetch[M._guard.CAT_NETWORK] == "allow"
    assert all(v == "deny" for c, v in fetch.items() if c != M._guard.CAT_NETWORK)


def test_policy_round_trip(qapp):
    stub = _grid_stub(qapp)
    stub._apply_task_policy(M._default_task_policy())
    assert stub._read_task_policy() == M._default_task_policy()
