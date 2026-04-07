# Search Spec

## Google Queries

Build Google queries from the current `.env` values instead of using a fixed three-query list.

### Inputs

- `APPLICANT_TARGET_ROLE_KEYWORDS`
- `APPLICANT_ENABLED_SEARCH_SITES`

Use the exact role phrases from `APPLICANT_TARGET_ROLE_KEYWORDS`.
The current workspace role list is:
- `software engineer`
- `backend engineer`
- `full stack engineer`
- `software developer`

If `APPLICANT_ENABLED_SEARCH_SITES` is omitted, run all supported source keys.

### Source Domains

- `jobright` -> `jobright.ai`
- `greenhouse` -> `boards.greenhouse.io`
- `ashby` -> `jobs.ashbyhq.com`
- `workable` -> `apply.workable.com`
- `jobvite` -> `jobs.jobvite.com`
- `jazz` -> `app.jazz.co`
- `adp` -> `recruiting.adp.com`
- `lever` -> `jobs.lever.co`
- `bamboohr` -> `bamboohr.com`
- `paylocity` -> `recruiting.paylocity.com`
- `smartrecruiters` -> `jobs.smartrecruiters.com`
- `gem` -> `jobs.gem.com`
- `dover` -> `app.dover.com`

### Query Template

For each enabled source, run:

`site:<domain> ("software engineer" OR "backend engineer" OR "full stack engineer" OR "software developer") ("united states" OR "remote")`

In execution, prefer the `google_search_queries` returned by `python -m job_apply_bot validate-profile`, since those are generated from the current `.env`.

## Search Execution Rules

For each Google query:
- force the `Past 24 hours` filter
- force Google's date-sorted / most-recent view when it is available
- if Google does not expose a usable date-sorted control for that query, keep the 24-hour filter and rely on page-level date extraction while processing results in the order they are surfaced
- continue paginating through all reachable result pages for the enabled source until there are no new relevant candidates left to ingest
- open each result immediately instead of collecting the whole query into a batch first
- if a result is a listing page, extract child job links from that page and process those child links in page order before returning to Google

## Preferred Result Types

Prioritize links that appear to be:
- direct job pages
- job search result pages
- recent listings pages
- company-hosted listing pages

## Filtering Rules

Keep a job only if:
- it is in the United States or remote within the United States
- it is a software engineer role or a close variant
- it was posted within the last 24 hours when that freshness can be verified

If freshness is ambiguous or unavailable:
- try to infer it from the page
- if freshness still cannot be verified, keep the job eligible for application and record that the freshness was unverified

## Processing Order Rule

For new discovery:
1. process Google results in the order shown for the current query
2. process listing-page child jobs in the order shown on that page
3. ingest, decide, and apply immediately before moving to the next candidate

Only backlog jobs recovered with `next-job` should use the local SQLite ordering by `posted_at DESC, discovered_at DESC`.

## Duplicate Rule

A job is a duplicate if the same canonical URL or the same `job_key` already exists in the database with a terminal outcome:
- application status `submitted`
- application status `incomplete`
- application status `blocked`
- application status `duplicate_skipped`
- job status `applied`
- job status `incomplete`
- job status `blocked`
- job status `duplicate_skipped`
- job status `applying`

`failed` is intentionally not terminal. It may be retried only if the same job is rediscovered in a later run.

## Canonicalization Suggestions

- lowercase hostname
- strip query params like `utm_*`, `gh_jid`, click trackers when safe
- preserve path and any true job ID
- prefer page canonical URL when available
