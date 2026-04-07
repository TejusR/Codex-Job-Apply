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
4. create a run with `python -m job_apply_bot start-run`
5. use Playwright MCP for search, extraction, and application steps
6. use `python -m job_apply_bot ingest-job` for filtering and dedupe
7. use `python -m job_apply_bot next-job --mark-applying` to pull the next job
8. use `python -m job_apply_bot record-application` after each attempt
9. use `python -m job_apply_bot finish-run` for the final summary

## Strong Guidance

Tell Codex to:
- treat markdown files as source-of-truth requirements
- force Google `Past 24 hours` and date-sorted / newest-first results during search
- honor `APPLICANT_ENABLED_SEARCH_SITES` from `.env` when deciding which search sources to run
- keep changes minimal and organized
- log every application attempt
- never submit duplicate applications
- never invent missing answers
- report any missing `.env` keys or document paths explicitly before attempting submissions
