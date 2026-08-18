# Web search precision — parked analysis, not a plan

> Moved out of the Project Ideas backlog on 2026-08-18. It was never work: it
> is a code reading with an explicit entry condition that has never fired, and
> it sat in a list of actionable items pretending otherwise. It lives here,
> next to the code it describes, so the analysis survives without implying
> anyone should act on it. **Do not build any of this without the evidence the
> entry condition demands.**

## Status: PARKED ON PURPOSE (2026-07-28)

This was analysed and deliberately NOT built. The user reports web search works
fine, and there is no failed-search report anywhere. Everything below is derived
from reading the code, not from an observed failure.

**Entry condition: a real failed-search report.** A concrete query where the
results were wrong or useless, with the log line naming the backend that
answered. Without that, building this trades a working feature for a slower,
more rate-limited one on a hunch.

## The observation

`systema/net/web_research.py`, `search()` — the backend loop is **first-wins**:
it walks `_engine_chain(config)` and returns the first backend that yields any
non-empty result list. "Non-empty" is the only quality bar, so a single junk hit
ends the chain and Bing, Brave and a configured Tavily key never run.

Note the consequence for the obvious "fix": simply ADDING more providers makes
this worse, not better, because new entries land at the end of a chain that
usually stops at the first one.

## Candidate changes, most valuable first

1. **Merge and rank instead of first-wins.** Query the top 2-3 backends, merge,
   and score by cross-engine agreement — a URL three engines return is almost
   certainly relevant. This is the actual precision win and it needs no new
   providers.
2. **Minimum-result threshold.** Accept a backend only at N or more results,
   otherwise keep walking and merge. Cheap, and useful on its own without 1.
3. **Parallel fan-out.** The chain is serial with a `time.sleep(0.6)` between
   attempts and a 12s per-request timeout (`_HTTP_TIMEOUT`), so a bad run can
   take well over a minute. Firing the top backends concurrently is faster AND
   gathers more.
4. **Domain quality filter.** `_filter_noise` cleans page TEXT but nothing
   filters the result LIST, and keyless scrapers rank content farms highly.

## The argument against, which currently wins

- Merging costs 2-3x the network calls per query. On the keyless scrapers
  (DuckDuckGo, Bing, Brave) that raises rate-limit and soft-ban exposure — which
  would be a reliability REGRESSION in the name of reliability.
- The user has no API keys configured, so the chain is four keyless scrapers.
  Merging scrapers yields less than merging a paid backend would.
- Slower agent turns on every single search, to fix a failure mode nobody has
  seen.

## Already done, not part of this

- The cache-key bug is FIXED (2026-07-28): `search()` keyed its cache without the
  Brave/Tavily keys, so adding a key served the stale keyless result and looked
  broken. Covered by `tests/systema/net/test_web_search_cache_key.py`.
- Settings UI for the four backend keys is BUILT (Settings > System > Web
  Search), so the optional backends are reachable without hand-editing
  `data/settings.json`.
- The Playwright renderer is FIXED (2026-07-28). It raised NotImplementedError
  on every use: `duckduckgo_search` sets WindowsSelectorEventLoopPolicy at
  import, a Selector loop cannot spawn subprocesses on Windows, and DDG is the
  first backend in the chain — so the policy was always already poisoned by the
  time a page was opened. `_render_with_playwright` now forces a Proactor policy
  and restores DDG's afterwards, and a failed render degrades to the plain fetch
  instead of killing the page read.
  **This does NOT count as evidence for the merge-and-rank work above.** It was
  a crash in an optional renderer, not a result-quality problem. The entry
  condition is still a query that came back wrong or useless.

## If it does get built

Settings it would need, beyond the four existing credential fields: search depth
(result count), a strategy selector (Fast / Balanced / Thorough) rather than five
separate knobs, request timeout, and the domain blocklist. Put the strategy
behind one dropdown — the point is one decision, not a panel of dials.
