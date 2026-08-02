"""
tests/systema/common/test_api_call_counts.py

The API request/response counters behind Settings ▸ AI ▸ API Requests, and the
shared bucketer they share with the two token graphs.

Tokens say what a turn COST; these say whether the provider actually answered.
The gap between the two series is the signal, so they are logged separately
rather than derived from one another.
"""
from datetime import datetime, timedelta, timezone

import pytest

from systema.common import token_est as te


@pytest.fixture
def counters(tmp_path, monkeypatch):
    """Point the counter files at a tmp dir — never touch the user's real logs."""
    monkeypatch.setattr(te, "_REQUEST_FILE", tmp_path / "api_requests.json")
    monkeypatch.setattr(te, "_RESPONSE_FILE", tmp_path / "api_responses.json")
    return tmp_path


# ── counting ────────────────────────────────────────────────────────────────

def test_each_call_logs_one_and_they_are_counted_separately(counters):
    for _ in range(7):
        te.log_request()
    for _ in range(5):
        te.log_response()

    sent = sum(v for _, v in te.get_request_data("Daily"))
    got = sum(v for _, v in te.get_response_data("Daily"))
    assert sent == 7
    assert got == 5
    # The whole reason for two series: 2 calls never came back.
    assert sent - got == 2


def test_counters_start_empty_rather_than_erroring(counters):
    assert te.get_request_data("Daily") == []
    assert te.get_response_data("Daily") == []


def test_logging_never_raises_even_when_the_path_is_unwritable(monkeypatch, tmp_path):
    """Telemetry must never be able to break a chat turn."""
    bad = tmp_path / "nope"
    bad.write_text("i am a file, not a directory", encoding="utf-8")
    monkeypatch.setattr(te, "_REQUEST_FILE", bad / "sub" / "api_requests.json")
    te.log_request()            # must not raise


def test_the_log_is_capped_so_it_cannot_grow_forever(counters, monkeypatch):
    path = te._REQUEST_FILE
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{"ts": now, "n": 1}] * 100_000, f)
    te.log_request()
    with open(path, encoding="utf-8") as f:
        assert len(json.load(f)) == 100_000


# ── the shared bucketer ─────────────────────────────────────────────────────

def test_every_series_goes_through_one_bucketer():
    """Input tokens, output tokens, requests and responses all log the same
    shape and want the same windows. The bucketing was copied verbatim per
    series, which is how four graphs drift apart the first time a window is
    adjusted."""
    now = datetime.now(timezone.utc)
    entries = [{"ts": (now - timedelta(days=i)).isoformat(), "n": 10}
               for i in range(5)]
    out = te.bucket_entries(entries, "Daily")
    assert len(out) == 5
    assert all(v == 10 for _, v in out)
    assert out == sorted(out, key=lambda kv: kv[0]), "buckets must be ordered"


def test_bucketer_tolerates_empty_and_malformed_entries():
    assert te.bucket_entries([], "Daily") == []
    assert te.bucket_entries([{"ts": "not-a-date", "n": 1}], "Daily") == []
    assert te.bucket_entries([{"n": 1}], "Daily") == []


def test_bucketer_respects_each_mode_window():
    now = datetime.now(timezone.utc)
    old = [{"ts": (now - timedelta(days=400)).isoformat(), "n": 5}]
    # Well outside the Minutes/Hourly/Daily windows.
    assert te.bucket_entries(old, "Minutes") == []
    assert te.bucket_entries(old, "Hourly") == []
    assert te.bucket_entries(old, "Daily") == []
    # Yearly has no cutoff, so it survives there.
    assert sum(v for _, v in te.bucket_entries(old, "Yearly")) == 5


@pytest.mark.parametrize("mode", ["Minutes", "Hourly", "Daily", "Weekly",
                                  "Monthly", "Yearly", "All"])
def test_no_mode_returns_more_points_than_the_graph_can_draw(mode):
    now = datetime.now(timezone.utc)
    entries = [{"ts": (now - timedelta(minutes=i)).isoformat(), "n": 1}
               for i in range(500)]
    assert len(te.bucket_entries(entries, mode)) <= te.MAX_POINTS.get(mode, 20)


def test_token_getters_still_route_through_the_shared_bucketer(monkeypatch):
    seen = {}

    def spy(entries, mode="Daily"):
        seen[mode] = seen.get(mode, 0) + 1
        return []

    monkeypatch.setattr(te, "bucket_entries", spy)
    te.get_usage_data("Hourly")
    te.get_output_usage_data("Hourly")
    te.get_request_data("Hourly")
    te.get_response_data("Hourly")
    assert seen == {"Hourly": 4}, "a series bypassing the shared bucketer will drift"
