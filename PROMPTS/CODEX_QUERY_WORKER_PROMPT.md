Read the workflow documents and perform exactly one discovery step for one claimed search query.

Requirements:
- Use the repository files named in the runtime context as source of truth.
- Use browser tooling for Google search and page inspection.
- Prefer Playwright browser tools first in spawned `codex exec` sessions because they are the most reliable MCP path in this environment.
- Use Camoufox only as a fallback when Playwright cannot complete the required search or page inspection step.
- Force Google's Past 24 hours filter and newest-first ordering when available.
- Process results in the order shown.
- Skip any URL already present in `current_run_seen_urls`.
- Skip any URL already listed in `query_skipped_results`.
- If a search result is a listing page, traverse it until you can return one concrete candidate job or determine that the result should be skipped.
- Do not apply to jobs in this worker. Discovery only.
- Do not edit repository files.

Return exactly one JSON object matching the provided schema:
- `candidate`: you found one next concrete candidate job to hand back to the supervisor
- `skip_result`: the next relevant result cannot be processed and should be persisted in the skip table
- `exhausted`: no unseen relevant candidates remain for this query
- `query_failed`: the query cannot continue because of a query-level blocker such as rate limits, search failures, or browser/tool breakage

Always include every top-level schema field. Set unused fields to `null`.

For `candidate`, populate:
- `raw_url`
- `canonical_url`
- `source`
- `title`
- `company`
- `location`
- `posted_at`
- `page_url`

For `skip_result`, populate:
- `result_url`
- `skip_reason`

For `query_failed`, populate:
- `error_message`

Return JSON only.
