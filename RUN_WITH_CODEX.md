# How to Run This with Codex

## Best Starting Point

Open Codex in the repository root and give it one high-authority instruction:
- reference `PROMPTS/CODEX_MASTER_PROMPT.md`
- execute the workflow from that prompt

That should be the normal future entry point.

## What to Tell Codex

Use the prompt in `PROMPTS/CODEX_MASTER_PROMPT.md`.

## Execution Style

Ask Codex to:

1. inspect the repo
2. read `.env`, `applicant.md`, and the resume file
3. run `python -m job_apply_bot validate-profile`
4. use the returned `google_search_queries` to drive discovery for enabled sites
5. create a run with `python -m job_apply_bot start-run`
6. optionally use `python -m job_apply_bot next-job --mark-applying` only to drain leftover `ready_to_apply` backlog from interrupted runs
7. use Playwright MCP for search, extraction, and the default application steps
8. if Playwright hits a CAPTCHA or anti-bot challenge for a specific job, reopen that same job in `@camoufox-browser` and continue the application there
9. use the Camoufox fallback only for the affected job, then return to Playwright for the rest of the run
10. search exhaustively across reachable result pages for each enabled source instead of stopping after a sample page
11. open each discovered job link immediately instead of batching the entire query first
12. if a result is a listing page, extract and process the child job links one by one before returning to search
13. use `python -m job_apply_bot ingest-job --allow-unverifiable-freshness` as soon as metadata is extracted so filtering and dedupe happen before applying
14. use `python -m job_apply_bot record-application` after each attempt
15. when the outcome is `failed`, `incomplete`, or `blocked`, also use `python -m job_apply_bot record-finding`
16. if Camoufox also cannot get past the CAPTCHA, record `blocked` and store a finding with category `captcha`
17. use `python -m job_apply_bot finish-run` for the final summary

## Strong Guidance

Tell Codex to:
- treat markdown files as source-of-truth requirements
- use the exact role phrases from `APPLICANT_TARGET_ROLE_KEYWORDS` when building Google queries
- use the `google_search_queries` emitted by `validate-profile` or build the same query shape from enabled site domains
- force Google `Past 24 hours` and date-sorted / newest-first results during search
- keep searching until no new relevant candidates remain for each enabled source
- honor `APPLICANT_ENABLED_SEARCH_SITES` from `.env` when deciding which search sources to run
- do not drop a job solely because freshness cannot be verified
- treat CAPTCHA in Playwright as a per-job tool-switch trigger, not an immediate `blocked` outcome
- return to Playwright after the Camoufox attempt finishes for that specific job
- keep changes minimal and organized
- log every application attempt
- capture structured findings for blocked, incomplete, and failed outcomes
- never submit duplicate applications
- make reasonable assumptions for missing supporting answers based on the applicant profile, resume, and `applicant.md`
- do not skip a job solely because some application information is missing if a reasonable profile-based assumption can be supplied
- use `blocked` for CAPTCHA only when Camoufox also cannot complete the application
- treat `failed` as retryable only on later rediscovery, not as a permanent duplicate
- treat `blocked`, `incomplete`, `submitted`, and `duplicate_skipped` as terminal duplicate-prevention outcomes
- report any missing `.env` keys or document paths explicitly before attempting submissions
