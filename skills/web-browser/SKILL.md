---
name: web-browser
description: A personal web browser for AI agents. Use this skill whenever the agent needs to search the web, read web page content, extract links from pages, or navigate multi-page content. Triggers on any request to browse, google, search, open a URL, read a website, find links, or research a topic online. All browsing is free — no API keys required. Uses a multi-engine fallback system (DuckDuckGo HTML → Bing → Brave → DuckDuckGo library) with real browser headers for resilient search. Must be used when the agent needs any real-time or external web information.
---

All scripts live in the `scripts/` folder. Run from that directory or provide the full path.

---

## Commands

### 1. Search the web
```bash
python browser.py search "<your query>" [--max N]
```
- Searches DuckDuckGo (free, no API key needed)
- Returns up to N results (default: 8) as markdown
- Each result includes: title, URL, and snippet
- **If results exceed 5**, they are automatically paginated (5 results/page):
  - Page files are saved to your system's temp folder
  - Page 1 is shown immediately; use `page` command to navigate further

```
[PAGINATED] 8 results split across 2 pages (5 results/page).
Files saved to temp:
  - /tmp/agent_browser_cache/search_latest_python_news_page1.txt
  - /tmp/agent_browser_cache/search_latest_python_news_page2.txt

View any page with:  python browser.py page "<filepath>"
```

**Example:**
```bash
python browser.py search "latest python web scraping libraries" --max 5
```

---

### 2. Read a page
```bash
python browser.py read "<url>"
```
- Fetches the URL and converts it to clean markdown
- Strips ads, nav bars, scripts, and HTML noise
- **If content exceeds 10KB**, it is automatically paginated:
  - Files are saved to your system's temp folder
  - You will see a message like:

```
[PAGINATED] Content exceeds safe context limit.
Total pages: 4
Files saved to temp:
  - /tmp/agent_browser_cache/result_example_com_article_page1.txt
  - /tmp/agent_browser_cache/result_example_com_article_page2.txt
  - /tmp/agent_browser_cache/result_example_com_article_page3.txt
  - /tmp/agent_browser_cache/result_example_com_article_page4.txt

View any page with:  python browser.py page "<filepath>"
────────────────────────────────────────────────────────────
PAGE 1 CONTENT:

[page 1 shown automatically below]
```

**Example:**
```bash
python browser.py read "https://en.wikipedia.org/wiki/Web_scraping"
```

---

### 3. Extract links from a page
```bash
python browser.py links "<url>" [--max N]
```
- Returns a numbered markdown list of all links found on the page
- Default: up to 30 links
- Use this to explore what sub-pages or resources exist before diving deeper

**Example:**
```bash
python browser.py links "https://news.ycombinator.com" --max 20
```

---

### 4. Read a pagination file (navigate pages)
```bash
python browser.py page "<filepath>"
```
- Used after a `read` command returns paginated content
- Pass the full path of any page file, e.g. `_page2.txt`, `_page3.txt`
- You can skip forward or go back freely — pages are just numbered `.txt` files

**Example:**
```bash
python browser.py page "/tmp/agent_browser_cache/result_example_com_article_page3.txt"
```

---

## Browsing Strategy (for agents)

Follow this approach to browse efficiently without bloating your context:

### Searching a topic
1. Run `search` to get an overview of results
2. Pick the most relevant URL(s) from the results
3. Run `read` on the chosen URL
4. If paginated → read page 1 first. Only read more pages if you need more detail.
5. Use `links` if you want to explore sub-pages within a site

### Navigating paginated content
- **Do not** read all pages unless you must — start with page 1
- If page 1 has what you need, stop there
- If you need more, jump to the page most likely to have what you need (you can skip pages)
- Temp files persist until system cleanup — you can re-read any page later

### Extracting sub-links
1. Run `links` on a page to get its link list
2. Find the specific link you want from the numbered list
3. Run `read` on that link directly

### Context hygiene rules
- Never read a full page when a search snippet already answers your question
- When a page is paginated, treat each page as a separate context unit — only load what you need
- If you've read a page and have your answer, stop browsing. Don't explore further unnecessarily.
- Prefer `search` → skim snippets → `read` one page over blind crawling

---

## Output format

All output is plain text / markdown. No JSON wrappers. Safe to pass directly to a language model.

---

## ⚠️ Windows: Required subprocess encoding

This script outputs **UTF-8**. On Windows, if you call it via `subprocess`, you **must** explicitly set the encoding or you will get a `UnicodeDecodeError`:

```python
# ❌ WRONG — Windows defaults to cp1252, will crash on any non-ASCII content
result = subprocess.run(["python", "browser.py", "read", url],
                        capture_output=True, text=True)

# ✅ CORRECT — always pass encoding="utf-8"
result = subprocess.run(["python", "browser.py", "read", url],
                        capture_output=True, text=True, encoding="utf-8")
```

This applies to **every** subprocess call to this script — search, read, links, and page.
Forgetting it on any one of them will cause a crash on Unicode content (non-English text, special characters, emoji, symbols).

---

## Temp file location

Paginated files are saved to:
- **Linux/macOS**: `/tmp/agent_browser_cache/`
- **Windows**: `C:\Users\<user>\AppData\Local\Temp\agent_browser_cache\`

Files are named: `result_<cleaned_url>_page<N>.txt`

These files persist across runs until your OS clears temp. You can re-read them anytime using `page`.

---

## Error handling

If a page can't be fetched, you'll see:
```
[ERROR] Failed to fetch page: <reason>
```

The `search` command uses a **4-engine fallback system** with real browser headers:
1. DuckDuckGo HTML endpoint (no VQD token — bot-block resistant)
2. Bing HTML scrape
3. Brave Search HTML scrape
4. DuckDuckGo library (last resort)

Each engine logs its attempt so you can see exactly what's happening:
```
[SEARCH] Trying DuckDuckGo HTML...
[SEARCH] DuckDuckGo HTML returned 8 results.
```

Or if it falls back:
```
[SEARCH] Trying DuckDuckGo HTML...
[WARN] DuckDuckGo HTML failed: ... — trying next engine.
[SEARCH] Trying Bing...
[SEARCH] Bing returned 8 results.
```

If **all engines fail**:
```
[ERROR] All search engines failed or returned no results.
```
In that case, try using the `read` command with a direct URL instead.

Common causes of `read` / `links` failure:
- Site is behind a login / paywall
- Bot protection (Cloudflare, etc.)
- Invalid URL

In these cases, try a different source from your search results.
