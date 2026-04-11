# Workflow

## Step 0: Prepare Run and Resume Any Backlog

Before new discovery:

- run `python -m job_apply_bot prepare-run`
- this validates the profile, creates a run row in SQLite, seeds one `run_search_queries` row per enabled Google query, and requeues any stale `jobs.status='applying'` rows back to `ready_to_apply`
- call `python -m job_apply_bot workflow-status --run-id <id>` immediately after `prepare-run`
- if backlog exists, use `next-job --mark-applying` to resume those jobs before claiming a new search query

Backlog draining is the only place the workflow should rely on the local SQLite sort order.

## Step 1: Run Discovery Slots Per Board

After backlog exists, `run-workflow` should drain it first with the apply pool.

Then discovery should run one logical slot per enabled board/query by default. Each query row is run-scoped and should be processed independently. If the same run is resumed with `run-workflow --run-id <id>`, continue any `in_progress` query from its persisted `results_seen`, `jobs_ingested`, and `cursor_json` state. A brand-new `prepare-run` still creates a fresh run and reseeds queries from the start.

## Step 2: Harvest Google Result Pages Into the Queue

Each Google query should be generated from:
- the exact phrases in `APPLICANT_TARGET_ROLE_KEYWORDS`
- the enabled source keys in `APPLICANT_ENABLED_SEARCH_SITES`
- the source-domain registry in `SEARCH_SPEC.md`

The query shape is:
- `site:<domain> ("role 1" OR "role 2" ...) ("united states" OR "remote")`

For each query:
- force Google's `Past 24 hours` filter
- force Google's date-sorted / newest-first view when available
- harvest one visible Google results page at a time
- persist the full visible page of normalized results into `run_search_results`
- continue across reachable Google result pages until there are no new pages left for that source or `APPLICANT_DISCOVERY_MAX_PAGES` pages have been harvested for that source
- if Google shows a CAPTCHA or anti-bot interstitial, keep that same Playwright page open, wait up to 10 minutes for manual solve, poll browser state every 10 seconds, then continue only if the normal SERP returns

Discovery should not apply to jobs directly.

Instead:
- store each visible Google result row in page order
- persist the next Google page cursor on the query row
- let the apply/resolution pool consume queued result rows immediately

## Step 3: Resolve Queued Results, Expand Listing Pages, and Ingest Jobs

For each claimed queued result:

- if the URL is a direct job page, extract when available:

- title
- company
- location
- source
- job URL
- posted date
- date discovered

- Immediately call `ingest-job --allow-unverifiable-freshness` with the extracted metadata so SQLite performs normalization, filtering, and duplicate checks before any application attempt.

- if the URL is a listing page instead of a direct job page:
  - extract child job links in visible order across up to `APPLICANT_DISCOVERY_MAX_PAGES` listing pages
  - insert them into `run_search_results` as child rows
  - mark the parent row `expanded`

Persist progress after every step:
- after each harvested Google page, update `run_search_queries.results_seen` and `cursor_json`
- after each direct job ingestion, increment `run_search_queries.jobs_ingested`
- keep the query row `in_progress` until `complete-query` or `fail-query`

## Step 4: Filter, Deduplicate, and Apply Immediately When Ready

Keep jobs posted in the last 24 hours when freshness can be verified.

If posted date is ambiguous or unavailable:
- try to infer it from the page
- if freshness still cannot be verified, keep the job in the apply queue and record that freshness was unverified

If `ingest-job` reports any of the following, mark the queued result terminal and move on:
- duplicate in the same run
- duplicate from a prior terminal attempt
- old / filtered out / non-matching role
- non-U.S. location

Only proceed to application when the job becomes `ready_to_apply`. When a queued result produces a `ready_to_apply` job, the same apply worker should claim that job and apply immediately in the same worker session.

## Step 5: Apply

For each `ready_to_apply` job:

1. Open job page in Playwright
2. Confirm it is an application page
3. Click apply if needed
4. If Playwright encounters a CAPTCHA or clear anti-bot challenge for that specific job, keep that same Playwright page open for manual solve
5. Poll browser state every 10 seconds for up to 10 minutes while the user solves the challenge manually
6. Continue the same application in that same Playwright session after challenge markers disappear and the normal application UI returns
7. If the challenge is still present after 10 minutes, record `blocked` with a `captcha` finding for that job
8. Fill required fields using local applicant data
9. If a required answer is missing, make a reasonable assumption based on the applicant profile, resume, and `applicant.md` instead of skipping the job for that reason alone
10. Keep any hard facts already present in the applicant files consistent, while using profile-based assumptions for missing supporting details such as salary expectations, start date, and concise free-response summaries
11. Upload resume / cover letter if configured
12. Review form
13. Submit
14. Record the application result in SQLite immediately

Do not skip a job solely because some application information is unavailable if a reasonable profile-based assumption can be supplied.

## Step 6: Record Findings for Non-Clean Outcomes

If the application cannot complete cleanly:

- record `failed` for transient or unverifiable submission outcomes
- record `blocked` for permanent workflow blockers such as login walls, unsupported flows, closed roles, disqualifying requirements, or CAPTCHA challenges that still block submission after the 10-minute Playwright wait window
- record `incomplete` only when the site requires a truthful hard fact or file that cannot be reasonably inferred from the applicant materials
- add one or more structured `record-finding` entries with the application status, workflow stage, category, summary, detail, and page URL

If Playwright hits a CAPTCHA:
- treat the CAPTCHA signal as a manual-solve wait trigger for that same Playwright session instead of marking it blocked immediately
- poll browser state every 10 seconds for up to 10 minutes while the challenge is present
- if the 10-minute wait expires and the challenge still blocks the workflow, record `blocked` and add a `record-finding` entry with category `captcha`

Only `failed` is retryable later, and only if the same job is rediscovered in a future search run.
`blocked`, `incomplete`, `submitted`, and `duplicate_skipped` are terminal outcomes for duplicate prevention.

## Step 7: Close the Claimed Query and Continue Until Empty

Repeat until:
- after a query is fully harvested, call `complete-query --run-id <id> --source-key <key>`
- if a query-level failure prevents finishing that query, call `fail-query --run-id <id> --source-key <key> --message <message>` and continue with the remaining queries
- a run may pause for up to 10 minutes while a visible CAPTCHA challenge is waiting for manual user interaction
- after each backlog drain or query completion, call `workflow-status --run-id <id>`
- the run is complete only when `workflow-status` reports:
  - `ready_jobs = 0`
  - `applying_jobs = 0`
  - `queries_pending = 0`
  - `queries_in_progress = 0`
  - `search_results_pending = 0`
  - `search_results_processing = 0`
- `drained_with_errors = true` is acceptable when some queries failed but every query is terminal and no jobs remain

## Step 8: Final Summary

Print:
- jobs found
- jobs skipped as old
- jobs skipped as duplicates
- jobs attempted
- jobs successfully applied
- jobs failed
- search queries completed / failed / pending
- requeued stale jobs count
- structured findings grouped by category
- latest blocked / incomplete / failed finding summaries for workflow follow-up

Only call `finish-run --run-id <id>` after `workflow-status` reports `drained=true`, unless an operator intentionally uses `--force`.
