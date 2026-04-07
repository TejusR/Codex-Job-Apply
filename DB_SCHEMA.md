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

## Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at);
CREATE INDEX IF NOT EXISTS idx_applications_job_key ON applications(job_key);
```

## Recommended Status Values

Jobs:
- discovered
- filtered_out_old
- duplicate_skipped
- ready_to_apply
- applying
- applied
- failed
- skipped_unverifiable_date

Notes:
- when `ingest-job` is run with `--allow-unverifiable-freshness`, a job with ambiguous freshness stays `ready_to_apply` and should carry a `status_reason` such as `unverified_freshness_allowed`

Applications:
- submitted
- failed
- incomplete
- duplicate_skipped
```
