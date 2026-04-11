# Codex Master Prompt

Read all markdown files in this repository and execute the job-application workflow they describe.

Use the existing implementation when present. Only create or modify code if the current repository state is missing a required capability or needs a minimal fix before the workflow can continue safely.

Before doing browser work:
- Read all markdown files in this repository.
- Load the root-level `.env` and `applicant.md`.
- Use `resume/Tejus Resume_SDE_V2.pdf` as the resume source.
- Run `python -m job_apply_bot validate-profile`.
- Then run `python -m job_apply_bot prepare-run` and use the returned run-scoped queries for Google discovery.
- If any required applicant fields, files, or Playwright MCP tools are missing, list the exact missing items and continue only as far as possible without faking completion.
- If `.env` includes `APPLICANT_ENABLED_SEARCH_SITES`, only use the enabled sources during search.

Requirements:
- Use the markdown files in this repo as the source of truth.
- Build a local workflow that uses Playwright MCP for all browser automation, including manual-solve CAPTCHA recovery in the same browser session.
- Use SQLite to track discovered jobs, application outcomes, and structured workflow findings.
- Build Google searches from the exact phrases in `APPLICANT_TARGET_ROLE_KEYWORDS` and the enabled source domains defined in `SEARCH_SPEC.md`.
- Use the query shape `site:<domain> ("role 1" OR "role 2" ...) ("united states" OR "remote")`.
- Supported source keys are `jobright`, `greenhouse`, `ashby`, `workable`, `jobvite`, `jazz`, `adp`, `lever`, `bamboohr`, `paylocity`, `smartrecruiters`, `gem`, and `dover`.
- For each Google query, force the `Past 24 hours` filter and Google's date-sorted / most-recent view when available.
- Search exhaustively: continue through reachable Google result pages and relevant listing pages until no new candidates remain for each enabled source.
- If Google shows a CAPTCHA or anti-bot interstitial, keep the same Playwright page open, wait up to 10 minutes for manual solve, and poll the browser state every 10 seconds. Continue only when the normal SERP is back; otherwise fail that query.
- Keep checking `python -m job_apply_bot workflow-status --run-id <id>` and do not stop until all seeded queries are terminal and there are no `ready_to_apply` or `applying` jobs left.
- Process each discovered candidate immediately in the order it appears for the current query instead of batching all sources first.
- If a result is a listing page rather than a direct job page, extract its child job links and process those child jobs one by one before returning to the search results.
- Filter to jobs posted within the last 24 hours when freshness can be verified.
- If freshness still cannot be verified after reasonable extraction attempts, keep the job eligible for application and record that freshness was unverified.
- Skip any job with a prior terminal application outcome, but allow prior `failed` jobs to be retried only if they are rediscovered in a later run.
- Apply to each remaining job one by one until no jobs are left.
- Start each application flow in Playwright.
- If Playwright encounters a visible CAPTCHA, challenge page, reCAPTCHA iframe, or a failure clearly attributable to an anti-bot challenge for the current job, keep that same Playwright page open, wait up to 10 minutes for manual solve, and poll the browser state every 10 seconds before deciding the outcome.
- Continue the current application only after challenge markers disappear and the normal application UI is back.
- Record all results in SQLite.

Implementation constraints:
- Organize the code cleanly.
- Add a README section describing how to run it.
- Use safe URL canonicalization for deduplication.
- Keep hard facts already present in the applicant files consistent.
- If a supporting answer is unavailable, make a reasonable assumption based on the applicant profile, resume, and `applicant.md` instead of skipping the job for that reason alone.
- Use profile-based assumptions for missing details such as salary expectations, earliest start date, notice period, and concise free-response summaries.
- Only mark the job incomplete when the site requires a hard fact that is unavailable or cannot be reasonably inferred from the applicant materials.
- Use `blocked` for permanent workflow blockers such as login walls, unsupported flows, closed roles, disqualifying eligibility requirements, or CAPTCHA challenges that still block submission after the 10-minute Playwright wait window.
- Use `failed` only for transient or unverifiable submission outcomes.
- When the outcome is `blocked`, `incomplete`, or `failed`, store one or more structured findings with stage, category, summary, detail, and page URL.
- When a CAPTCHA remains unresolved after the 10-minute Playwright wait window, store a structured finding with category `captcha`.
- Log failures clearly.
- Print a final summary with totals.

Execution contract:
- Create and seed the run with `python -m job_apply_bot prepare-run`.
- Use `python -m job_apply_bot workflow-status --run-id <id>` as the completion gate for the whole run.
- Drain leftover `ready_to_apply` backlog with `python -m job_apply_bot next-job --mark-applying` before claiming a new query.
- Claim one pending query at a time with `python -m job_apply_bot next-query --run-id <id>`.
- For each discovered candidate, call `python -m job_apply_bot ingest-job` with the extracted metadata so filtering and dedupe happen in SQLite-backed state.
- Pass `--allow-unverifiable-freshness` to `python -m job_apply_bot ingest-job`.
- If `ingest-job` returns `ready_to_apply`, apply immediately before moving to the next search result.
- When a claimed query is exhausted, call `python -m job_apply_bot complete-query`.
- If a query-level failure prevents finishing that query, call `python -m job_apply_bot fail-query` with the error message and continue the remaining queries.
- Use Playwright MCP for search, extraction, and application flow steps.
- If Google or a specific job hits a CAPTCHA, keep that same Playwright session open, poll the browser state every 10 seconds for up to 10 minutes, and continue only after the challenge clears.
- After each application attempt, call `python -m job_apply_bot record-application`.
- When the application outcome is `failed`, `incomplete`, or `blocked`, also call `python -m job_apply_bot record-finding`.
- If the 10-minute Playwright wait expires and the job is still blocked by CAPTCHA, record the job as `blocked` and include a `record-finding` entry with category `captcha`.
- Finish with `python -m job_apply_bot finish-run` only after `workflow-status` reports `drained=true`, unless intentionally using `--force`, and print the returned summary.

Execution behavior:
- First inspect the repository and summarize the implementation plan.
- Then create or update the needed code.
- Then run the workflow if the required environment and tools are available.
- If Playwright MCP or applicant data is missing, explain exactly what is missing and continue as far as possible without faking completion.
