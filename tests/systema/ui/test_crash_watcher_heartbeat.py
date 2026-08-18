"""The UI-thread heartbeat must never touch the disk.

Regression guard for the hitch reports of 2026-08-02: `beat()` wrote
heartbeat.txt from the GUI thread every 5 s and was the culprit in 10 of the
50 recorded hitches. The stamp is now an in-memory float; the CrashWatcher
daemon thread does the writing.
"""

import time

from systema.ui import crash_watcher


def test_beat_writes_nothing_to_disk(tmp_path, monkeypatch):
    hb = tmp_path / "heartbeat.txt"
    monkeypatch.setattr(crash_watcher, "HEARTBEAT_FILE", hb)
    monkeypatch.setattr(crash_watcher, "_last_beat", 0.0)

    before = time.time()
    crash_watcher.beat()

    assert not hb.exists(), "beat() must not perform any filesystem I/O"
    assert crash_watcher._last_beat >= before


def test_persist_beat_writes_a_parsable_stamp(tmp_path, monkeypatch):
    hb = tmp_path / "dumps" / "heartbeat.txt"
    monkeypatch.setattr(crash_watcher, "HEARTBEAT_FILE", hb)
    monkeypatch.setattr(crash_watcher, "_last_beat", 0.0)

    crash_watcher._persist_beat()
    assert not hb.exists(), "nothing to persist before the first beat"

    crash_watcher.beat()
    crash_watcher._persist_beat()

    assert hb.exists()
    stamp = hb.read_text(encoding="utf-8")
    # The watcher's own staleness math depends on field 0 being a float.
    assert abs(float(stamp.split()[0]) - crash_watcher._last_beat) < 0.001
    assert "pid=" in stamp
