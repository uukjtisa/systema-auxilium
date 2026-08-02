"""
tests/systema/common/test_app_config.py

The consolidated settings.json store (common/app_config.py): section
load/save round-trips, one-time legacy migration (assistant_settings.json +
chat_config.json + floating_window_config.json -> one multi-section file,
legacy deleted only after the merged file verifies), and isolation between
sections on save.
"""
import json


from systema.common import app_config


def _cfg(tmp_path):
    return tmp_path / "settings.json"


def _write_legacy(tmp_path, filename, data):
    (tmp_path / filename).write_text(json.dumps(data), encoding="utf-8")


# ── fresh install ────────────────────────────────────────────────────────────

def test_fresh_install_returns_empty_sections(tmp_path):
    cfg = _cfg(tmp_path)
    assert app_config.load_section("settings", cfg) == {}
    assert not cfg.exists()  # loading alone never creates the file


def test_save_then_load_round_trip(tmp_path):
    cfg = _cfg(tmp_path)
    assert app_config.save_section("settings", {"ai_provider": "custom_script"}, cfg)
    assert app_config.load_section("settings", cfg) == {"ai_provider": "custom_script"}
    on_disk = json.loads(cfg.read_text(encoding="utf-8"))
    assert on_disk == {"settings": {"ai_provider": "custom_script"}}


# ── migration ────────────────────────────────────────────────────────────────

def test_migration_merges_all_three_and_deletes_legacy(tmp_path):
    cfg = _cfg(tmp_path)
    _write_legacy(tmp_path, "assistant_settings.json", {"user_name": "USER"})
    _write_legacy(tmp_path, "chat_config.json", {"chat_zoom": 1.5})
    _write_legacy(tmp_path, "floating_window_config.json", {"icon_type": "letter"})

    assert app_config.load_section("settings", cfg) == {"user_name": "USER"}
    assert app_config.load_section("chat_window_config", cfg) == {"chat_zoom": 1.5}
    assert app_config.load_section("floating_window_config", cfg) == {"icon_type": "letter"}

    assert cfg.exists()
    for legacy in app_config.LEGACY_FILES.values():
        assert not (tmp_path / legacy).exists()


def test_migration_partial_legacy_set(tmp_path):
    cfg = _cfg(tmp_path)
    _write_legacy(tmp_path, "chat_config.json", {"chat_zoom": 2.0})

    assert app_config.load_section("chat_window_config", cfg) == {"chat_zoom": 2.0}
    assert app_config.load_section("settings", cfg) == {}
    assert not (tmp_path / "chat_config.json").exists()


def test_migration_skips_corrupt_legacy_and_keeps_it_on_disk(tmp_path):
    cfg = _cfg(tmp_path)
    (tmp_path / "assistant_settings.json").write_text("{not json", encoding="utf-8")
    _write_legacy(tmp_path, "chat_config.json", {"chat_zoom": 1.25})

    assert app_config.load_section("chat_window_config", cfg) == {"chat_zoom": 1.25}
    assert app_config.load_section("settings", cfg) == {}
    # the parseable file migrated + was removed; the corrupt one stays untouched
    assert not (tmp_path / "chat_config.json").exists()
    assert (tmp_path / "assistant_settings.json").exists()


def test_existing_settings_json_wins_over_stray_legacy(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.write_text(json.dumps({"settings": {"user_name": "NEW"}}), encoding="utf-8")
    _write_legacy(tmp_path, "assistant_settings.json", {"user_name": "OLD"})

    assert app_config.load_section("settings", cfg) == {"user_name": "NEW"}
    # no migration ran — the stray legacy file is left alone
    assert (tmp_path / "assistant_settings.json").exists()


# ── section isolation ────────────────────────────────────────────────────────

def test_saving_one_section_preserves_the_others(tmp_path):
    cfg = _cfg(tmp_path)
    app_config.save_section("settings", {"a": 1}, cfg)
    app_config.save_section("chat_window_config", {"b": 2}, cfg)
    app_config.save_section("floating_window_config", {"c": 3}, cfg)

    app_config.save_section("chat_window_config", {"b": 99}, cfg)

    assert app_config.load_section("settings", cfg) == {"a": 1}
    assert app_config.load_section("chat_window_config", cfg) == {"b": 99}
    assert app_config.load_section("floating_window_config", cfg) == {"c": 3}


def test_non_dict_section_reads_as_empty(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.write_text(json.dumps({"settings": ["not", "a", "dict"]}), encoding="utf-8")
    assert app_config.load_section("settings", cfg) == {}


def test_corrupt_settings_json_degrades_to_empty_not_crash(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.write_text("{truncated", encoding="utf-8")
    assert app_config.load_section("settings", cfg) == {}
    # a save still succeeds and repairs the file
    assert app_config.save_section("settings", {"ok": True}, cfg)
    assert app_config.load_section("settings", cfg) == {"ok": True}
