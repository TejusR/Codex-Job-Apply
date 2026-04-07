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
- Use SQLite to track discovered jobs, application outcomes, and structured workflow findings.
- Search Google for:
  1. `jobright.ai`
  2. `site:boards.greenhouse.io ("software engineer" AND "united states")`
  3. `site:jobs.ashbyhq.com`
- For each Google query, force the `Past 24 hours` filter and Google's date-sorted / most-recent view when available.
- Treat those three queries as source-keyed toggles: `jobright`, `greenhouse`, and `ashby`.
- Search exhaustively: continue through reachable Google result pages and relevant listing pages until no new candidates remain for each enabled source.
- Process each discovered candidate immediately in the order it appears for the current query instead of batching all sources first.
- If a result is a listing page rather than a direct job page, extract its child job links and process those child jobs one by one before returning to the search results.
- Filter to jobs posted within the last 24 hours when freshness can be verified.
- If freshness still cannot be verified after reasonable extraction attempts, keep the job eligible for application and record that freshness was unverified.
- Skip any job with a prior terminal application outcome, but allow prior `failed` jobs to be retried only if they are rediscovered in a later run.
- Apply to each remaining job one by one until no jobs are left.
- Record all results in SQLite.

Implementation constraints:
- Organize the code cleanly.
- Add a README section describing how to run it.
- Use safe URL canonicalization for deduplication.
- Keep hard facts already present in the applicant files consistent.
- If a supporting answer is unavailable, make a reasonable assumption based on the applicant profile, resume, and `applicant.md` instead of skipping the job for that reason alone.
- Use profile-based assumptions for missing details such as salary expectations, earliest start date, notice period, and concise free-response summaries.
- Only mark the job incomplete when the site requires a hard fact that is unavailable or cannot be reasonably inferred from the applicant materials.
- Use `blocked` for permanent workflow blockers such as login walls, unsupported flows, closed roles, or disqualifying eligibility requirements.
- Use `failed` only for transient or unverifiable submission outcomes.
- When the outcome is `blocked`, `incomplete`, or `failed`, store one or more structured findings with stage, category, summary, detail, and page URL.
- Log failures clearly.
- Print a final summary with totals.

Execution contract:
- Create a run with `python -m job_apply_bot start-run`.
- Optionally use `python -m job_apply_bot next-job --mark-applying` to drain leftover `ready_to_apply` backlog from interrupted runs before new discovery.
- For each discovered candidate, call `python -m job_apply_bot ingest-job` with the extracted metadata so filtering and dedupe happen in SQLite-backed state.
- Pass `--allow-unverifiable-freshness` to `python -m job_apply_bot ingest-job`.
- If `ingest-job` returns `ready_to_apply`, apply immediately before moving to the next search result.
- After each application attempt, call `python -m job_apply_bot record-application`.
- When the application outcome is `failed`, `incomplete`, or `blocked`, also call `python -m job_apply_bot record-finding`.
- Finish with `python -m job_apply_bot finish-run` and print the returned summary.

Execution behavior:
- First inspect the repository and summarize the implementation plan.
- Then create or update the needed code.
- Then run the workflow if the required environment and tools are available.
- If Playwright MCP or applicant data is missing, explain exactly what is missing and continue as far as possible without faking completion.
