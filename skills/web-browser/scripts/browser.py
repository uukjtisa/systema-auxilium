#!/usr/bin/env python3
"""
web-browser skill — agent-facing CLI
Usage:
  python browser.py search   "<query>" [--max N]
  python browser.py read     "<url>"
  python browser.py links    "<url>" [--max N]
  python browser.py page     "<filepath>"
"""

import re
import sys
import argparse
import tempfile
import textwrap
import time
import random
from pathlib import Path

# Force UTF-8 on Windows (prevents UnicodeEncodeError on cp1252 consoles)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
PAGE_SIZE_BYTES         = 10_000   # 10 KB per page  (read / links)
SEARCH_RESULTS_PER_PAGE = 5        # results per page (search)

TEMP_DIR = Path(tempfile.gettempdir()) / "agent_browser_cache"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────
# Browser headers — mimics a real Chrome session
# ──────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

def get_headers(referer: str = "") -> dict:
    ua = random.choice(USER_AGENTS)
    h = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",  # no brotli — requests can't decode br natively
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        h["Referer"] = referer
    return h


# ──────────────────────────────────────────────
# Search engines (tried in order)
# ──────────────────────────────────────────────

def _ddg_html_parse(soup, max_results: int) -> list:
    """
    Try multiple selector strategies against a DDG HTML response.
    DDG periodically changes its class names — this tries them all.
    Returns the first strategy that produces results.
    """
    from urllib.parse import parse_qs, urlparse, unquote

    def unwrap_ddg_url(href: str) -> str:
        if "uddg=" in href:
            qs = parse_qs(urlparse(href).query)
            return unquote(qs.get("uddg", [href])[0])
        return href

    # Strategy 1: classic layout (pre-2024)
    #   <div class="result"> → <a class="result__a"> + <a class="result__snippet">
    results = []
    for div in soup.select(".result"):
        a = div.select_one(".result__a")
        snip = div.select_one(".result__snippet")
        if not a:
            continue
        href = unwrap_ddg_url(a.get("href", ""))
        title = a.get_text(strip=True)
        body = snip.get_text(strip=True) if snip else ""
        if title and href:
            results.append({"title": title, "href": href, "body": body})
        if len(results) >= max_results:
            break
    if results:
        return results

    # Strategy 2: newer layout — results sit in <article> or <div> with data-testid
    for article in soup.select("article, [data-testid='result']"):
        a = article.select_one("h2 a, h3 a, a[href]")
        snip = article.select_one("p, [data-testid='result-snippet']")
        if not a:
            continue
        href = unwrap_ddg_url(a.get("href", ""))
        title = a.get_text(strip=True)
        body = snip.get_text(strip=True) if snip else ""
        if title and href and href.startswith("http"):
            results.append({"title": title, "href": href, "body": body})
        if len(results) >= max_results:
            break
    if results:
        return results

    # Strategy 3: broadest sweep — every <h2>/<h3> with a real link nearby
    for heading in soup.select("h2, h3"):
        a = heading.find("a", href=True) or heading.find_next_sibling("a", href=True)
        if not a:
            continue
        href = unwrap_ddg_url(a.get("href", ""))
        if not href.startswith("http"):
            continue
        title = heading.get_text(strip=True) or a.get_text(strip=True)
        # grab the next <p> as snippet
        p = heading.find_next("p")
        body = p.get_text(strip=True) if p else ""
        if title:
            results.append({"title": title, "href": href, "body": body})
        if len(results) >= max_results:
            break
    if results:
        return results

    # Nothing matched — log what classes are actually present so we can debug
    all_classes = set()
    for el in soup.find_all(True, class_=True):
        for c in (el.get("class") or []):
            all_classes.add(c)
    sample = sorted(all_classes)[:30]
    print(f"[DEBUG] DDG HTML: no selectors matched. Classes found: {sample}")
    return []


def _search_ddg_html(query: str, max_results: int) -> list:
    """
    DuckDuckGo HTML endpoint — bypasses VQD token entirely.
    Tries POST first (standard), then GET (sometimes returns different markup).
    Parsing uses multi-strategy selector logic to survive DDG layout changes.
    """
    import requests
    from bs4 import BeautifulSoup

    base_headers = get_headers()
    base_headers["Referer"] = "https://duckduckgo.com/"

    # Attempt 1: POST (standard)
    post_headers = {**base_headers,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://duckduckgo.com"}
    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "b": "", "kl": ""},
        headers=post_headers,
        timeout=12,
    )
    resp.raise_for_status()
    results = _ddg_html_parse(BeautifulSoup(resp.text, "html.parser"), max_results)
    if results:
        return results

    # Attempt 2: GET fallback (DDG occasionally serves cleaner markup via GET)
    print("[WARN] DDG HTML POST returned 0 — retrying via GET...")
    from urllib.parse import urlencode
    get_url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query, "kl": ""})
    resp2 = requests.get(get_url, headers=base_headers, timeout=12)
    resp2.raise_for_status()
    return _ddg_html_parse(BeautifulSoup(resp2.text, "html.parser"), max_results)


def _search_bing(query: str, max_results: int) -> list:
    """Bing HTML scrape fallback."""
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import urlencode

    params = urlencode({"q": query, "count": max_results})
    url = f"https://www.bing.com/search?{params}"
    headers = get_headers(referer="https://www.bing.com/")

    resp = requests.get(url, headers=headers, timeout=12)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        snippet_tag = li.select_one(".b_caption p") or li.select_one("p")
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a.get("href", "")
        body = snippet_tag.get_text(strip=True) if snippet_tag else ""
        if title and href and href.startswith("http"):
            results.append({"title": title, "href": href, "body": body})
        if len(results) >= max_results:
            break

    return results


def _search_brave(query: str, max_results: int) -> list:
    """Brave Search HTML scrape — second fallback."""
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import urlencode

    params = urlencode({"q": query})
    url = f"https://search.brave.com/search?{params}"
    headers = get_headers(referer="https://search.brave.com/")

    resp = requests.get(url, headers=headers, timeout=12)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    for div in soup.select(".snippet"):
        a = div.select_one(".snippet-title a") or div.select_one("a")
        snippet_tag = div.select_one(".snippet-description") or div.select_one("p")
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a.get("href", "")
        body = snippet_tag.get_text(strip=True) if snippet_tag else ""
        if title and href and href.startswith("http"):
            results.append({"title": title, "href": href, "body": body})
        if len(results) >= max_results:
            break

    return results



# ──────────────────────────────────────────────
# Persistent DDGS client (module-level — same trick that makes test.py work.
# A shared session preserves cookies/headers across calls, which is why
# `with DDGS() as ddgs:` per-call fails: it gets a fresh blocked session every time.)
# ──────────────────────────────────────────────
from duckduckgo_search import DDGS as _DDGS
_ddgs = _DDGS()


def _search_ddg_library(query: str, max_results: int) -> list:
    """
    duckduckgo_search library using the persistent module-level session.
    This is the primary engine — it works when the session is kept alive.
    """
    return list(_ddgs.text(query, max_results=max_results))


# Engine registry — tried in order, each with a friendly name
# DDG library (persistent session) goes first — same pattern as test.py which works
SEARCH_ENGINES = [
    ("DuckDuckGo library", _search_ddg_library),
    ("DuckDuckGo HTML",    _search_ddg_html),
    ("Bing",               _search_bing),
    ("Brave",              _search_brave),
]


def multi_engine_search(query: str, max_results: int) -> list:
    """
    Try each engine in order.
    Returns the first successful non-empty result list.
    """
    for name, fn in SEARCH_ENGINES:
        try:
            print(f"[SEARCH] Trying {name}...")
            results = fn(query, max_results)
            if results:
                print(f"[SEARCH] {name} returned {len(results)} results.\n")
                return results
            else:
                print(f"[WARN] {name} returned 0 results — trying next engine.")
        except Exception as e:
            short = str(e)[:120]
            print(f"[WARN] {name} failed: {short} — trying next engine.")
        time.sleep(1)  # small polite gap between attempts

    return []


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def clean_name(s: str) -> str:
    """Turn a URL or query into a safe short filename token."""
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^\w\-]", "_", s)
    return s[:60]


def paginate_and_save(content: str, key: str, prefix: str = "result") -> list:
    """Split content into PAGE_SIZE_BYTES chunks, save to temp, return paths."""
    chunks = textwrap.wrap(content, PAGE_SIZE_BYTES, break_long_words=True, break_on_hyphens=False)
    if not chunks:
        chunks = ["(empty page)"]
    name = clean_name(key)
    paths = []
    for i, chunk in enumerate(chunks, 1):
        p = TEMP_DIR / f"{prefix}_{name}_page{i}.txt"
        p.write_text(chunk, encoding="utf-8")
        paths.append(p)
    return paths


def fetch_html(url: str) -> tuple:
    """Return (raw_html, final_url) using trafilatura (handles bot-detection better)."""
    import trafilatura
    html = trafilatura.fetch_url(url)
    if not html:
        raise RuntimeError("Empty response — site may block scrapers or URL is invalid.")
    return html, url


def html_to_text(html: str) -> str:
    """
    Extract clean readable text from HTML.

    Strategy:
      1. trafilatura with favor_recall=True  — best for articles, tries harder on homepages
      2. BS4 with cookie/consent/overlay stripping — handles Wikipedia & JS-heavy pages
      3. Full-page BS4 text dump                  — last resort
    """
    import trafilatura

    # favor_recall=True makes trafilatura try harder on non-article pages (news homepages etc.)
    text = trafilatura.extract(html, include_comments=False, include_tables=False, favor_recall=True)
    if text and text.strip():
        return _filter_noise(text.strip())

    # ── BS4 fallback ──────────────────────────────────────────────────────────
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    # 1. Nuke structural noise tags
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"]):
        tag.decompose()

    # 2. Nuke cookie banners, consent dialogs, GDPR overlays, modals
    #    Matches any element whose id or class contains these keywords
    NOISE_KEYWORDS = [
        "cookie", "consent", "gdpr", "privacy-banner", "privacy-notice",
        "privacy-policy", "modal", "overlay", "popup", "banner", "notice",
        "notification", "toast", "alert-bar", "advert", "advertisement",
        "promo", "subscribe", "newsletter", "paywall", "sticky",
    ]
    for el in soup.find_all(True):
        el_id    = " ".join(el.get("id",    "").lower().split())
        el_class = " ".join(el.get("class", []) or []).lower()
        combined = el_id + " " + el_class
        if any(kw in combined for kw in NOISE_KEYWORDS):
            el.decompose()

    # 3. Prefer semantic content containers in priority order
    for selector in [
        "article",
        "main",
        '[role="main"]',
        ".mw-parser-output",   # Wikipedia
        ".post-content",
        ".entry-content",
        ".article-body",
        ".story-body",
        "#content",
        "#main-content",
        "body",
    ]:
        container = soup.select_one(selector)
        if not container:
            continue
        lines = [
            el.get_text(separator=" ", strip=True)
            for el in container.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "blockquote"])
        ]
        text = "\n".join(l for l in lines if l)
        if text.strip():
            return _filter_noise(text.strip())

    # 4. Last resort: all visible text
    return _filter_noise(soup.get_text(separator="\n", strip=True))


# Noise keywords used to filter individual lines of extracted text
_LINE_NOISE = [
    "cookie", "cookies", "we use cookies", "consent", "gdpr",
    "privacy policy", "accept all", "reject all", "manage preferences",
    "by continuing", "your experience", "personaliz", "advertis",
    "technical storage", "legitimate interest", "opt-out", "opt out",
    "subscribe to our newsletter", "sign up for", "enable javascript",
    "please enable", "javascript is required",
]

def _filter_noise(text: str) -> str:
    """
    Drop individual lines that are clearly cookie/consent/legal boilerplate.
    A line is dropped if:
      - it's very short (< 30 chars) AND contains a noise keyword, OR
      - it's under 120 chars AND starts with a noise phrase
    Keeps the rest untouched.
    """
    clean = []
    for line in text.splitlines():
        low = line.lower().strip()
        if not low:
            clean.append(line)
            continue
        is_noise = any(kw in low for kw in _LINE_NOISE)
        if is_noise and len(low) < 120:
            continue   # drop it
        clean.append(line)
    return "\n".join(clean).strip()


def extract_links_from_html(html: str, base_url: str) -> list:
    """Return list of {text, href} from page."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin, urlparse

    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(base_url, href)
        if urlparse(full).scheme not in ("http", "https"):
            continue
        if full in seen:
            continue
        seen.add(full)
        text = a.get_text(strip=True) or "(no text)"
        links.append({"text": text[:80], "href": full})
    return links


# ──────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────

def cmd_search(query: str, max_results: int = 8):
    """Search with multi-engine fallback, paginate results if > SEARCH_RESULTS_PER_PAGE."""
    print(f"[SEARCH] Query: {query}\n")

    results = multi_engine_search(query, max_results)

    if not results:
        print(
            "[ERROR] All search engines failed or returned no results.\n"
            "Suggestions:\n"
            "  - Check your internet connection\n"
            "  - Try the `read` command with a direct URL instead\n"
            "  - Try a different or simpler query"
        )
        return

    # Build one result block per result
    blocks = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url   = r.get("href",  "")
        body  = r.get("body",  "")
        blocks.append(f"### {i}. {title}\n**URL:** {url}\n{body}")

    total = len(blocks)

    if total <= SEARCH_RESULTS_PER_PAGE:
        header = f"## Search Results for: `{query}` ({total} results)\n"
        print(header + "\n\n".join(blocks))
    else:
        page_total = -(-total // SEARCH_RESULTS_PER_PAGE)  # ceiling div
        paths = []
        for page_num, i in enumerate(range(0, total, SEARCH_RESULTS_PER_PAGE), 1):
            chunk = blocks[i:i + SEARCH_RESULTS_PER_PAGE]
            header = (
                f"## Search Results for: `{query}`\n"
                f"Page {page_num}/{page_total} — "
                f"results {i+1}-{min(i+SEARCH_RESULTS_PER_PAGE, total)} of {total}\n"
            )
            content = header + "\n\n".join(chunk)
            p = TEMP_DIR / f"search_{clean_name(query)}_page{page_num}.txt"
            p.write_text(content, encoding="utf-8")
            paths.append(p)

        file_list = "\n".join(f"  - {p.resolve()}" for p in paths)
        print(
            f"[PAGINATED] {total} results split across {len(paths)} pages "
            f"({SEARCH_RESULTS_PER_PAGE} results/page).\n"
            f"Files saved to temp:\n{file_list}\n\n"
            f"View any page with:  python browser.py page \"<filepath>\"\n"
            f"{'─'*60}\n"
            f"PAGE 1:\n\n{paths[0].read_text(encoding='utf-8')}"
        )


def cmd_read(url: str):
    """Fetch a URL and return clean text. Paginates if > 10KB."""
    print(f"[READ] {url}\n")
    try:
        html, final_url = fetch_html(url)
    except Exception as e:
        print(f"[ERROR] Failed to fetch page: {e}")
        return

    text = html_to_text(html)
    size = len(text.encode("utf-8"))

    if size <= PAGE_SIZE_BYTES:
        print(f"[CONTENT] From: {final_url}\n")
        print(text)
    else:
        pages = paginate_and_save(text, url)
        file_list = "\n".join(f"  - {p.resolve()}" for p in pages)
        first_page = pages[0].read_text(encoding="utf-8")

        print(
            f"[PAGINATED] Content exceeds safe context limit.\n"
            f"Total pages: {len(pages)}\n"
            f"Files saved to temp:\n{file_list}\n\n"
            f"View any page with:  python browser.py page \"<filepath>\"\n"
            f"{'─'*60}\n"
            f"PAGE 1 CONTENT:\n\n{first_page}"
        )


def cmd_links(url: str, max_links: int = 30):
    """Extract and list all links found on a page."""
    print(f"[LINKS] {url}\n")
    try:
        html, final_url = fetch_html(url)
    except Exception as e:
        print(f"[ERROR] Failed to fetch page: {e}")
        return

    links = extract_links_from_html(html, final_url)[:max_links]

    if not links:
        print("No links found on this page.")
        return

    lines = [f"## Links found on: {final_url}\n"]
    for i, lnk in enumerate(links, 1):
        lines.append(f"{i}. [{lnk['text']}]({lnk['href']})")

    print("\n".join(lines))


def cmd_page(filepath: str):
    """Read a previously saved pagination file."""
    p = Path(filepath)
    if not p.exists():
        p = TEMP_DIR / Path(filepath).name
    if not p.exists():
        print(f"[ERROR] File not found: {Path(filepath).resolve()}")
        print(f"   Temp cache dir: {TEMP_DIR.resolve()}")
        return
    print(f"[PAGE] {p.resolve()}\n{'─'*60}\n")
    print(p.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Agent web browser — search, read, extract links, paginate.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python browser.py search "best python web scraping libraries" --max 5
          python browser.py read   "https://example.com/article"
          python browser.py links  "https://news.ycombinator.com" --max 20
          python browser.py page   "/tmp/agent_browser_cache/search_best_python_page2.txt"
        """)
    )

    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="Search the web via multi-engine fallback")
    s.add_argument("query")
    s.add_argument("--max", type=int, default=8, dest="max_results")

    r = sub.add_parser("read", help="Fetch and read a URL")
    r.add_argument("url")

    lk = sub.add_parser("links", help="Extract all links from a URL")
    lk.add_argument("url")
    lk.add_argument("--max", type=int, default=30, dest="max_links")

    pg = sub.add_parser("page", help="Read a saved pagination file")
    pg.add_argument("filepath")

    args = parser.parse_args()

    if args.command == "search":
        cmd_search(args.query, args.max_results)
    elif args.command == "read":
        cmd_read(args.url)
    elif args.command == "links":
        cmd_links(args.url, args.max_links)
    elif args.command == "page":
        cmd_page(args.filepath)


if __name__ == "__main__":
    main()
