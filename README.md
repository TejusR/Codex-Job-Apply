# Job Apply Bot

This project is a Codex + Playwright MCP workflow for finding recent jobs and applying one by one.

## Objective

Automate this loop:

1. Open browser and run Google searches for:
   - `jobright.ai`
   - `site:boards.greenhouse.io ("software engineer" AND "united states")`
   - `site:jobs.ashbyhq.com`
   - force `Past 24 hours`
   - force Google's date-sorted / newest-first view when available
2. Filter jobs posted in the last 24 hours
3. Sort by most recent
4. Keep a SQLite database of jobs already applied to
5. Apply one by one until no jobs remain

## Components

- Codex for orchestration and code generation
- Playwright MCP for browser automation
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
- Skip jobs older than 24 hours
- Sort newest first
- Continue until queue is empty
- Never invent answers on application forms

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

Example:

```bash
APPLICANT_ENABLED_SEARCH_SITES=greenhouse, ashby
```

If the key is omitted, the workflow defaults to all supported sources.

## Support CLI

The repo now includes a small Python CLI for the deterministic workflow steps:

```bash
python -m job_apply_bot validate-profile
python -m job_apply_bot start-run
python -m job_apply_bot ingest-job --run-id 1 --raw-url "https://boards.greenhouse.io/acme/jobs/12345" --title "Software Engineer" --location "Remote, United States" --posted-at "2 hours ago"
python -m job_apply_bot next-job --mark-applying
python -m job_apply_bot record-application --job-key "<job_key>" --status submitted --run-id 1
python -m job_apply_bot finish-run --run-id 1
```

By default the CLI stores SQLite state at `data/job_apply_bot.sqlite3`.

## Future Codex Run

Once `.env` and `applicant.md` are filled in, the normal future instruction is just to reference `PROMPTS/CODEX_MASTER_PROMPT.md` from the repository root. That prompt now tells Codex to load the root applicant files, validate them, use Playwright MCP for browser work, and use the CLI for SQLite tracking and summaries.
