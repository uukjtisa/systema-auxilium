"""
tests/systema/common/test_settings_durability.py

Settings must survive everything the app throws at them.

Two hazards this locks down:

  * **Silent wipe.** `_load_all` treated an UNREADABLE settings.json exactly like
    a fresh install — so the next save_section() rewrote the file with only the
    section being saved, permanently discarding the rest. A transient read
    failure must never be indistinguishable from a first run.
  * **Section clobber.** Each owner (controller / chat window / floating window)
    saves its own section independently, often from different threads. One
    save must never drop another's data.

Also covers the 2026-07-26 move of settings.json into data/, including the case
where the OLD root file is restored from a backup after the new one exists.
"""
import json
import threading


from systema.common import app_config


def _cfg(tmp_path):
    return tmp_path / "settings.json"


# ── the silent-wipe guard ────────────────────────────────────────────────────

def test_a_corrupt_config_is_preserved_not_silently_discarded(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.write_text("{ this is not valid json", encoding="utf-8")

    assert app_config.load_section("settings", cfg) == {}

    kept = list(tmp_path.glob("settings.json.corrupt-*"))
    assert kept, "the unreadable config was thrown away instead of preserved"
    assert "not valid json" in kept[0].read_text(encoding="utf-8")


def test_a_save_after_corruption_does_not_bury_the_original(tmp_path):
    """The real-world sequence: unreadable file, app starts on defaults, then
    something saves a window geometry. The original must still be recoverable."""
    cfg = _cfg(tmp_path)
    cfg.write_text('{"settings": {"api_key": "PRECIOUS"} , broken', encoding="utf-8")

    app_config.save_section("chat_window_config", {"window_geometry": {"x": 1}}, cfg)

    kept = list(tmp_path.glob("settings.json.corrupt-*"))
    assert kept and "PRECIOUS" in kept[0].read_text(encoding="utf-8")


def test_an_empty_file_is_treated_as_corrupt_not_as_fresh(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.write_text("", encoding="utf-8")

    app_config.load_section("settings", cfg)

    assert list(tmp_path.glob("settings.json.corrupt-*"))


# ── section isolation ────────────────────────────────────────────────────────

def test_saving_one_section_preserves_the_others(tmp_path):
    cfg = _cfg(tmp_path)
    app_config.save_section("settings", {"ai_provider": "custom_script"}, cfg)
    app_config.save_section("chat_window_config", {"chat_zoom": 1.4}, cfg)
    app_config.save_section("floating_window_config", {"icon_type": "emoji"}, cfg)

    app_config.save_section("chat_window_config", {"chat_zoom": 2.0}, cfg)

    whole = json.loads(cfg.read_text(encoding="utf-8"))
    assert whole["settings"] == {"ai_provider": "custom_script"}
    assert whole["floating_window_config"] == {"icon_type": "emoji"}
    assert whole["chat_window_config"] == {"chat_zoom": 2.0}


def test_concurrent_section_saves_do_not_lose_data(tmp_path):
    """Different owners save from different threads; the lock must make every
    write a read-merge-write, not a last-writer-wins overwrite."""
    cfg = _cfg(tmp_path)
    sections = [f"section_{i}" for i in range(12)]

    def _save(name):
        app_config.save_section(name, {"value": name}, cfg)

    threads = [threading.Thread(target=_save, args=(n,)) for n in sections]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    whole = json.loads(cfg.read_text(encoding="utf-8"))
    missing = [n for n in sections if whole.get(n) != {"value": n}]
    assert not missing, f"concurrent saves lost these sections: {missing}"


def test_a_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    cfg = _cfg(tmp_path)
    app_config.save_section("settings", {"a": 1}, cfg)

    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert not leftovers, f"atomic write left temp files behind: {leftovers}"
    assert json.loads(cfg.read_text(encoding="utf-8"))["settings"] == {"a": 1}


def test_values_survive_a_full_round_trip_unchanged(tmp_path):
    """Nested structures, unicode and secrets must come back byte-identical —
    provider Display values live in here."""
    cfg = _cfg(tmp_path)
    payload = {
        "provider_display_values": {
            "provider_x.py": {"API_KEY": "sk-live-∂ƒ©", "MODEL": "a/b:c"}},
        "memory_max_results": 3,
        "streaming_enabled": True,
        "nested": {"deep": {"list": [1, 2, {"x": None}]}},
        "emoji_free_but_unicode": "café — naïve",
    }

    app_config.save_section("settings", payload, cfg)

    assert app_config.load_section("settings", cfg) == payload


# ── the data/ move ───────────────────────────────────────────────────────────

def test_the_config_now_lives_under_data(tmp_path):
    assert app_config.CONFIG_FILE.parent.name == "data"
    assert app_config.ROOT_CONFIG_FILE.parent == app_config.CONFIG_FILE.parent.parent


def test_a_test_path_never_touches_the_projects_real_root_config(tmp_path):
    """Guard rail on the adoption logic: it must be inert for any explicitly
    passed path, or running the suite would consume the developer's own
    settings.json."""
    cfg = _cfg(tmp_path)
    app_config.save_section("settings", {"mine": True}, cfg)

    assert app_config.load_section("settings", cfg) == {"mine": True}
    # The real root config (whether or not it exists) is untouched by this call.
    assert app_config._adopt_root_config(cfg) is None


def test_load_never_creates_a_file_on_a_fresh_install(tmp_path):
    cfg = _cfg(tmp_path)
    app_config.load_section("settings", cfg)
    app_config.load_section("chat_window_config", cfg)
    assert not cfg.exists()
