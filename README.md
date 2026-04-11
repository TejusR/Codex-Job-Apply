# Job Apply Bot

This project is a supervised Codex workflow for finding recent jobs and applying one by one with Playwright MCP as the sole browser engine, including manual-solve CAPTCHA recovery in the same Playwright session.

## Objective

Automate this loop:

1. Prepare a run in SQLite, seed every enabled Google query, and requeue any stale `applying` backlog from an interrupted session
2. Use run-scoped query state plus `workflow-status` to keep looping until every query is terminal and no `ready_to_apply` / `applying` jobs remain
3. Open a browser and run generated Google searches for each enabled source
   - build each query from `APPLICANT_TARGET_ROLE_KEYWORDS` and the source domain registry in `SEARCH_SPEC.md`
   - use the query shape `site:<domain> ("role 1" OR "role 2" ...) ("united states" OR "remote")`
   - the current `.env` role list is `software engineer`, `backend engineer`, `full stack engineer`, `software developer`
   - force `Past 24 hours`
   - force Google's date-sorted / newest-first view when available
   - paginate through up to the configured discovery page cap for Google result pages and relevant listing pages
4. Open discovered job links immediately, or expand listing pages into child job links and process those one by one before returning to search
5. Filter jobs posted in the last 24 hours when freshness can be verified
6. Keep jobs with unverified freshness eligible for application, while recording that the freshness could not be verified
7. Keep a SQLite database of jobs, application outcomes, structured workflow findings, and per-run query progress
8. Keep each discovery/application step in Playwright, wait up to 10 minutes for manual CAPTCHA solve in that same browser session, and poll browser state until the page becomes usable again or the wait window expires
9. Apply one by one in discovery order until no jobs remain

## Components

- Codex for orchestration and code generation
- Playwright MCP for search, extraction, application flow, and manual CAPTCHA recovery in the same browser session
- SQLite for persistence and deduplication

## Files

- `WORKFLOW.md`: end-to-end workflow
- `SEARCH_SPEC.md`: search queries and filtering rules
- `DB_SCHEMA.md`: SQLite schema
- `APPLICATION_RULES.md`: application logic and safety constraints
- `MCP_SETUP.md`: MCP integration notes
- `RUN_WITH_CODEX.md`: exactly how to ask Codex to execute the workflow
- `PROMPTS/CODEX_QUERY_WORKER_PROMPT.md` / `PROMPTS/CODEX_APPLY_WORKER_PROMPT.md`: bounded worker prompts used by the supervised runner
- `PROMPTS/CODEX_QUERY_WORKER_SCHEMA.json` / `PROMPTS/CODEX_APPLY_WORKER_SCHEMA.json`: machine-readable output contracts for those workers
- `.env` / `.env.example`: root-level applicant fields used for forms and validation
- `applicant.md` / `applicant.md.example`: root-level truthful free-form context for questions that do not fit neatly in env vars
- `job_apply_bot/`: local CLI helpers for SQLite state, filtering, dedupe, and profile validation

## Principles

- Never apply twice to the same job
- Record every attempt
- Record structured findings for blocked, incomplete, and failed attempts
- Skip jobs older than 24 hours
- Do not skip a job solely because freshness could not be verified
- Process newly discovered jobs immediately instead of batching all sources first
- Continue until queue is empty
- Handle CAPTCHA and anti-bot pages in the same Playwright session with a 10-minute manual-solve wait and continuous browser-state polling
- Keep hard facts consistent with the applicant files
- Use reasonable profile-based assumptions for missing supporting application answers instead of skipping a job solely for that reason
- Only `failed` outcomes are retryable, and only when the job is rediscovered in a future run
- Persist `run_search_queries.results_seen` and `jobs_ingested` after each processed discovery result so same-run resume is accurate

## Browser Strategy

- Use Playwright MCP for Google search, listing traversal, metadata extraction, and application flows.
- If Playwright encounters a visible CAPTCHA, reCAPTCHA iframe, challenge page, or a failure clearly caused by anti-bot protection for the current search/application step, keep that same Playwright page open for manual solve.
- Poll browser state every 10 seconds for up to 10 minutes, and continue only after challenge markers disappear and the expected page content is visible again.
- If the 10-minute window expires first, record the discovery step as `query_failed` or the application as `blocked`, depending on which workflow step was affected.

## Applicant Inputs

Keep the real applicant files in the repository root:

- `.env`: structured applicant fields, document paths, work authorization, sponsorship, and search preferences
- `applicant.md`: additional truthful details and reusable notes for application questions

Committed templates are provided as `.env.example` and `applicant.md.example`.

### Search Site Toggles

Use `APPLICANT_ENABLED_SEARCH_SITES` in `.env` to choose which search sources are active for discovery.

Supported values:
- `jobright`
- `greenhouse`
- `ashby`
- `workable`
- `jobvite`
- `jazz`
- `adp`
- `lever`
- `bamboohr`
- `paylocity`
- `smartrecruiters`
- `gem`
- `dover`

Example:

```bash
APPLICANT_ENABLED_SEARCH_SITES=greenhouse, ashby, workable, jobvite, jazz, adp, lever, bamboohr, paylocity, smartrecruiters, gem, dover
```

If the key is omitted, the workflow defaults to all supported sources.

### Discovery Page Cap

Use `APPLICANT_DISCOVERY_MAX_PAGES` in `.env` to cap discovery pagination for both Google result pages and job-board listing pages.

Example:

```bash
APPLICANT_DISCOVERY_MAX_PAGES=5
```

If the key is omitted or invalid, the workflow defaults to `5`.

## Support CLI

The repo now includes a Python CLI for the deterministic workflow steps plus a supervised Codex runner:

```bash
python -m job_apply_bot validate-profile
python -m job_apply_bot prepare-run
python -m job_apply_bot claim-query --run-id 1
python -m job_apply_bot discover-next-candidate-with-codex --run-id 1 --source-key greenhouse
python -m job_apply_bot apply-job-with-codex --run-id 1 --job-key "<job_key>"
python -m job_apply_bot run-workflow
python -m job_apply_bot requeue-runner-failures --run-id 1
python -m job_apply_bot workflow-status --run-id 1
python -m job_apply_bot next-query --run-id 1
python -m job_apply_bot complete-query --run-id 1 --source-key greenhouse --results-seen 20 --jobs-ingested 4
python -m job_apply_bot fail-query --run-id 1 --source-key ashby --message "Google rate limited the query"
python -m job_apply_bot ingest-job --run-id 1 --raw-url "https://boards.greenhouse.io/acme/jobs/12345" --title "Software Engineer" --location "Remote, United States" --posted-at "New" --allow-unverifiable-freshness
python -m job_apply_bot next-job --mark-applying
python -m job_apply_bot record-application --job-key "<job_key>" --status submitted --run-id 1
python -m job_apply_bot record-finding --job-key "<job_key>" --run-id 1 --application-status failed --stage submit --category confirmation_missing --summary "No confirmation page appeared"
python -m job_apply_bot finish-run --run-id 1
python -m unittest discover -s tests
```

By default the CLI stores SQLite state at `data/job_apply_bot.sqlite3`.
`prepare-run` validates the profile, creates the run, seeds run-scoped query rows, and requeues stale `applying` jobs back to `ready_to_apply`.
`workflow-status` is the completion gate: the workflow is done only when it reports `drained=true`.
`next-job` remains available for draining backlog in SQLite order; new discovery should otherwise ingest and apply each candidate immediately.
`finish-run` now refuses unresolved work unless `--force` is supplied.
`validate-profile` still emits `google_search_queries`, which are generated from the current `.env` role keywords and enabled search sites, along with the resolved discovery page cap in the emitted profile payload.
`run-workflow` is the primary entrypoint. It owns the outer loop in Python and launches short-lived `codex exec` workers for one discovery step or one job application attempt at a time.
Those workers run in Codex's non-interactive bypass mode so Playwright MCP tools remain usable from spawned sessions.
If a search or application CAPTCHA appears, a worker keeps the same Playwright session open, polls browser state every 10 seconds for up to 10 minutes, and then either continues or returns the appropriate terminal outcome.
Any unsuccessful apply attempt now leaves a raw local failure bundle under `data/codex_worker_artifacts/run-<id>/apply/`. These bundles may contain PII-filled form data, screenshots, HTML, and browser logs.
If an apply attempt fails because of an internal worker problem such as `codex_worker_error`, use `requeue-runner-failures` to put those jobs back into `ready_to_apply` within the same run after you deploy the fix.

## Future Codex Run

Once `.env` and `applicant.md` are filled in, the normal entrypoint is:

```bash
python -m job_apply_bot run-workflow
```

This keeps the workflow lifecycle in Python while still using Codex for bounded browser work.

`PROMPTS/CODEX_MASTER_PROMPT.md` remains available as a legacy/manual fallback when you explicitly want one long Codex-run conversation, but it is no longer the recommended primary entrypoint.
