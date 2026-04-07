# Job Apply Bot

This project is a Codex workflow for finding recent jobs and applying one by one with Playwright MCP as the default browser engine and `@camoufox-browser` as a per-job CAPTCHA fallback.

## Objective

Automate this loop:

1. Start a run in SQLite and optionally drain any leftover `ready_to_apply` backlog from an interrupted session
2. Open a browser and run generated Google searches for each enabled source
   - build each query from `APPLICANT_TARGET_ROLE_KEYWORDS` and the source domain registry in `SEARCH_SPEC.md`
   - use the query shape `site:<domain> ("role 1" OR "role 2" ...) ("united states" OR "remote")`
   - the current `.env` role list is `software engineer`, `backend engineer`, `full stack engineer`, `software developer`
   - force `Past 24 hours`
   - force Google's date-sorted / newest-first view when available
   - paginate exhaustively through reachable result pages and relevant listing pages
3. Open discovered job links immediately, or expand listing pages into child job links and process those one by one before returning to search
4. Filter jobs posted in the last 24 hours when freshness can be verified
5. Keep jobs with unverified freshness eligible for application, while recording that the freshness could not be verified
6. Keep a SQLite database of jobs, application outcomes, and structured workflow findings
7. Start each application in Playwright, switch only the affected job to `@camoufox-browser` if a CAPTCHA blocks Playwright, then return to Playwright for the rest of the run
8. Apply one by one in discovery order until no jobs remain

## Components

- Codex for orchestration and code generation
- Playwright MCP for search, extraction, and the default application flow
- `@camoufox-browser` for CAPTCHA or anti-bot fallback on a single blocked job
- SQLite for persistence and deduplication

## Files

- `WORKFLOW.md`: end-to-end workflow
- `SEARCH_SPEC.md`: search queries and filtering rules
- `DB_SCHEMA.md`: SQLite schema
- `APPLICATION_RULES.md`: application logic and safety constraints
- `MCP_SETUP.md`: MCP integration notes
- `RUN_WITH_CODEX.md`: exactly how to ask Codex to execute the workflow
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
- Start in Playwright and switch to Camoufox only when a specific job hits a CAPTCHA or anti-bot challenge
- Keep hard facts consistent with the applicant files
- Use reasonable profile-based assumptions for missing supporting application answers instead of skipping a job solely for that reason
- Only `failed` outcomes are retryable, and only when the job is rediscovered in a future run

## Browser Strategy

- Use Playwright MCP for Google search, listing traversal, metadata extraction, and normal application flows.
- If Playwright encounters a visible CAPTCHA, reCAPTCHA iframe, challenge page, or a failure clearly caused by anti-bot protection for the current job, reopen that same job in `@camoufox-browser`.
- Keep the Camoufox fallback scoped to that one affected job, then return to Playwright for the rest of the run.
- If Camoufox also cannot get past the challenge, record the application as `blocked` and store a structured finding with category `captcha`.

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

## Support CLI

The repo now includes a small Python CLI for the deterministic workflow steps:

```bash
python -m job_apply_bot validate-profile
python -m job_apply_bot start-run
python -m job_apply_bot ingest-job --run-id 1 --raw-url "https://boards.greenhouse.io/acme/jobs/12345" --title "Software Engineer" --location "Remote, United States" --posted-at "New" --allow-unverifiable-freshness
python -m job_apply_bot next-job --mark-applying
python -m job_apply_bot record-application --job-key "<job_key>" --status submitted --run-id 1
python -m job_apply_bot record-finding --job-key "<job_key>" --run-id 1 --application-status failed --stage submit --category confirmation_missing --summary "No confirmation page appeared"
python -m job_apply_bot finish-run --run-id 1
```

By default the CLI stores SQLite state at `data/job_apply_bot.sqlite3`.
`next-job` remains available for draining leftover backlog from interrupted runs; new discovery should otherwise ingest and apply each candidate immediately.
`validate-profile` also emits `google_search_queries`, which are generated from the current `.env` role keywords and enabled search sites.

## Future Codex Run

Once `.env` and `applicant.md` are filled in, the normal future instruction is just to reference `PROMPTS/CODEX_MASTER_PROMPT.md` from the repository root. That prompt now tells Codex to load the root applicant files, validate them, use Playwright MCP by default, switch to `@camoufox-browser` only when a specific job hits CAPTCHA protection, and use the CLI for SQLite tracking and summaries.
