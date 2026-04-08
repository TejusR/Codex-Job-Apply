# SQLite Database Schema

## Table: jobs

```sql
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_key TEXT NOT NULL UNIQUE,
  canonical_url TEXT NOT NULL,
  raw_url TEXT,
  source TEXT,
  title TEXT,
  company TEXT,
  location TEXT,
  posted_at TEXT,
  discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
  status TEXT NOT NULL DEFAULT 'discovered',
  status_reason TEXT,
  last_updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## Table: applications

```sql
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_key TEXT NOT NULL,
  applied_at TEXT NOT NULL DEFAULT (datetime('now')),
  status TEXT NOT NULL,
  confirmation_text TEXT,
  confirmation_url TEXT,
  error_message TEXT,
  FOREIGN KEY (job_key) REFERENCES jobs(job_key)
);
```

## Table: runs

```sql
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  jobs_found INTEGER NOT NULL DEFAULT 0,
  jobs_filtered_in INTEGER NOT NULL DEFAULT 0,
  jobs_skipped_old INTEGER NOT NULL DEFAULT 0,
  jobs_skipped_duplicate INTEGER NOT NULL DEFAULT 0,
  jobs_applied INTEGER NOT NULL DEFAULT 0,
  jobs_failed INTEGER NOT NULL DEFAULT 0,
  notes TEXT
);
```

## Table: application_findings

```sql
CREATE TABLE IF NOT EXISTS application_findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_key TEXT NOT NULL,
  run_id INTEGER NOT NULL,
  application_status TEXT NOT NULL,
  stage TEXT NOT NULL,
  category TEXT NOT NULL,
  summary TEXT NOT NULL,
  detail TEXT,
  page_url TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (job_key) REFERENCES jobs(job_key),
  FOREIGN KEY (run_id) REFERENCES runs(id)
);
```

## Table: run_search_queries

```sql
CREATE TABLE IF NOT EXISTS run_search_queries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  source_key TEXT NOT NULL,
  domain TEXT NOT NULL,
  query_text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  started_at TEXT,
  finished_at TEXT,
  results_seen INTEGER NOT NULL DEFAULT 0,
  jobs_ingested INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  FOREIGN KEY (run_id) REFERENCES runs(id),
  UNIQUE(run_id, source_key)
);
```

## Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at);
CREATE INDEX IF NOT EXISTS idx_applications_job_key ON applications(job_key);
CREATE INDEX IF NOT EXISTS idx_application_findings_run_id ON application_findings(run_id);
CREATE INDEX IF NOT EXISTS idx_application_findings_job_key ON application_findings(job_key);
CREATE INDEX IF NOT EXISTS idx_run_search_queries_run_status ON run_search_queries(run_id, status);
```

## Recommended Status Values

Jobs:
- discovered
- filtered_out_old
- duplicate_skipped
- ready_to_apply
- applying
- applied
- incomplete
- blocked
- failed
- skipped_unverifiable_date

Notes:
- when `ingest-job` is run with `--allow-unverifiable-freshness`, a job with ambiguous freshness stays `ready_to_apply` and should carry a `status_reason` such as `unverified_freshness_allowed`
- `failed` is retryable only when the same job is rediscovered in a later run
- `applied`, `duplicate_skipped`, `incomplete`, `blocked`, and `applying` are terminal for duplicate checks
- `prepare-run` requeues stale `applying` jobs to `ready_to_apply` with `status_reason=requeued_from_interrupted_run`

Applications:
- submitted
- failed
- incomplete
- blocked
- duplicate_skipped

Findings:
- use `application_findings` for structured blocker / failure capture instead of relying only on `applications.error_message`
- `finish-run` should summarize findings by category and include the latest findings for blocked, incomplete, and failed jobs

Run Search Queries:
- pending
- in_progress
- completed
- failed

Run Notes:
- `seen_job_keys` tracks same-run dedupe during discovery
- `requeued_jobs_count` tracks how many stale `applying` jobs were moved back to `ready_to_apply` during `prepare-run`

Run Completion:
- `workflow-status` is drained only when `ready_to_apply=0`, `applying=0`, `queries_pending=0`, and `queries_in_progress=0`
- `finish-run --run-id <id>` refuses unresolved work unless `--force` is supplied
- `finish-run` includes a `search_summary` with total/completed/failed/pending/in-progress query counts and `requeued_jobs_count`
