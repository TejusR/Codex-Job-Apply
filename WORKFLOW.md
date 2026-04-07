# Workflow

## Step 1: Search

Open a browser and search Google for the following:

1. `jobright.ai`
2. `site:boards.greenhouse.io ("software engineer" AND "united states")`
3. `site:jobs.ashbyhq.com`

If `.env` includes `APPLICANT_ENABLED_SEARCH_SITES`, only run the enabled source queries.

For each query:
- force Google's `Past 24 hours` filter
- force Google's date-sorted / newest-first view when available
- continue across all reachable Google result pages and relevant listing pages until there are no new candidate links left for that source

Collect candidate job posting links from search results.

## Step 2: Extract Job Metadata

For each result, extract when available:

- title
- company
- location
- source
- job URL
- posted date
- date discovered

## Step 3: Filter

Keep jobs posted in the last 24 hours when freshness can be verified.

If posted date is ambiguous or unavailable:
- try to infer it from the page
- if freshness still cannot be verified, keep the job in the apply queue and record that freshness was unverified

## Step 4: Normalize

Normalize each job URL so tracking is stable:
- remove obvious tracking parameters
- preserve canonical job identity
- produce a deterministic `job_key`

## Step 5: Deduplicate

Before applying:
- check SQLite
- skip any job already marked as applied
- skip any job already attempted in the same run

## Step 6: Sort

Sort remaining jobs by posted date descending.
Most recent jobs go first.

## Step 7: Apply

For each remaining job:

1. Open job page
2. Confirm it is an application page
3. Click apply if needed
4. Fill required fields using local applicant data
5. Upload resume / cover letter if configured
6. Review form
7. Submit
8. Record result in SQLite

## Step 8: Continue Until Empty

Repeat until there are no unprocessed jobs left.

## Step 9: Final Summary

Print:
- jobs found
- jobs skipped as old
- jobs skipped as duplicates
- jobs attempted
- jobs successfully applied
- jobs failed
