"""
systema/net/web_research.py — the backend engine for the built-in `web_search` tool.

A reliable, keyless-by-default web search + page reader. Lifted and hardened from
the old `web-browser` skill (skills/web-browser/scripts/browser.py) which this
tool replaces.

Three search tiers, tried in order, degrading gracefully:
  1. Optional keyed / self-hosted backends (higher reliability, opt-in via config):
     SearXNG instance, Brave Search API, Tavily API.
  2. Keyless scrapers (always available, no key): DuckDuckGo library,
     DuckDuckGo HTML, Bing HTML, Brave HTML.
Hardening: user-agent rotation, retry/backoff between engines, a short in-memory
result cache, and href de-duplication.

Page reading uses trafilatura (readability) with a BeautifulSoup fallback and
cookie/consent noise stripping. An OPTIONAL Playwright renderer handles JS-heavy
pages when the `playwright` package is installed and enabled in config.

All heavy imports are lazy (inside functions) so importing this module stays cheap
— it is loaded unconditionally because the tool is always on.

Public API (used by ToolManager.run_web_search):
    search(query, max_results=8, config=None) -> list[{title, href, body, engine}]
    open_page(url, config=None, max_chars=8000) -> {url, title, text, truncated, engine}
    links(url, config=None, max_results=50) -> list[{text, href}]
    playwright_available() -> bool
"""

from __future__ import annotations

import time
import random
from urllib.parse import urlencode, urljoin, urlparse, parse_qs, unquote

# ── Config / constants ─────────────────────────────────────────────────────────
_HTTP_TIMEOUT = 12
_MAX_PAGE_CHARS = 8000
_CACHE_TTL = 120          # seconds a search result stays cached
_CACHE_MAX = 64           # max cached queries (simple LRU-ish trim)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

_SEARCH_CACHE: dict = {}   # {(query, max, backend): (ts, results)}


def get_headers(referer: str = "") -> dict:
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        h["Referer"] = referer
    return h


def _cfg(config, key, default=None):
    try:
        return (config or {}).get(key, default)
    except Exception:
        return default


# ── Keyless scrapers ───────────────────────────────────────────────────────────

# Persistent DDGS client (module-level — a shared session preserves cookies/headers
# across calls, which is why a fresh `with DDGS()` per call gets blocked).
_ddgs = None


def _search_ddg_library(query: str, max_results: int, config=None) -> list:
    global _ddgs
    from duckduckgo_search import DDGS
    if _ddgs is None:
        _ddgs = DDGS()
    out = []
    for r in _ddgs.text(query, max_results=max_results):
        out.append({"title": r.get("title", ""), "href": r.get("href", ""),
                    "body": r.get("body", "")})
    return out


def _ddg_html_parse(soup, max_results: int) -> list:
    def unwrap(href: str) -> str:
        if "uddg=" in href:
            qs = parse_qs(urlparse(href).query)
            return unquote(qs.get("uddg", [href])[0])
        return href

    results = []
    for div in soup.select(".result"):
        a = div.select_one(".result__a")
        snip = div.select_one(".result__snippet")
        if not a:
            continue
        href = unwrap(a.get("href", ""))
        title = a.get_text(strip=True)
        if title and href:
            results.append({"title": title, "href": href,
                            "body": snip.get_text(strip=True) if snip else ""})
        if len(results) >= max_results:
            break
    if results:
        return results

    for art in soup.select("article, [data-testid='result']"):
        a = art.select_one("h2 a, h3 a, a[href]")
        snip = art.select_one("p, [data-testid='result-snippet']")
        if not a:
            continue
        href = unwrap(a.get("href", ""))
        title = a.get_text(strip=True)
        if title and href.startswith("http"):
            results.append({"title": title, "href": href,
                            "body": snip.get_text(strip=True) if snip else ""})
        if len(results) >= max_results:
            break
    return results


def _search_ddg_html(query: str, max_results: int, config=None) -> list:
    import requests
    from bs4 import BeautifulSoup

    headers = get_headers(referer="https://duckduckgo.com/")
    post_headers = {**headers, "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://duckduckgo.com"}
    resp = requests.post("https://html.duckduckgo.com/html/",
                         data={"q": query, "b": "", "kl": ""},
                         headers=post_headers, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    results = _ddg_html_parse(BeautifulSoup(resp.text, "html.parser"), max_results)
    if results:
        return results
    get_url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query, "kl": ""})
    resp2 = requests.get(get_url, headers=headers, timeout=_HTTP_TIMEOUT)
    resp2.raise_for_status()
    return _ddg_html_parse(BeautifulSoup(resp2.text, "html.parser"), max_results)


def _search_bing(query: str, max_results: int, config=None) -> list:
    import requests
    from bs4 import BeautifulSoup
    url = "https://www.bing.com/search?" + urlencode({"q": query, "count": max_results})
    resp = requests.get(url, headers=get_headers(referer="https://www.bing.com/"),
                        timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        snip = li.select_one(".b_caption p") or li.select_one("p")
        if not a:
            continue
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if title and href.startswith("http"):
            results.append({"title": title, "href": href,
                            "body": snip.get_text(strip=True) if snip else ""})
        if len(results) >= max_results:
            break
    return results


def _search_brave(query: str, max_results: int, config=None) -> list:
    import requests
    from bs4 import BeautifulSoup
    url = "https://search.brave.com/search?" + urlencode({"q": query})
    resp = requests.get(url, headers=get_headers(referer="https://search.brave.com/"),
                        timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for div in soup.select(".snippet"):
        a = div.select_one(".snippet-title a") or div.select_one("a")
        snip = div.select_one(".snippet-description") or div.select_one("p")
        if not a:
            continue
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if title and href.startswith("http"):
            results.append({"title": title, "href": href,
                            "body": snip.get_text(strip=True) if snip else ""})
        if len(results) >= max_results:
            break
    return results


# ── Optional keyed / self-hosted backends (opt-in via config) ───────────────────

def _search_searxng(query: str, max_results: int, config=None) -> list:
    base = _cfg(config, "searxng_url")
    if not base:
        return []
    import requests
    url = base.rstrip("/") + "/search?" + urlencode({"q": query, "format": "json"})
    resp = requests.get(url, headers=get_headers(), timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    out = []
    for r in data.get("results", [])[:max_results]:
        out.append({"title": r.get("title", ""), "href": r.get("url", ""),
                    "body": r.get("content", "")})
    return out


def _search_brave_api(query: str, max_results: int, config=None) -> list:
    key = _cfg(config, "brave_api_key")
    if not key:
        return []
    import requests
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode(
        {"q": query, "count": max_results})
    resp = requests.get(url, timeout=_HTTP_TIMEOUT, headers={
        "Accept": "application/json", "X-Subscription-Token": key})
    resp.raise_for_status()
    out = []
    for r in resp.json().get("web", {}).get("results", [])[:max_results]:
        out.append({"title": r.get("title", ""), "href": r.get("url", ""),
                    "body": r.get("description", "")})
    return out


def _search_tavily(query: str, max_results: int, config=None) -> list:
    key = _cfg(config, "tavily_api_key")
    if not key:
        return []
    import requests
    resp = requests.post("https://api.tavily.com/search", timeout=_HTTP_TIMEOUT, json={
        "api_key": key, "query": query, "max_results": max_results})
    resp.raise_for_status()
    out = []
    for r in resp.json().get("results", [])[:max_results]:
        out.append({"title": r.get("title", ""), "href": r.get("url", ""),
                    "body": r.get("content", "")})
    return out


def _engine_chain(config) -> list:
    """Backend order: configured (reliable) backends first, then keyless scrapers."""
    chain = []
    if _cfg(config, "searxng_url"):
        chain.append(("SearXNG", _search_searxng))
    if _cfg(config, "brave_api_key"):
        chain.append(("Brave API", _search_brave_api))
    if _cfg(config, "tavily_api_key"):
        chain.append(("Tavily", _search_tavily))
    chain += [
        ("DuckDuckGo library", _search_ddg_library),
        ("DuckDuckGo HTML", _search_ddg_html),
        ("Bing", _search_bing),
        ("Brave", _search_brave),
    ]
    return chain


def _dedupe(results: list, limit: int) -> list:
    seen, out = set(), []
    for r in results:
        href = (r.get("href") or "").rstrip("/")
        if not href or href in seen:
            continue
        seen.add(href)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def search(query: str, max_results: int = 8, config=None) -> list:
    """Search the web. Returns [{title, href, body, engine}], first engine that
    yields non-empty results wins. Cached briefly to avoid hammering engines."""
    query = (query or "").strip()
    if not query:
        return []
    ckey = (query, max_results, _cfg(config, "searxng_url", "") or "")
    hit = _SEARCH_CACHE.get(ckey)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    last_err = None
    for name, fn in _engine_chain(config):
        try:
            results = fn(query, max_results, config) or []
            results = _dedupe(results, max_results)
            if results:
                for r in results:
                    r["engine"] = name
                if len(_SEARCH_CACHE) >= _CACHE_MAX:
                    _SEARCH_CACHE.clear()
                _SEARCH_CACHE[ckey] = (time.time(), results)
                return results
        except Exception as e:
            last_err = f"{name}: {type(e).__name__}: {str(e)[:120]}"
        time.sleep(0.6)   # polite gap + lets a rate-limit cool down
    if last_err:
        raise RuntimeError(f"All search engines failed. Last: {last_err}")
    return []


# ── Page reading ────────────────────────────────────────────────────────────────

_LINE_NOISE = [
    "cookie", "cookies", "we use cookies", "consent", "gdpr", "privacy policy",
    "accept all", "reject all", "manage preferences", "by continuing",
    "your experience", "personaliz", "advertis", "technical storage",
    "legitimate interest", "opt-out", "opt out", "subscribe to our newsletter",
    "sign up for", "enable javascript", "please enable", "javascript is required",
]


def _filter_noise(text: str) -> str:
    clean = []
    for line in text.splitlines():
        low = line.lower().strip()
        if not low:
            clean.append(line)
            continue
        if any(kw in low for kw in _LINE_NOISE) and len(low) < 120:
            continue
        clean.append(line)
    return "\n".join(clean).strip()


def playwright_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("playwright") is not None


def _render_with_playwright(url: str) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=random.choice(USER_AGENTS))
            page.goto(url, wait_until="networkidle", timeout=30000)
            return page.content()
        finally:
            browser.close()


def fetch_html(url: str, config=None) -> str:
    """Return raw HTML. Uses Playwright (JS render) when enabled + installed,
    else trafilatura's fetcher (good bot-detection handling)."""
    if _cfg(config, "use_playwright") and playwright_available():
        html = _render_with_playwright(url)
        if html:
            return html
    import trafilatura
    html = trafilatura.fetch_url(url)
    if not html:
        raise RuntimeError("Empty response — site may block scrapers or the URL is invalid.")
    return html


def _html_to_text(html: str) -> str:
    import trafilatura
    text = trafilatura.extract(html, include_comments=False, include_tables=False,
                               favor_recall=True)
    if text and text.strip():
        return _filter_noise(text.strip())

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                     "noscript", "iframe"]):
        tag.decompose()
    NOISE = ["cookie", "consent", "gdpr", "privacy-banner", "modal", "overlay",
             "popup", "banner", "notice", "toast", "advert", "promo", "subscribe",
             "newsletter", "paywall", "sticky"]
    for el in soup.find_all(True):
        combined = (" ".join(el.get("id", "").lower().split()) + " "
                    + " ".join(el.get("class", []) or []).lower())
        if any(kw in combined for kw in NOISE):
            el.decompose()
    for selector in ["article", "main", '[role="main"]', ".mw-parser-output",
                     ".post-content", ".entry-content", ".article-body",
                     ".story-body", "#content", "#main-content", "body"]:
        container = soup.select_one(selector)
        if not container:
            continue
        lines = [el.get_text(separator=" ", strip=True)
                 for el in container.find_all(
                     ["p", "li", "h1", "h2", "h3", "h4", "h5", "blockquote"])]
        text = "\n".join(l for l in lines if l)
        if text.strip():
            return _filter_noise(text.strip())
    return _filter_noise(soup.get_text(separator="\n", strip=True))


def _page_title(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        t = BeautifulSoup(html, "html.parser").title
        return t.get_text(strip=True) if t else ""
    except Exception:
        return ""


def open_page(url: str, config=None, max_chars: int = _MAX_PAGE_CHARS,
              offset: int = 0) -> dict:
    """Fetch a URL and return cleaned readable text, windowed.

    `offset` selects WHERE the window starts, so a long page can be read in
    successive calls. Without it the text beyond the cap was simply
    unreachable — the caller's only options were to guess a more specific page
    or find a raw/API mirror, neither of which exists for most sites.

    Returns `total_chars` and `next_offset` (None when the end is reached) so
    the caller can continue deterministically instead of re-fetching blindly.
    """
    html = fetch_html(url, config)
    full = _html_to_text(html)
    total = len(full)
    offset = max(0, int(offset or 0))
    if offset >= total and total:
        return {"url": url, "title": _page_title(html), "text": "",
                "truncated": False, "offset": offset, "total_chars": total,
                "next_offset": None,
                "engine": "playwright" if (_cfg(config, "use_playwright")
                                           and playwright_available()) else "trafilatura"}

    text = full[offset:offset + max_chars] if max_chars else full[offset:]
    end = offset + len(text)
    truncated = end < total
    next_offset = end if truncated else None
    if truncated:
        text = text.rstrip() + (
            f"\n\n[... {total - end} more characters — continue with "
            f"offset: {next_offset} ...]")
    return {"url": url, "title": _page_title(html), "text": text,
            "truncated": truncated, "offset": offset, "total_chars": total,
            "next_offset": next_offset,
            "engine": "playwright" if (_cfg(config, "use_playwright")
                                       and playwright_available()) else "trafilatura"}


def links(url: str, config=None, max_results: int = 50) -> list:
    """Return outgoing links from a page: [{text, href}]."""
    html = fetch_html(url, config)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(url, href)
        if urlparse(full).scheme not in ("http", "https") or full in seen:
            continue
        seen.add(full)
        out.append({"text": (a.get_text(strip=True) or "(no text)")[:80], "href": full})
        if len(out) >= max_results:
            break
    return out
