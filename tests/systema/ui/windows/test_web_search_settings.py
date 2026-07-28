"""
tests/systema/ui/windows/test_web_search_settings.py

Settings ▸ System ▸ Web Search. Until this section existed, none of the four
keys `ToolManager._web_config()` reads was reachable from the UI at all — they
could only be set by hand-editing `data/settings.json`.

What is worth locking is the CONTRACT between the two sides. The settings window
writes four names and the tool reads four names; a typo in either makes a key
look saved while doing absolutely nothing, which is indistinguishable from a
backend that simply did not help.
"""
import pytest

from systema.ui.windows.settings_window import _WebBackendTestWorker


class _Ctrl:
    def __init__(self, settings):
        self.settings = settings


class _Engine:
    def __init__(self, settings):
        self.controller = _Ctrl(settings)


# Exactly what SettingsWindow writes in its save path.
SAVED_BY_SETTINGS_WINDOW = {
    'web_brave_api_key': 'brave-key',
    'web_tavily_api_key': 'tavily-key',
    'web_searxng_url': 'https://searx.example.org',
    'web_use_playwright': True,
}


# ── the settings-key contract ────────────────────────────────────────────────

def test_web_config_reads_exactly_what_the_settings_window_writes(tool_manager):
    tool_manager.ai_engine = _Engine(dict(SAVED_BY_SETTINGS_WINDOW))

    cfg = tool_manager._web_config()

    assert cfg['brave_api_key'] == 'brave-key'
    assert cfg['tavily_api_key'] == 'tavily-key'
    assert cfg['searxng_url'] == 'https://searx.example.org'
    assert cfg['use_playwright'] is True


def test_blank_fields_mean_keyless_rather_than_empty_strings(tool_manager):
    """The window saves "" for an untouched field. `_web_config` must turn that
    into None, or `_engine_chain` would put an unusable backend FIRST and every
    search would start by failing."""
    tool_manager.ai_engine = _Engine({
        'web_brave_api_key': '',
        'web_tavily_api_key': '   ',
        'web_searxng_url': '',
        'web_use_playwright': False,
    })

    cfg = tool_manager._web_config()

    assert cfg['brave_api_key'] is None
    assert cfg['tavily_api_key'] is None
    assert cfg['searxng_url'] is None
    assert cfg['use_playwright'] is False


def test_a_configured_backend_is_tried_before_the_keyless_scrapers(tool_manager):
    """End to end: saved settings -> _web_config -> the real backend order."""
    from systema.net import web_research as wr

    tool_manager.ai_engine = _Engine(dict(SAVED_BY_SETTINGS_WINDOW))
    names = [name for name, _fn in wr._engine_chain(tool_manager._web_config())]

    assert names[:3] == ["SearXNG", "Brave API", "Tavily"]
    assert "DuckDuckGo library" in names, "keyless fallback must survive"


def test_with_nothing_configured_the_chain_is_purely_keyless(tool_manager):
    from systema.net import web_research as wr

    tool_manager.ai_engine = _Engine({})
    names = [name for name, _fn in wr._engine_chain(tool_manager._web_config())]

    assert names == ["DuckDuckGo library", "DuckDuckGo HTML", "Bing", "Brave"]


# ── the "Test backends" probe ────────────────────────────────────────────────

def _probe(monkeypatch, search_impl):
    """Drive the worker body directly — the thread machinery is Qt's problem,
    the reported message is ours."""
    from systema.net import web_research as wr
    monkeypatch.setattr(wr, "search", search_impl)

    worker = _WebBackendTestWorker({'brave_api_key': 'k'})
    seen = []
    worker.done.connect(seen.append)
    worker.run()
    return seen[0]


def test_the_probe_names_the_backend_that_answered(qapp, monkeypatch):
    """Naming the backend is the whole point: a result list looks identical
    whether Brave or the keyless fallback produced it."""
    msg = _probe(monkeypatch, lambda q, max_results=8, config=None: [
        {"title": "t", "href": "h", "engine": "Brave API"}])

    assert msg.startswith("OK")
    assert "Brave API" in msg


def test_the_probe_reports_an_empty_result_set_plainly(qapp, monkeypatch):
    msg = _probe(monkeypatch, lambda q, max_results=8, config=None: [])

    assert "No results" in msg


def test_the_probe_reports_a_failure_instead_of_raising(qapp, monkeypatch):
    def _boom(q, max_results=8, config=None):
        raise RuntimeError("all engines failed")

    msg = _probe(monkeypatch, _boom)

    assert msg.startswith("Failed")
    assert "RuntimeError" in msg
