"""The startup integrity check.

This runs before AssistantController exists, so its failure modes matter more
than most: a false NEGATIVE launches a broken app into a stack trace the user
cannot act on, and a false POSITIVE (or a raise) blocks a working app from
starting at all. Both directions are pinned here.
"""
import pytest

from systema.startup import integrity as ig


def _make_healthy(root):
    for rel in ig.CRITICAL_FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
    return root


@pytest.fixture
def copy(tmp_path):
    return _make_healthy(tmp_path)


# -- the healthy case --------------------------------------------------------

def test_a_complete_copy_is_healthy(copy):
    rep = ig.run_full_check(copy)
    assert rep.healthy
    assert not rep.skipped


def test_the_critical_files_list_still_matches_reality():
    """The only check that CRITICAL_FILES has not gone stale -- a renamed or
    moved module would make this fail.

    Deliberately checks presence and compilability directly instead of calling
    run_full_check() on APP_ROOT: that also reads the apply marker, which is
    shared mutable state another test could legitimately have set.
    """
    assert not ig.check_missing(), "CRITICAL_FILES names a file that moved"
    assert not ig.check_syntax(), "a critical file does not compile"


# -- detection ---------------------------------------------------------------

def test_a_missing_critical_file_is_caught(copy):
    (copy / "systema/execution/tool_manager.py").unlink()
    rep = ig.run_full_check(copy)
    assert not rep.healthy
    assert any(p.kind == "missing" for p in rep.problems)


def test_a_syntax_error_is_caught(copy):
    (copy / "systema/engine/ai_engine.py").write_text("def broken(:\n",
                                                      encoding="utf-8")
    rep = ig.run_full_check(copy)
    assert any(p.kind == "syntax" for p in rep.problems)


def test_the_syntax_check_actually_raises_rather_than_swallowing(tmp_path):
    """py_compile's quiet=2 means "no output AND no exception" -- combined with
    doraise it silently passes every broken file, and the first version of this
    check used it. Asserted BEHAVIOURALLY: reading the source for "quiet=2" also
    matches the comment explaining the trap."""
    bad = tmp_path / "systema" / "engine" / "ai_engine.py"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("def broken(:\n", encoding="utf-8")
    assert any(p.kind == "syntax" for p in ig.check_syntax(tmp_path)), (
        "a file that cannot compile must be reported, not silently passed")


def test_a_syntax_error_names_the_file_and_the_error(copy):
    (copy / "systema/engine/ai_engine.py").write_text("def broken(:\n",
                                                      encoding="utf-8")
    problem = next(p for p in ig.run_full_check(copy).problems
                   if p.kind == "syntax")
    assert "ai_engine.py" in problem.path
    assert "SyntaxError" in problem.detail


def test_several_problems_are_all_reported(copy):
    (copy / "systema/engine/ai_engine.py").write_text("def broken(:\n",
                                                      encoding="utf-8")
    (copy / "systema/execution/tool_manager.py").unlink()
    kinds = {p.kind for p in ig.run_full_check(copy).problems}
    assert kinds == {"syntax", "missing"}


# -- the interrupted apply, which no syntax check can find -------------------

def test_an_interrupted_apply_is_caught_even_when_every_file_is_valid(copy):
    assert ig.run_full_check(copy).healthy
    ig.mark_apply_started("v1 -> v2", root=copy)
    rep = ig.run_full_check(copy)
    assert not rep.healthy
    assert any(p.kind == "interrupted" for p in rep.problems)


def test_the_marker_carries_what_was_being_applied(copy):
    ig.mark_apply_started("main -> 2026-08-19+abc1234", root=copy)
    problem = ig.run_full_check(copy).problems[0]
    assert "2026-08-19+abc1234" in problem.describe()


def test_finishing_an_apply_clears_the_marker(copy):
    ig.mark_apply_started("v1 -> v2", root=copy)
    assert not ig.run_full_check(copy).healthy
    ig.mark_apply_finished(root=copy)
    assert ig.run_full_check(copy).healthy


def test_clearing_a_marker_that_is_not_there_is_fine(copy):
    assert ig.mark_apply_finished(root=copy) is True


# -- it must never block a launch -------------------------------------------

def test_a_dev_copy_is_skipped_entirely(copy):
    (copy / ig.DEV_MARKER).write_text("", encoding="utf-8")
    (copy / "systema/engine/ai_engine.py").write_text("def broken(:\n",
                                                      encoding="utf-8")
    rep = ig.run_full_check(copy)
    assert rep.healthy, "a developer's mid-edit files are not a broken install"
    assert rep.skipped


def test_skip_dev_false_overrides_the_marker(copy):
    (copy / ig.DEV_MARKER).write_text("", encoding="utf-8")
    (copy / "systema/engine/ai_engine.py").write_text("def broken(:\n",
                                                      encoding="utf-8")
    assert not ig.run_full_check(copy, skip_dev=False).healthy


def test_a_check_that_explodes_reports_skipped_instead_of_raising(monkeypatch):
    """A health check that can crash the app is worse than no health check."""
    monkeypatch.setattr(ig, "check_missing",
                        lambda root=None: (_ for _ in ()).throw(OSError("boom")))
    rep = ig.run_full_check()
    assert rep.skipped and rep.healthy


def test_it_never_writes_pyc_into_the_copy_under_test(copy):
    ig.run_full_check(copy)
    assert not list(copy.rglob("*.pyc")), (
        "a health check must not modify the thing it is checking")


def test_a_totally_empty_directory_is_reported_not_crashed(tmp_path):
    rep = ig.run_full_check(tmp_path)
    assert not rep.healthy
    assert len(rep.problems) == len(ig.CRITICAL_FILES)


# -- summary -----------------------------------------------------------------

def test_summary_names_the_problems(copy):
    (copy / "main.py").unlink()
    assert "main.py" in ig.run_full_check(copy).summary()


def test_summary_says_so_when_skipped(copy):
    (copy / ig.DEV_MARKER).write_text("", encoding="utf-8")
    assert "skipped" in ig.run_full_check(copy).summary()


# -- the failure that actually happened, on Kali, 2026-08-19 -----------------
# An update wrote 3-way merge conflict markers into floating_window.py. The app
# died at IMPORT with a bare "SyntaxError: invalid syntax" and no recovery
# offer, because the gate was inside main() while the import that broke sits at
# module level -- main() was never reached.

def _wreck_with_conflict(root):
    marker_a, marker_b, marker_c = "<" * 7, "=" * 7, ">" * 7
    (root / "systema/ui/windows/floating_window.py").write_text(
        "class FloatingWindow:\n    pass\n"
        f"{marker_a} systema/ui/windows/floating_window.py (local)\n"
        f"    old = 1\n{marker_b}\n    new = 2\n{marker_c} remote\n",
        encoding="utf-8")


def test_conflict_markers_are_detected(copy):
    _wreck_with_conflict(copy)
    rep = ig.run_full_check(copy)
    assert not rep.healthy
    assert any(p.kind == "conflict" for p in rep.problems)


def test_a_conflict_is_named_as_one_not_as_a_syntax_error(copy):
    """"unresolved conflict markers from an update" points at a recovery
    option. "invalid syntax" does not."""
    _wreck_with_conflict(copy)
    problem = next(p for p in ig.run_full_check(copy).problems
                   if p.kind == "conflict")
    assert "conflict" in problem.detail
    assert "update" in problem.detail


def test_a_conflicted_file_is_reported_once(copy):
    """Markers are also a SyntaxError; reporting both for one file is noise."""
    _wreck_with_conflict(copy)
    paths = [p.path for p in ig.run_full_check(copy).problems]
    assert paths.count("systema/ui/windows/floating_window.py") == 1


def test_all_four_marker_kinds_are_recognised(copy):
    for marker in ("<" * 7, "=" * 7, ">" * 7, "|" * 7):
        (copy / "main.py").write_text(f"x = 1\n{marker} something\n",
                                      encoding="utf-8")
        assert any(p.kind == "conflict" for p in ig.check_conflict_markers(copy)), \
            f"marker {marker!r} not recognised"


def test_the_gate_runs_before_the_controller_import():
    """THE bug: the check lived inside main(), but the import that breaks is at
    module level, so the process died before main() was ever called."""
    from systema import APP_ROOT
    src = (APP_ROOT / "main.py").read_text(encoding="utf-8", errors="replace")
    gate = src.index("_startup_integrity_gate()")
    imp = src.index("from systema.app.controller import")
    assert gate < imp, (
        "the integrity gate must run BEFORE the controller import, or a "
        "half-applied update kills the process before it can fire")
