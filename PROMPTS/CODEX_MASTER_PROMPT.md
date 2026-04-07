# Codex Master Prompt

Read all markdown files in this repository and execute the job-application workflow they describe.

Use the existing implementation when present. Only create or modify code if the current repository state is missing a required capability or needs a minimal fix before the workflow can continue safely.

Before doing browser work:
- Read all markdown files in this repository.
- Load the root-level `.env` and `applicant.md`.
- Use `resume/Tejus Resume_SDE_V2.pdf` as the resume source.
- Run `python -m job_apply_bot validate-profile`.
- If any required applicant fields, files, or Playwright MCP tools are missing, list the exact missing items and continue only as far as possible without faking completion.
- If `.env` includes `APPLICANT_ENABLED_SEARCH_SITES`, only use the enabled sources during search.

Requirements:
- Use the markdown files in this repo as the source of truth.
- Build a local workflow that uses Playwright MCP for browser automation.
- Use SQLite to track discovered jobs and submitted applications.
- Search Google for:
  1. `jobright.ai`
  2. `site:boards.greenhouse.io ("software engineer" AND "united states")`
  3. `site:jobs.ashbyhq.com`
- For each Google query, force the `Past 24 hours` filter and Google's date-sorted / most-recent view when available.
- Treat those three queries as source-keyed toggles: `jobright`, `greenhouse`, and `ashby`.
- Filter to jobs posted within the last 24 hours.
- Sort jobs by most recent first.
- Skip any job already applied to or already attempted.
- Apply to each remaining job one by one until no jobs are left.
- Record all results in SQLite.

Implementation constraints:
- Organize the code cleanly.
- Add a README section describing how to run it.
- Use safe URL canonicalization for deduplication.
- Never fabricate application answers.
- If a required answer is unavailable, mark the job incomplete and skip submission.
- Log failures clearly.
- Print a final summary with totals.

Execution contract:
- Create a run with `python -m job_apply_bot start-run`.
- For each discovered candidate, call `python -m job_apply_bot ingest-job` with the extracted metadata so filtering and dedupe happen in SQLite-backed state.
- Pull the next job with `python -m job_apply_bot next-job --mark-applying`.
- After each application attempt, call `python -m job_apply_bot record-application`.
- Finish with `python -m job_apply_bot finish-run` and print the returned summary.

Execution behavior:
- First inspect the repository and summarize the implementation plan.
- Then create or update the needed code.
- Then run the workflow if the required environment and tools are available.
- If Playwright MCP or applicant data is missing, explain exactly what is missing and continue as far as possible without faking completion.
