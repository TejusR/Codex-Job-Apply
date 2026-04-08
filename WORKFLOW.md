# Workflow

## Step 0: Prepare Run and Resume Any Backlog

Before new discovery:

- run `python -m job_apply_bot prepare-run`
- this validates the profile, creates a run row in SQLite, seeds one `run_search_queries` row per enabled Google query, and requeues any stale `jobs.status='applying'` rows back to `ready_to_apply`
- call `python -m job_apply_bot workflow-status --run-id <id>` immediately after `prepare-run`
- if backlog exists, use `next-job --mark-applying` to resume those jobs before claiming a new search query

Backlog draining is the only place the workflow should rely on the local SQLite sort order.

## Step 1: Claim Exactly One Query at a Time

After backlog is empty, call `python -m job_apply_bot next-query --run-id <id>`.

If `next-query` returns `null`, there are no pending queries left for the run.

Each query row is run-scoped and should be processed independently. If the run is interrupted mid-query, the next run will reseed that query from the start and rely on SQLite dedupe to avoid duplicate applications.

## Step 2: Search Exhaustively for the Claimed Query

Each Google query should be generated from:
- the exact phrases in `APPLICANT_TARGET_ROLE_KEYWORDS`
- the enabled source keys in `APPLICANT_ENABLED_SEARCH_SITES`
- the source-domain registry in `SEARCH_SPEC.md`

The query shape is:
- `site:<domain> ("role 1" OR "role 2" ...) ("united states" OR "remote")`

For each query:
- force Google's `Past 24 hours` filter
- force Google's date-sorted / newest-first view when available
- continue across all reachable Google result pages and relevant listing pages until there are no new candidate links left for that source

Do not collect the full query into a batch before applying.

Instead:
- open each concrete job link as it is discovered
- if a result is a listing page instead of a direct job page, extract the child job links from that page and process those child links one by one before returning to the search results
- process links in the order the search result page or listing page presents them

## Step 3: Extract Job Metadata and Ingest Immediately

For each opened candidate, extract when available:

- title
- company
- location
- source
- job URL
- posted date
- date discovered

Immediately call `ingest-job --allow-unverifiable-freshness` with the extracted metadata so SQLite performs normalization, filtering, and duplicate checks before any application attempt.

## Step 4: Filter and Decide Immediately

Keep jobs posted in the last 24 hours when freshness can be verified.

If posted date is ambiguous or unavailable:
- try to infer it from the page
- if freshness still cannot be verified, keep the job in the apply queue and record that freshness was unverified

If `ingest-job` reports any of the following, skip to the next candidate immediately:
- duplicate in the same run
- duplicate from a prior terminal attempt
- old / filtered out / non-matching role
- non-U.S. location

Only proceed to application when the job becomes `ready_to_apply`.

## Step 5: Apply

For each `ready_to_apply` job:

1. Open job page in Playwright
2. Confirm it is an application page
3. Click apply if needed
4. If Playwright encounters a CAPTCHA or clear anti-bot challenge for that specific job, reopen the same job in `@camoufox-browser` and continue the application there
5. Use the Camoufox fallback only for the current affected job, then return to Playwright for the rest of the run
6. Fill required fields using local applicant data
7. If a required answer is missing, make a reasonable assumption based on the applicant profile, resume, and `applicant.md` instead of skipping the job for that reason alone
8. Keep any hard facts already present in the applicant files consistent, while using profile-based assumptions for missing supporting details such as salary expectations, start date, and concise free-response summaries
9. Upload resume / cover letter if configured
10. Review form
11. Submit
12. Record the application result in SQLite immediately

Do not skip a job solely because some application information is unavailable if a reasonable profile-based assumption can be supplied.

## Step 6: Record Findings for Non-Clean Outcomes

If the application cannot complete cleanly:

- record `failed` for transient or unverifiable submission outcomes
- record `blocked` for permanent workflow blockers such as login walls, unsupported flows, closed roles, disqualifying requirements, or CAPTCHA challenges that still block submission after the Camoufox fallback attempt
- record `incomplete` only when the site requires a truthful hard fact or file that cannot be reasonably inferred from the applicant materials
- add one or more structured `record-finding` entries with the application status, workflow stage, category, summary, detail, and page URL

If Playwright hits a CAPTCHA:
- treat the CAPTCHA signal as a tool-switch trigger for that job instead of marking it blocked immediately
- if Camoufox also cannot get past the challenge, record `blocked` and add a `record-finding` entry with category `captcha`

Only `failed` is retryable later, and only if the same job is rediscovered in a future search run.
`blocked`, `incomplete`, `submitted`, and `duplicate_skipped` are terminal outcomes for duplicate prevention.

## Step 7: Close the Claimed Query and Continue Until Empty

Repeat until:
- after a query is fully processed, call `complete-query --run-id <id> --source-key <key>`
- if a query-level failure prevents finishing that query, call `fail-query --run-id <id> --source-key <key> --message <message>` and continue with the remaining queries
- after each backlog drain or query completion, call `workflow-status --run-id <id>`
- the run is complete only when `workflow-status` reports:
  - `ready_jobs = 0`
  - `applying_jobs = 0`
  - `queries_pending = 0`
  - `queries_in_progress = 0`
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
