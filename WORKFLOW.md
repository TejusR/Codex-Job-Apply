# Workflow

## Step 0: Start Run and Resume Any Backlog

Before new discovery:

- create a run row in SQLite
- optionally drain any leftover `ready_to_apply` jobs from interrupted work
- if backlog exists, use `next-job --mark-applying` to resume those jobs before running new searches

Backlog draining is the only place the workflow should rely on the local SQLite sort order.

## Step 1: Search

Run `python -m job_apply_bot validate-profile` first and use the returned `google_search_queries`.

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

## Step 2: Extract Job Metadata and Ingest Immediately

For each opened candidate, extract when available:

- title
- company
- location
- source
- job URL
- posted date
- date discovered

Immediately call `ingest-job --allow-unverifiable-freshness` with the extracted metadata so SQLite performs normalization, filtering, and duplicate checks before any application attempt.

## Step 3: Filter and Decide Immediately

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

## Step 4: Apply

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

## Step 5: Record Findings for Non-Clean Outcomes

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

## Step 6: Continue Until Empty

Repeat until:
- there are no backlog jobs left to resume
- no enabled query has any new relevant candidates left
- every discovered eligible job has been attempted or explicitly recorded as blocked, incomplete, duplicate, or submitted

## Step 7: Final Summary

Print:
- jobs found
- jobs skipped as old
- jobs skipped as duplicates
- jobs attempted
- jobs successfully applied
- jobs failed
- structured findings grouped by category
- latest blocked / incomplete / failed finding summaries for workflow follow-up
