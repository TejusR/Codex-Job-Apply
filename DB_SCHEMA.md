# SQLite Database Schema

The Go backend preserves the existing SQLite database at
`data/job_apply_bot.sqlite3`. Schema initialization is idempotent and never
recreates or drops user tables. Legacy columns are added before indexes that
reference them.

## Tables

- `jobs`: normalized jobs, source metadata, description text, current workflow status, and status reason
- `applications`: every application attempt, run association, confirmation data, and resume snapshot/customization
- `runs`: workflow counters, lifecycle timestamps, and JSON notes (`seen_job_keys`, `requeued_jobs_count`)
- `application_findings`: structured blocked, incomplete, and failed-application findings
- `run_search_queries`: per-run board query state, progress counters, cursor JSON, and terminal error
- `run_search_results`: Google/listing results, parent-child expansion, claims, and terminal resolution status
- `run_query_skipped_results`: deduplicated query results skipped before ingestion
- `codex_worker_sessions`: reusable Codex thread IDs and live worker status by logical slot
- `codex_worker_attempts`: invocation attempts, exit state, result path, and log path
- `resume_customizations`: tailored resume source, payload, rendered files, compiler, and failure state

The complete executable DDL and index definitions live in
`internal/store/store.go` so runtime migration behavior and documentation cannot
silently diverge.

## Status Contracts

Jobs use `discovered`, `filtered_out_old`, `duplicate_skipped`,
`ready_to_apply`, `applying`, `applied`, `incomplete`, `blocked`, `failed`, and
`skipped_unverifiable_date`.

Applications use `submitted`, `failed`, `incomplete`, `blocked`, and
`duplicate_skipped`. Only `failed` applications may become eligible again when
rediscovered in a later run.

Queries use `pending`, `in_progress`, `completed`, and `failed`. Search results
add `processing` plus terminal resolution/application states such as `expanded`,
`filtered_out`, `ingested`, `applied`, and `duplicate_skipped`.

## Compatibility Migrations

On every open, the store:

1. Creates missing tables.
2. Adds legacy `applications.run_id`, `applications.resume_customization_id`,
   `applications.resume_path_used`, `applications.resume_label_used`,
   `jobs.description_text`, and `run_search_queries.cursor_json` columns.
3. Creates all indexes only after those columns exist.

This ordering allows databases created by older Python releases to open without
data loss. The backend uses a SQLite busy timeout and short transactions for
cross-process dashboard/workflow concurrency.

## Completion Rules

`workflow-status` reports `drained=true` only when no ready/applying jobs,
pending/in-progress queries, or pending/processing search results remain.
`finish-run` rejects unresolved work unless `--force` is supplied.
