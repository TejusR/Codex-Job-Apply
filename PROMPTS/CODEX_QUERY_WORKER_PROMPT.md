Read the workflow documents and execute exactly one Google SERP harvesting turn for the single query in the runtime context.

Requirements:
- Use the repository files named in the runtime context as source of truth.
- Use browser tooling to open Google for the exact query in the runtime context.
- Prefer Playwright browser tools first in spawned `codex exec` sessions because they are the most reliable MCP path in this environment.
- Use Camoufox only as a fallback when Playwright hits a blocker that the workflow docs explicitly allow Camoufox to handle.
- Respect `profile.discovery_max_pages` from the runtime context and harvest at most that many Google result pages for this source.
- Respect the persisted query cursor in the runtime context. If a cursor is present, reopen Google and navigate to that next results page before harvesting.
- Force Google's `Past 24 hours` filter and newest-first/date-sorted view when available.
- Harvest the full visible SERP page only. Do not open each result page during this discovery turn.
- Return normalized visible search results in page order.
- Each result must include `url`, `title`, `snippet`, `visible_date`, `page_number`, and `rank`.
- If Google shows a CAPTCHA or anti-bot interstitial, switch that same discovery turn to a visible Camoufox window, emit only ASCII status text while waiting, wait as long as needed for manual solve, then continue in the same Camoufox session.
- Do not edit tracked repository source files.

You must return one outcome for this single discovery turn:
- `results_page` when you harvested at least one visible Google result from the current page
- `exhausted` when there are no more Google result pages left for this query
- `query_failed` when a query-level failure prevents harvesting this page

Output contract:
- Always return valid JSON matching the provided schema.
- Always include `outcome`, `results`, `next_page`, and `query_error`.
- For `results_page`, include every visible result from the current Google page in `results`.
- Set `next_page` to an object describing the next page to visit, or `null` when the current page is the last page or the configured `profile.discovery_max_pages` cap has been reached.
- Set `query_error` to `null` unless the outcome is `query_failed`.
- For `exhausted`, return an empty `results` list and `next_page = null`.
- For `query_failed`, return an empty `results` list, `next_page = null`, and a non-empty `query_error`.

Do not return prose outside the JSON object.
