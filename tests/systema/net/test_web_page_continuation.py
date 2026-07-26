"""
tests/systema/net/test_web_page_continuation.py

Reading a long page used to hit a hard wall: the text was cut at the cap and the
remainder was simply unreachable — the only advice was "refine your query", and
for most sites there is no raw/API mirror to fall back to.

`open_page(offset=...)` windows the page, and the truncation notice now tells
the caller the exact offset to continue from.
"""
import pytest

from systema.net import web_research as wr


@pytest.fixture
def long_page(monkeypatch):
    """A deterministic 1000-char page, no network."""
    body = "".join(f"{i%10}" for i in range(1000))
    monkeypatch.setattr(wr, "fetch_html", lambda url, config=None: body)
    monkeypatch.setattr(wr, "_html_to_text", lambda html: html)
    monkeypatch.setattr(wr, "_page_title", lambda html: "Long Page")
    return body


def test_first_window_reports_how_to_continue(long_page):
    page = wr.open_page("http://x", max_chars=400)

    assert page['truncated'] is True
    assert page['total_chars'] == 1000
    assert page['next_offset'] == 400
    assert "offset: 400" in page['text']


def test_the_second_window_continues_where_the_first_stopped(long_page):
    first = wr.open_page("http://x", max_chars=400)
    second = wr.open_page("http://x", max_chars=400, offset=first['next_offset'])

    assert second['offset'] == 400
    assert second['text'].startswith(long_page[400:410])
    assert second['next_offset'] == 800


def test_the_last_window_is_not_marked_truncated(long_page):
    last = wr.open_page("http://x", max_chars=400, offset=800)

    assert last['truncated'] is False
    assert last['next_offset'] is None
    assert last['text'] == long_page[800:]
    assert "more characters" not in last['text']


def test_windows_reassemble_into_the_whole_page(long_page):
    out, off = "", 0
    while off is not None:
        page = wr.open_page("http://x", max_chars=250, offset=off)
        text = page['text']
        if page['next_offset'] is not None:      # strip the continuation note
            text = text[:text.rindex("\n\n[...")]
        out += text
        off = page['next_offset']

    assert out == long_page, "windowed reads did not reconstruct the page"


def test_an_offset_past_the_end_says_so_instead_of_looping(long_page):
    page = wr.open_page("http://x", max_chars=400, offset=5000)

    assert page['text'] == ""
    assert page['next_offset'] is None


def test_a_short_page_is_never_truncated(monkeypatch):
    monkeypatch.setattr(wr, "fetch_html", lambda url, config=None: "tiny")
    monkeypatch.setattr(wr, "_html_to_text", lambda html: html)
    monkeypatch.setattr(wr, "_page_title", lambda html: "")

    page = wr.open_page("http://x", max_chars=400)

    assert page['truncated'] is False
    assert page['next_offset'] is None
    assert page['text'] == "tiny"


# ── the tool surface carries the parameter through ───────────────────────────

def test_compat_fence_parses_the_offset(tool_manager):
    bt = "`" * 3
    tm = tool_manager

    spec, _ = tm.parse_web_search(
        f"{bt}web_search: [reading on]\nhttps://example.com\nmode: open\noffset: 400\n{bt}")

    assert spec['mode'] == 'open'
    assert spec['offset'] == 400


def test_native_args_carry_the_offset(tool_manager):
    tm = tool_manager

    spec = tm.native_args_to_spec('web_search', {'mode': 'open',
                                                 'url': 'https://example.com',
                                                 'offset': '400'})

    assert spec['offset'] == 400


def test_the_observation_tells_the_model_the_next_offset(tool_manager, monkeypatch):
    """The whole point: the model must be able to read on without guessing.
    Uses a page longer than the real default cap so the tool truncates for the
    same reason it does in production."""
    body = "x" * (wr._MAX_PAGE_CHARS + 5000)
    monkeypatch.setattr(wr, "fetch_html", lambda url, config=None: body)
    monkeypatch.setattr(wr, "_html_to_text", lambda html: html)
    monkeypatch.setattr(wr, "_page_title", lambda html: "Long Page")
    tm = tool_manager
    monkeypatch.setattr(tm, "_web_config", lambda: {})

    obs = tm.run_web_search({'mode': 'open', 'query': 'http://x', 'offset': 0,
                             'max_results': 8, 'fetch_top': 0, 'error': None})

    assert "offset" in obs.lower()
