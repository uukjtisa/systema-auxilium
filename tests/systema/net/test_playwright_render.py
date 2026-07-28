"""
tests/systema/net/test_playwright_render.py

The optional Playwright renderer raised NotImplementedError on every use.

`duckduckgo_search` sets `WindowsSelectorEventLoopPolicy` at IMPORT time, and a
Selector loop cannot spawn subprocesses on Windows — which is precisely what
Playwright's node driver is. DuckDuckGo is the FIRST backend in `_engine_chain`,
so by the time anyone opened a page the policy was already swapped and the
render could never work. The setting was unreachable from the UI until
2026-07-28, which is why nobody had hit it before.
"""
import asyncio
import sys

import pytest

from systema.net import web_research as wr

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the loop-policy clash is Windows-only")


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """`_PLAYWRIGHT_DISABLED` is module state that survives between tests."""
    wr._PLAYWRIGHT_DISABLED = False
    yield
    wr._PLAYWRIGHT_DISABLED = False


@pytest.fixture
def poisoned_policy():
    """The world exactly as `duckduckgo_search` leaves it."""
    prev = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    yield
    asyncio.set_event_loop_policy(prev)


def _stub_sync_playwright(monkeypatch, fn):
    import playwright.sync_api as psa
    monkeypatch.setattr(psa, "sync_playwright", fn)


def test_the_renderer_runs_under_a_proactor_policy(poisoned_policy, monkeypatch):
    seen = {}

    def _probe():
        seen['policy'] = asyncio.get_event_loop_policy()
        raise RuntimeError("far enough — we only need the policy at entry")

    _stub_sync_playwright(monkeypatch, _probe)

    with pytest.raises(RuntimeError):
        wr._render_with_playwright("http://example.invalid")

    assert isinstance(seen['policy'], asyncio.WindowsProactorEventLoopPolicy), \
        "a Selector loop cannot spawn Playwright's driver subprocess"


def test_duckduckgos_policy_is_put_back_afterwards(poisoned_policy, monkeypatch):
    """Restoring matters: DDG set that policy on purpose, and the search backends
    outnumber the renderer."""
    _stub_sync_playwright(monkeypatch, lambda: (_ for _ in ()).throw(RuntimeError("nope")))

    with pytest.raises(RuntimeError):
        wr._render_with_playwright("http://example.invalid")

    assert isinstance(asyncio.get_event_loop_policy(),
                      asyncio.WindowsSelectorEventLoopPolicy)


def test_a_failing_renderer_degrades_to_the_plain_fetch(monkeypatch):
    """The renderer is an enhancement. A missing browser binary or a render
    timeout must not take the whole page read down with it — which is what the
    propagating NotImplementedError did."""
    import trafilatura

    monkeypatch.setattr(wr, "playwright_available", lambda: True)
    monkeypatch.setattr(wr, "_render_with_playwright",
                        lambda url: (_ for _ in ()).throw(RuntimeError("no browser")))
    monkeypatch.setattr(trafilatura, "fetch_url", lambda url: "<html>plain</html>")

    html = wr.fetch_html("http://example.invalid", config={"use_playwright": True})

    assert html == "<html>plain</html>"


def test_the_renderer_is_not_retried_after_it_fails_once(monkeypatch):
    """A missing browser binary does not fix itself mid-run. Retrying spends a
    multi-second failed launch on EVERY fetch, which would make the setting feel
    like it broke page reading rather than merely not helping."""
    import trafilatura

    attempts = []

    def _boom(url):
        attempts.append(url)
        raise RuntimeError("Executable doesn't exist")

    monkeypatch.setattr(wr, "playwright_available", lambda: True)
    monkeypatch.setattr(wr, "_render_with_playwright", _boom)
    monkeypatch.setattr(trafilatura, "fetch_url", lambda url: "<html>plain</html>")

    cfg = {"use_playwright": True}
    for _ in range(4):
        assert wr.fetch_html("http://example.invalid", config=cfg) == "<html>plain</html>"

    assert len(attempts) == 1, "should give up after the first failed launch"
