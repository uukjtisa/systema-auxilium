"""
Tests for the skills "recently used" tracking + unload-all in
systema/agents/skill_manager.py (drives the sidebar's default ordering and the
Unload all button).
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from systema.agents.skill_manager import SkillManager  # noqa: E402


def _make_skill(root, name):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: \"test {name}\"\n---\n# {name}\n",
        encoding="utf-8")


def test_load_records_last_used_and_unload_all(qapp, tmp_path):
    _make_skill(tmp_path, "alpha")
    _make_skill(tmp_path, "beta")
    sm = SkillManager(tmp_path)

    before = {s["name"]: s["last_used"] for s in sm.get_skills()}
    assert before["alpha"] == 0.0 and before["beta"] == 0.0

    ok, _ = sm.load_skill("beta")
    assert ok
    after = {s["name"]: s["last_used"] for s in sm.get_skills()}
    assert after["beta"] > 0.0          # timestamp recorded
    assert after["alpha"] == 0.0

    # Unload-all clears everything and reports the count.
    n = sm.unload_all_skills()
    assert n == 1
    assert all(not s["is_loaded"] for s in sm.get_skills())
    # A second call is a no-op (nothing loaded).
    assert sm.unload_all_skills() == 0


def test_last_used_persists_across_restart(qapp, tmp_path):
    _make_skill(tmp_path, "gamma")
    sm = SkillManager(tmp_path)
    sm.load_skill("gamma")
    ts = {s["name"]: s["last_used"] for s in sm.get_skills()}["gamma"]
    assert ts > 0.0

    # A fresh manager over the same dir restores last_used from state.
    sm2 = SkillManager(tmp_path)
    ts2 = {s["name"]: s["last_used"] for s in sm2.get_skills()}["gamma"]
    assert ts2 == pytest.approx(ts, abs=0.001)
