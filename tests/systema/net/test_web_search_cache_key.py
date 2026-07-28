"""
tests/systema/net/test_web_search_cache_key.py

Adding a Brave or Tavily key used to look like it did nothing. `search()` keyed
its cache on (query, max_results, searxng_url) only, so a keyless result cached
minutes earlier was served even though `_engine_chain` would now put the paid
backend first — the user sets a key, searches the same thing to check, and gets
the identical keyless answer back.

The key covers backend PRESENCE now. Presence only: a credential must never sit
in a cache key, and presence is all that reorders the chain.
"""
import pytest

from systema.net import web_research as wr


@pytest.fixture
def stub_backends(monkeypatch):
    """Every backend answers with its own marker. No network, no sleeps."""
    def _mk(name):
        def _fn(query, max_results, config=None):
            return [{"title": name, "href": f"http://{name}", "body": ""}]
        return _fn

    for attr, marker in (
        ("_search_ddg_library", "ddg"),
        ("_search_ddg_html", "ddg-html"),
        ("_search_bing", "bing"),
        ("_search_brave", "brave-scrape"),
        ("_search_brave_api", "brave-api"),
        ("_search_tavily", "tavily"),
        ("_search_searxng", "searxng"),
    ):
        monkeypatch.setattr(wr, attr, _mk(marker))

    wr._SEARCH_CACHE.clear()
    yield
    wr._SEARCH_CACHE.clear()


def test_adding_a_brave_key_is_not_served_the_keyless_cached_result(stub_backends):
    assert wr.search("same query")[0]["title"] == "ddg"

    keyed = wr.search("same query", config={"brave_api_key": "abc"})
    assert keyed[0]["title"] == "brave-api"


def test_adding_a_tavily_key_is_not_served_the_keyless_cached_result(stub_backends):
    assert wr.search("q")[0]["title"] == "ddg"
    assert wr.search("q", config={"tavily_api_key": "abc"})[0]["title"] == "tavily"


def test_removing_a_key_falls_back_instead_of_serving_the_keyed_result(stub_backends):
    assert wr.search("q", config={"brave_api_key": "abc"})[0]["title"] == "brave-api"
    assert wr.search("q", config={})[0]["title"] == "ddg"


def test_the_secret_itself_never_lands_in_the_cache_key(stub_backends):
    wr.search("q", config={"brave_api_key": "SUPER-SECRET",
                           "tavily_api_key": "ALSO-SECRET"})

    flat = repr(list(wr._SEARCH_CACHE.keys()))
    assert "SUPER-SECRET" not in flat
    assert "ALSO-SECRET" not in flat


def test_an_unchanged_config_still_hits_the_cache(stub_backends):
    cfg = {"brave_api_key": "abc"}
    wr.search("q", config=cfg)
    assert len(wr._SEARCH_CACHE) == 1

    wr.search("q", config=cfg)
    assert len(wr._SEARCH_CACHE) == 1, "same inputs must not add a second entry"
