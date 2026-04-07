# Search Spec

## Google Queries

### Query A
Source key: `jobright`
`jobright.ai`

### Query B
Source key: `greenhouse`
`site:boards.greenhouse.io ("software engineer" AND "united states")`

### Query C
Source key: `ashby`
`site:jobs.ashbyhq.com`

## Search Execution Rules

If `.env` includes `APPLICANT_ENABLED_SEARCH_SITES`, only run the queries whose source keys are enabled.

For each Google query:
- force the `Past 24 hours` filter
- force Google's date-sorted / most-recent view when it is available
- if Google does not expose a usable date-sorted control for that query, keep the 24-hour filter and rely on page-level date extraction plus local SQLite sorting

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
- it was posted within the last 24 hours

## Sort Rule

After filtering, sort by:
1. posted timestamp descending
2. discovery timestamp descending

This local sort is still required even if Google already appears date-sorted.

## Duplicate Rule

A job is a duplicate if the same canonical URL or the same `job_key` already exists in the database with:
- `applied`
- `submitted`
- `in_progress`
- `duplicate_skipped`

## Canonicalization Suggestions

- lowercase hostname
- strip query params like `utm_*`, `gh_jid`, click trackers when safe
- preserve path and any true job ID
- prefer page canonical URL when available
