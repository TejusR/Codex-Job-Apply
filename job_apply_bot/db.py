from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3

from .jobs import (
    FreshnessCheck,
    build_job_key,
    canonicalize_url,
    evaluate_posted_at,
    format_timestamp,
    infer_source,
    location_matches_us,
    title_matches_role,
    utc_now,
)
from .profile import validate_profile

TERMINAL_APPLICATION_STATUSES = (
    "submitted",
    "incomplete",
    "duplicate_skipped",
    "blocked",
)
FINDING_APPLICATION_STATUSES = ("failed", "incomplete", "blocked")
TERMINAL_JOB_STATUSES = ("applied", "duplicate_skipped", "blocked", "incomplete", "applying")

SCHEMA_STATEMENTS = (
    """
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
    """,
    """
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
    """,
    """
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
    """,
    """
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
    """,
    """
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
    """,
    """
    CREATE TABLE IF NOT EXISTS codex_worker_attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id INTEGER NOT NULL,
      worker_type TEXT NOT NULL,
      target_key TEXT NOT NULL,
      attempt_number INTEGER NOT NULL,
      status TEXT NOT NULL,
      exit_code INTEGER,
      error_message TEXT,
      started_at TEXT NOT NULL,
      finished_at TEXT,
      result_path TEXT,
      log_path TEXT,
      FOREIGN KEY (run_id) REFERENCES runs(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS run_query_skipped_results (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id INTEGER NOT NULL,
      source_key TEXT NOT NULL,
      url TEXT NOT NULL,
      reason TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY (run_id) REFERENCES runs(id),
      UNIQUE(run_id, source_key, url)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at);",
    "CREATE INDEX IF NOT EXISTS idx_applications_job_key ON applications(job_key);",
    "CREATE INDEX IF NOT EXISTS idx_application_findings_run_id ON application_findings(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_application_findings_job_key ON application_findings(job_key);",
    "CREATE INDEX IF NOT EXISTS idx_run_search_queries_run_status ON run_search_queries(run_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_codex_worker_attempts_run_target ON codex_worker_attempts(run_id, worker_type, target_key);",
    "CREATE INDEX IF NOT EXISTS idx_run_query_skipped_results_run_source ON run_query_skipped_results(run_id, source_key);",
)


@dataclass(slots=True)
class IngestResult:
    action: str
    job_key: str
    canonical_url: str
    status: str
    status_reason: str | None
    source: str
    freshness: FreshnessCheck

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "job_key": self.job_key,
            "canonical_url": self.canonical_url,
            "status": self.status,
            "status_reason": self.status_reason,
            "source": self.source,
            "freshness": {
                "raw_value": self.freshness.raw_value,
                "is_recent": self.freshness.is_recent,
                "is_verifiable": self.freshness.is_verifiable,
                "normalized_posted_at": self.freshness.normalized_posted_at,
                "reason": self.freshness.reason,
            },
        }


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    initialize_database(connection)
    return connection


@contextmanager
def managed_connection(db_path: Path) -> sqlite3.Connection:
    connection = connect(db_path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize_database(connection: sqlite3.Connection) -> None:
    with connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


def start_run(db_path: Path) -> dict[str, object]:
    with managed_connection(db_path) as connection:
        run_id = _create_run(connection)
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return {"id": run_id}
    payload = dict(row)
    payload["notes"] = _load_notes(payload.get("notes"))
    return payload


def prepare_run(db_path: Path, repo_root: Path) -> dict[str, object]:
    validation = validate_profile(repo_root.resolve())
    if not validation.ok:
        missing_items = validation.missing_required_fields + validation.missing_required_files
        raise ValueError(
            "Profile validation failed. Missing required items: "
            + ", ".join(missing_items)
        )

    query_specs = validation.to_dict()["google_search_queries"]
    if not isinstance(query_specs, list):
        raise ValueError("Profile validation did not return google_search_queries.")

    with managed_connection(db_path) as connection:
        run_id = _create_run(connection)
        requeued_jobs_count = _requeue_stale_applying_jobs(connection)
        run_notes = _get_run_notes(connection, run_id)
        run_notes["requeued_jobs_count"] = requeued_jobs_count
        _set_run_notes(connection, run_id, run_notes)
        _seed_run_search_queries(connection, run_id, query_specs)
        query_rows = connection.execute(
            """
            SELECT id, run_id, source_key, domain, query_text, status
            FROM run_search_queries
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()

    return {
        "run_id": run_id,
        "queries": [dict(row) for row in query_rows],
        "requeued_jobs_count": requeued_jobs_count,
        "warnings": validation.warnings,
    }


def finish_run(db_path: Path, run_id: int, *, force: bool = False) -> dict[str, object]:
    finished_at = format_timestamp(utc_now())
    with managed_connection(db_path) as connection:
        if not force:
            _assert_run_can_finish(connection, run_id)
        connection.execute(
            "UPDATE runs SET finished_at = COALESCE(finished_at, ?) WHERE id = ?",
            (finished_at, run_id),
        )
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        findings_summary = _build_findings_summary(connection, run_id)
        notes = _load_notes(row["notes"] if row is not None else None)
        search_summary = _build_search_summary(connection, run_id, notes)
    if row is None:
        raise ValueError(f"Run {run_id} does not exist.")
    summary = dict(row)
    summary["notes"] = notes
    summary["findings_summary"] = findings_summary
    summary["search_summary"] = search_summary
    return summary


def ingest_job(
    db_path: Path,
    *,
    run_id: int,
    raw_url: str,
    canonical_url: str | None,
    source: str | None,
    title: str | None,
    company: str | None,
    location: str | None,
    posted_at: str | None,
    discovered_at: str | None,
    role_keywords: list[str] | None,
    allowed_locations: list[str] | None,
    allow_unverifiable_freshness: bool = False,
) -> IngestResult:
    canonical = canonicalize_url(raw_url, canonical_url)
    job_key = build_job_key(canonical)
    source_name = source or infer_source(canonical)
    freshness = evaluate_posted_at(posted_at)
    discovered_value = discovered_at or format_timestamp(utc_now())

    with managed_connection(db_path) as connection:
        _increment_run_counter(connection, run_id, "jobs_found")
        run_notes = _get_run_notes(connection, run_id)
        seen_job_keys = run_notes.get("seen_job_keys")
        if not isinstance(seen_job_keys, list):
            seen_job_keys = []
            run_notes["seen_job_keys"] = seen_job_keys
        if job_key in seen_job_keys:
            _increment_run_counter(connection, run_id, "jobs_skipped_duplicate")
            return IngestResult(
                action="duplicate_same_run",
                job_key=job_key,
                canonical_url=canonical,
                status="duplicate_skipped",
                status_reason="job_already_seen_in_current_run",
                source=source_name,
                freshness=freshness,
            )
        seen_job_keys.append(job_key)
        _set_run_notes(connection, run_id, run_notes)

        duplicate_reason = _find_attempted_duplicate(connection, job_key, canonical)
        if duplicate_reason is not None:
            _increment_run_counter(connection, run_id, "jobs_skipped_duplicate")
            return IngestResult(
                action="duplicate_existing_attempt",
                job_key=job_key,
                canonical_url=canonical,
                status="duplicate_skipped",
                status_reason=duplicate_reason,
                source=source_name,
                freshness=freshness,
            )

        if not freshness.is_verifiable and not allow_unverifiable_freshness:
            _upsert_job(
                connection,
                job_key=job_key,
                canonical_url=canonical,
                raw_url=raw_url,
                source=source_name,
                title=title,
                company=company,
                location=location,
                posted_at=freshness.normalized_posted_at,
                discovered_at=discovered_value,
                status="skipped_unverifiable_date",
                status_reason=freshness.reason,
            )
            return IngestResult(
                action="skipped_unverifiable_date",
                job_key=job_key,
                canonical_url=canonical,
                status="skipped_unverifiable_date",
                status_reason=freshness.reason,
                source=source_name,
                freshness=freshness,
            )

        if freshness.is_verifiable and not freshness.is_recent:
            _upsert_job(
                connection,
                job_key=job_key,
                canonical_url=canonical,
                raw_url=raw_url,
                source=source_name,
                title=title,
                company=company,
                location=location,
                posted_at=freshness.normalized_posted_at,
                discovered_at=discovered_value,
                status="filtered_out_old",
                status_reason=freshness.reason,
            )
            _increment_run_counter(connection, run_id, "jobs_skipped_old")
            return IngestResult(
                action="filtered_out_old",
                job_key=job_key,
                canonical_url=canonical,
                status="filtered_out_old",
                status_reason=freshness.reason,
                source=source_name,
                freshness=freshness,
            )

        if not title_matches_role(title, role_keywords):
            _upsert_job(
                connection,
                job_key=job_key,
                canonical_url=canonical,
                raw_url=raw_url,
                source=source_name,
                title=title,
                company=company,
                location=location,
                posted_at=freshness.normalized_posted_at,
                discovered_at=discovered_value,
                status="discovered",
                status_reason="filtered_out_role",
            )
            return IngestResult(
                action="filtered_out_role",
                job_key=job_key,
                canonical_url=canonical,
                status="discovered",
                status_reason="filtered_out_role",
                source=source_name,
                freshness=freshness,
            )

        if not location_matches_us(location, allowed_locations):
            _upsert_job(
                connection,
                job_key=job_key,
                canonical_url=canonical,
                raw_url=raw_url,
                source=source_name,
                title=title,
                company=company,
                location=location,
                posted_at=freshness.normalized_posted_at,
                discovered_at=discovered_value,
                status="discovered",
                status_reason="filtered_out_location",
            )
            return IngestResult(
                action="filtered_out_location",
                job_key=job_key,
                canonical_url=canonical,
                status="discovered",
                status_reason="filtered_out_location",
                source=source_name,
                freshness=freshness,
            )

        ready_status_reason = (
            "unverified_freshness_allowed"
            if not freshness.is_verifiable and allow_unverifiable_freshness
            else None
        )
        _upsert_job(
            connection,
            job_key=job_key,
            canonical_url=canonical,
            raw_url=raw_url,
            source=source_name,
            title=title,
            company=company,
            location=location,
            posted_at=freshness.normalized_posted_at,
            discovered_at=discovered_value,
            status="ready_to_apply",
            status_reason=ready_status_reason,
        )
        _increment_run_counter(connection, run_id, "jobs_filtered_in")
        return IngestResult(
            action=(
                "ready_to_apply_unverifiable_date"
                if ready_status_reason is not None
                else "ready_to_apply"
            ),
            job_key=job_key,
            canonical_url=canonical,
            status="ready_to_apply",
            status_reason=ready_status_reason,
            source=source_name,
            freshness=freshness,
        )


def next_job(db_path: Path, *, mark_applying: bool = False) -> dict[str, object] | None:
    with managed_connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE status = 'ready_to_apply'
            ORDER BY posted_at DESC, discovered_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None

        if mark_applying:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'applying',
                    status_reason = NULL,
                    last_updated_at = ?
                WHERE job_key = ?
                """,
                (format_timestamp(utc_now()), row["job_key"]),
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_key = ?", (row["job_key"],)
            ).fetchone()
    return dict(row) if row is not None else None


def requeue_stale_applying_jobs(db_path: Path, *, run_id: int | None = None) -> int:
    with managed_connection(db_path) as connection:
        requeued_jobs_count = _requeue_stale_applying_jobs(connection)
        if run_id is not None:
            run_notes = _get_run_notes(connection, run_id)
            prior_value = run_notes.get("requeued_jobs_count", 0)
            if not isinstance(prior_value, int):
                prior_value = 0
            run_notes["requeued_jobs_count"] = prior_value + requeued_jobs_count
            _set_run_notes(connection, run_id, run_notes)
    return requeued_jobs_count


def requeue_runner_failures(db_path: Path, *, run_id: int) -> dict[str, object]:
    with managed_connection(db_path) as connection:
        run = connection.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"Run {run_id} does not exist.")

        rows = connection.execute(
            """
            WITH latest_failed_applications AS (
                SELECT applications.job_key, applications.status
                FROM applications
                JOIN (
                    SELECT job_key, MAX(id) AS max_id
                    FROM applications
                    GROUP BY job_key
                ) latest_applications
                  ON latest_applications.max_id = applications.id
            ),
            latest_run_findings AS (
                SELECT application_findings.job_key,
                       application_findings.category,
                       application_findings.application_status
                FROM application_findings
                JOIN (
                    SELECT job_key, MAX(id) AS max_id
                    FROM application_findings
                    WHERE run_id = ?
                    GROUP BY job_key
                ) latest_findings
                  ON latest_findings.max_id = application_findings.id
            )
            SELECT jobs.job_key
            FROM jobs
            JOIN latest_failed_applications
              ON latest_failed_applications.job_key = jobs.job_key
            JOIN latest_run_findings
              ON latest_run_findings.job_key = jobs.job_key
            WHERE latest_failed_applications.status = 'failed'
              AND latest_run_findings.application_status = 'failed'
              AND latest_run_findings.category = 'codex_worker_error'
            ORDER BY jobs.last_updated_at DESC, jobs.job_key ASC
            """,
            (run_id,),
        ).fetchall()
        job_keys = [str(row["job_key"]) for row in rows]

        if job_keys:
            now = format_timestamp(utc_now())
            placeholders = ", ".join("?" for _ in job_keys)
            connection.execute(
                f"""
                UPDATE jobs
                SET status = 'ready_to_apply',
                    status_reason = 'requeued_runner_failure',
                    last_updated_at = ?
                WHERE job_key IN ({placeholders})
                """,
                (now, *job_keys),
            )

    return {
        "run_id": run_id,
        "count": len(job_keys),
        "job_keys": job_keys,
    }


def next_query(db_path: Path, *, run_id: int) -> dict[str, object] | None:
    with managed_connection(db_path) as connection:
        run = connection.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"Run {run_id} does not exist.")

        row = connection.execute(
            """
            SELECT *
            FROM run_search_queries
            WHERE run_id = ? AND status = 'pending'
            ORDER BY id ASC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None

        started_at = format_timestamp(utc_now())
        connection.execute(
            """
            UPDATE run_search_queries
            SET status = 'in_progress',
                started_at = COALESCE(started_at, ?),
                last_error = NULL
            WHERE id = ?
            """,
            (started_at, row["id"]),
        )
        row = connection.execute(
            """
            SELECT *
            FROM run_search_queries
            WHERE id = ?
            """,
            (row["id"],),
        ).fetchone()
    return dict(row) if row is not None else None


def claim_query(db_path: Path, *, run_id: int) -> dict[str, object] | None:
    with managed_connection(db_path) as connection:
        run = connection.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"Run {run_id} does not exist.")

        row = connection.execute(
            """
            SELECT *
            FROM run_search_queries
            WHERE run_id = ? AND status = 'in_progress'
            ORDER BY started_at ASC, id ASC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is not None:
            return dict(row)

        row = connection.execute(
            """
            SELECT *
            FROM run_search_queries
            WHERE run_id = ? AND status = 'pending'
            ORDER BY id ASC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None

        started_at = format_timestamp(utc_now())
        connection.execute(
            """
            UPDATE run_search_queries
            SET status = 'in_progress',
                started_at = COALESCE(started_at, ?),
                last_error = NULL
            WHERE id = ?
            """,
            (started_at, row["id"]),
        )
        updated = connection.execute(
            """
            SELECT *
            FROM run_search_queries
            WHERE id = ?
            """,
            (row["id"],),
        ).fetchone()
    return dict(updated) if updated is not None else None


def complete_query(
    db_path: Path,
    *,
    run_id: int,
    source_key: str,
    results_seen: int | None = None,
    jobs_ingested: int | None = None,
) -> dict[str, object]:
    return _update_query_status(
        db_path,
        run_id=run_id,
        source_key=source_key,
        status="completed",
        results_seen=results_seen,
        jobs_ingested=jobs_ingested,
        last_error=None,
    )


def fail_query(
    db_path: Path,
    *,
    run_id: int,
    source_key: str,
    message: str,
    results_seen: int | None = None,
    jobs_ingested: int | None = None,
) -> dict[str, object]:
    return _update_query_status(
        db_path,
        run_id=run_id,
        source_key=source_key,
        status="failed",
        results_seen=results_seen,
        jobs_ingested=jobs_ingested,
        last_error=message,
    )


def workflow_status(db_path: Path, *, run_id: int) -> dict[str, object]:
    with managed_connection(db_path) as connection:
        run = connection.execute("SELECT notes FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"Run {run_id} does not exist.")

        open_jobs = _count_open_jobs(connection)
        query_counts = _count_query_statuses(connection, run_id)
        status = {
            "run_id": run_id,
            "ready_jobs": open_jobs["ready_jobs"],
            "applying_jobs": open_jobs["applying_jobs"],
            "queries_pending": query_counts["pending"],
            "queries_in_progress": query_counts["in_progress"],
            "queries_failed": query_counts["failed"],
            "queries_completed": query_counts["completed"],
            "queries_total": query_counts["total"],
        }
        drained = (
            status["ready_jobs"] == 0
            and status["applying_jobs"] == 0
            and status["queries_pending"] == 0
            and status["queries_in_progress"] == 0
        )
        status["drained"] = drained
        status["drained_with_errors"] = drained and status["queries_failed"] > 0
        status["requeued_jobs_count"] = _load_notes(run["notes"]).get("requeued_jobs_count", 0)
    return status


def get_job(db_path: Path, *, job_key: str) -> dict[str, object] | None:
    with managed_connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_key = ?", (job_key,)
        ).fetchone()
    return dict(row) if row is not None else None


def get_query(db_path: Path, *, run_id: int, source_key: str) -> dict[str, object] | None:
    with managed_connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM run_search_queries
            WHERE run_id = ? AND source_key = ?
            """,
            (run_id, source_key),
        ).fetchone()
    return dict(row) if row is not None else None


def list_run_seen_urls(db_path: Path, *, run_id: int) -> list[str]:
    with managed_connection(db_path) as connection:
        notes = _get_run_notes(connection, run_id)
        job_keys = [str(item) for item in notes.get("seen_job_keys", []) if item]
        if not job_keys:
            return []

        placeholders = ", ".join("?" for _ in job_keys)
        rows = connection.execute(
            f"""
            SELECT canonical_url, raw_url
            FROM jobs
            WHERE job_key IN ({placeholders})
            ORDER BY id ASC
            """,
            tuple(job_keys),
        ).fetchall()

    urls: list[str] = []
    seen_urls: set[str] = set()
    for row in rows:
        for field_name in ("canonical_url", "raw_url"):
            value = row[field_name]
            if not value:
                continue
            url = str(value)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            urls.append(url)
    return urls


def record_query_skipped_result(
    db_path: Path, *, run_id: int, source_key: str, url: str, reason: str
) -> dict[str, object]:
    created_at = format_timestamp(utc_now())
    with managed_connection(db_path) as connection:
        run = connection.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"Run {run_id} does not exist.")

        connection.execute(
            """
            INSERT INTO run_query_skipped_results (
                run_id, source_key, url, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, source_key, url) DO UPDATE SET
                reason = excluded.reason
            """,
            (run_id, source_key, url, reason, created_at),
        )
        row = connection.execute(
            """
            SELECT *
            FROM run_query_skipped_results
            WHERE run_id = ? AND source_key = ? AND url = ?
            """,
            (run_id, source_key, url),
        ).fetchone()
    return dict(row) if row is not None else {"run_id": run_id, "source_key": source_key, "url": url}


def get_query_skipped_results(
    db_path: Path, *, run_id: int, source_key: str
) -> list[dict[str, object]]:
    with managed_connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM run_query_skipped_results
            WHERE run_id = ? AND source_key = ?
            ORDER BY id ASC
            """,
            (run_id, source_key),
        ).fetchall()
    return [dict(row) for row in rows]


def start_worker_attempt(
    db_path: Path,
    *,
    run_id: int,
    worker_type: str,
    target_key: str,
    attempt_number: int,
    result_path: Path,
    log_path: Path,
) -> dict[str, object]:
    started_at = format_timestamp(utc_now())
    with managed_connection(db_path) as connection:
        run = connection.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"Run {run_id} does not exist.")

        connection.execute(
            """
            INSERT INTO codex_worker_attempts (
                run_id, worker_type, target_key, attempt_number, status,
                started_at, result_path, log_path
            )
            VALUES (?, ?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                run_id,
                worker_type,
                target_key,
                attempt_number,
                started_at,
                str(result_path),
                str(log_path),
            ),
        )
        row = connection.execute(
            """
            SELECT *
            FROM codex_worker_attempts
            WHERE rowid = last_insert_rowid()
            """
        ).fetchone()
    return dict(row) if row is not None else {"run_id": run_id, "worker_type": worker_type, "target_key": target_key}


def finish_worker_attempt(
    db_path: Path,
    *,
    attempt_id: int,
    status: str,
    exit_code: int | None,
    error_message: str | None,
) -> dict[str, object]:
    finished_at = format_timestamp(utc_now())
    with managed_connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM codex_worker_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Worker attempt {attempt_id} does not exist.")

        connection.execute(
            """
            UPDATE codex_worker_attempts
            SET status = ?,
                exit_code = ?,
                error_message = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (status, exit_code, error_message, finished_at, attempt_id),
        )
        updated = connection.execute(
            "SELECT * FROM codex_worker_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
    return dict(updated) if updated is not None else {"id": attempt_id}


def record_application(
    db_path: Path,
    *,
    job_key: str,
    status: str,
    confirmation_text: str | None,
    confirmation_url: str | None,
    error_message: str | None,
    run_id: int | None,
) -> dict[str, object]:
    if status not in {
        "submitted",
        "failed",
        "incomplete",
        "duplicate_skipped",
        "blocked",
    }:
        raise ValueError(f"Unsupported application status: {status}")

    with managed_connection(db_path) as connection:
        job = connection.execute(
            "SELECT * FROM jobs WHERE job_key = ?", (job_key,)
        ).fetchone()
        if job is None:
            raise ValueError(f"Job {job_key} does not exist.")

        applied_at = format_timestamp(utc_now())
        connection.execute(
            """
            INSERT INTO applications (
                job_key, applied_at, status, confirmation_text, confirmation_url, error_message
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_key,
                applied_at,
                status,
                confirmation_text,
                confirmation_url,
                error_message,
            ),
        )
        job_status, status_reason = _map_application_status(status, error_message)
        connection.execute(
            """
            UPDATE jobs
            SET status = ?, status_reason = ?, last_updated_at = ?
            WHERE job_key = ?
            """,
            (job_status, status_reason, applied_at, job_key),
        )

        if run_id is not None:
            if status == "submitted":
                _increment_run_counter(connection, run_id, "jobs_applied")
            elif status in {"failed", "incomplete", "blocked"}:
                _increment_run_counter(connection, run_id, "jobs_failed")
            elif status == "duplicate_skipped":
                _increment_run_counter(connection, run_id, "jobs_skipped_duplicate")

        application = connection.execute(
            """
            SELECT *
            FROM applications
            WHERE rowid = last_insert_rowid()
            """
        ).fetchone()
    return dict(application) if application is not None else {"job_key": job_key}


def record_finding(
    db_path: Path,
    *,
    job_key: str,
    run_id: int,
    application_status: str,
    stage: str,
    category: str,
    summary: str,
    detail: str | None,
    page_url: str | None,
) -> dict[str, object]:
    if application_status not in FINDING_APPLICATION_STATUSES:
        raise ValueError(f"Unsupported finding application status: {application_status}")

    created_at = format_timestamp(utc_now())
    with managed_connection(db_path) as connection:
        job = connection.execute(
            "SELECT job_key FROM jobs WHERE job_key = ?", (job_key,)
        ).fetchone()
        if job is None:
            raise ValueError(f"Job {job_key} does not exist.")

        run = connection.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"Run {run_id} does not exist.")

        connection.execute(
            """
            INSERT INTO application_findings (
                job_key, run_id, application_status, stage, category, summary,
                detail, page_url, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_key,
                run_id,
                application_status,
                stage,
                category,
                summary,
                detail,
                page_url,
                created_at,
            ),
        )
        finding = connection.execute(
            """
            SELECT *
            FROM application_findings
            WHERE rowid = last_insert_rowid()
            """
        ).fetchone()
    return dict(finding) if finding is not None else {"job_key": job_key, "run_id": run_id}


def _build_findings_summary(connection: sqlite3.Connection, run_id: int) -> dict[str, object]:
    category_rows = connection.execute(
        """
        SELECT category, COUNT(*) AS count
        FROM application_findings
        WHERE run_id = ?
        GROUP BY category
        ORDER BY count DESC, category ASC
        """,
        (run_id,),
    ).fetchall()
    total_findings = sum(int(row["count"]) for row in category_rows)

    latest_rows = connection.execute(
        """
        SELECT job_key, application_status, stage, category, summary, detail, page_url, created_at
        FROM application_findings
        WHERE run_id = ?
          AND application_status IN ('blocked', 'incomplete', 'failed')
        ORDER BY created_at DESC, id DESC
        """,
        (run_id,),
    ).fetchall()

    latest_for_unsuccessful_jobs: list[dict[str, object]] = []
    seen_job_keys: set[str] = set()
    for row in latest_rows:
        job_key = str(row["job_key"])
        if job_key in seen_job_keys:
            continue
        seen_job_keys.add(job_key)
        latest_for_unsuccessful_jobs.append(dict(row))

    return {
        "total_findings": total_findings,
        "by_category": [dict(row) for row in category_rows],
        "latest_for_unsuccessful_jobs": latest_for_unsuccessful_jobs,
    }


def _build_search_summary(
    connection: sqlite3.Connection, run_id: int, run_notes: dict[str, object]
) -> dict[str, object]:
    query_counts = _count_query_statuses(connection, run_id)
    return {
        "total_queries": query_counts["total"],
        "completed_queries": query_counts["completed"],
        "failed_queries": query_counts["failed"],
        "pending_queries": query_counts["pending"],
        "in_progress_queries": query_counts["in_progress"],
        "requeued_jobs_count": run_notes.get("requeued_jobs_count", 0),
    }


def _map_application_status(status: str, error_message: str | None) -> tuple[str, str | None]:
    if status == "submitted":
        return "applied", None
    if status == "duplicate_skipped":
        return "duplicate_skipped", error_message or "duplicate_skip_recorded"
    if status == "blocked":
        return "blocked", error_message or "application_flow_blocked"
    if status == "incomplete":
        return "incomplete", error_message or "missing_required_application_data"
    return "failed", error_message or "application_submission_failed"


def _find_attempted_duplicate(
    connection: sqlite3.Connection, job_key: str, canonical_url: str
) -> str | None:
    application_row = connection.execute(
        """
        SELECT applications.status
        FROM applications
        JOIN jobs ON jobs.job_key = applications.job_key
        WHERE jobs.job_key = ? OR jobs.canonical_url = ?
        ORDER BY applications.id DESC
        LIMIT 1
        """,
        (job_key, canonical_url),
    ).fetchone()
    if (
        application_row is not None
        and application_row["status"] in TERMINAL_APPLICATION_STATUSES
    ):
        return f"existing_application_{application_row['status']}"

    job_row = connection.execute(
        """
        SELECT status
        FROM jobs
        WHERE job_key = ? OR canonical_url = ?
        LIMIT 1
        """,
        (job_key, canonical_url),
    ).fetchone()
    if job_row is not None and job_row["status"] in TERMINAL_JOB_STATUSES:
        return f"existing_job_{job_row['status']}"
    return None


def _update_query_status(
    db_path: Path,
    *,
    run_id: int,
    source_key: str,
    status: str,
    results_seen: int | None,
    jobs_ingested: int | None,
    last_error: str | None,
) -> dict[str, object]:
    finished_at = format_timestamp(utc_now())
    with managed_connection(db_path) as connection:
        query_row = connection.execute(
            """
            SELECT *
            FROM run_search_queries
            WHERE run_id = ? AND source_key = ?
            """,
            (run_id, source_key),
        ).fetchone()
        if query_row is None:
            raise ValueError(
                f"Run {run_id} does not have a search query for source '{source_key}'."
            )

        connection.execute(
            """
            UPDATE run_search_queries
            SET status = ?,
                started_at = COALESCE(started_at, ?),
                finished_at = ?,
                results_seen = COALESCE(?, results_seen),
                jobs_ingested = COALESCE(?, jobs_ingested),
                last_error = ?
            WHERE id = ?
            """,
            (
                status,
                finished_at,
                finished_at,
                results_seen,
                jobs_ingested,
                last_error,
                query_row["id"],
            ),
        )
        updated = connection.execute(
            """
            SELECT *
            FROM run_search_queries
            WHERE id = ?
            """,
            (query_row["id"],),
        ).fetchone()
    return dict(updated) if updated is not None else {"run_id": run_id, "source_key": source_key}


def _upsert_job(
    connection: sqlite3.Connection,
    *,
    job_key: str,
    canonical_url: str,
    raw_url: str,
    source: str,
    title: str | None,
    company: str | None,
    location: str | None,
    posted_at: str | None,
    discovered_at: str,
    status: str,
    status_reason: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO jobs (
            job_key, canonical_url, raw_url, source, title, company, location,
            posted_at, discovered_at, status, status_reason, last_updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_key) DO UPDATE SET
            canonical_url = excluded.canonical_url,
            raw_url = excluded.raw_url,
            source = excluded.source,
            title = excluded.title,
            company = excluded.company,
            location = excluded.location,
            posted_at = excluded.posted_at,
            discovered_at = excluded.discovered_at,
            status = excluded.status,
            status_reason = excluded.status_reason,
            last_updated_at = excluded.last_updated_at
        """,
        (
            job_key,
            canonical_url,
            raw_url,
            source,
            title,
            company,
            location,
            posted_at,
            discovered_at,
            status,
            status_reason,
            format_timestamp(utc_now()),
        ),
    )


def _count_open_jobs(connection: sqlite3.Connection) -> dict[str, int]:
    ready_jobs = connection.execute(
        "SELECT COUNT(*) AS count FROM jobs WHERE status = 'ready_to_apply'"
    ).fetchone()
    applying_jobs = connection.execute(
        "SELECT COUNT(*) AS count FROM jobs WHERE status = 'applying'"
    ).fetchone()
    return {
        "ready_jobs": int(ready_jobs["count"]) if ready_jobs is not None else 0,
        "applying_jobs": int(applying_jobs["count"]) if applying_jobs is not None else 0,
    }


def _count_query_statuses(connection: sqlite3.Connection, run_id: int) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM run_search_queries
        WHERE run_id = ?
        GROUP BY status
        """,
        (run_id,),
    ).fetchall()
    counts = {
        "pending": 0,
        "in_progress": 0,
        "completed": 0,
        "failed": 0,
        "total": 0,
    }
    for row in rows:
        status = str(row["status"])
        count = int(row["count"])
        if status in counts:
            counts[status] = count
        counts["total"] += count
    return counts


def _create_run(connection: sqlite3.Connection) -> int:
    started_at = format_timestamp(utc_now())
    notes = json.dumps(
        {"seen_job_keys": [], "requeued_jobs_count": 0}, separators=(",", ":")
    )
    cursor = connection.execute(
        """
        INSERT INTO runs (started_at, notes)
        VALUES (?, ?)
        """,
        (started_at, notes),
    )
    return int(cursor.lastrowid)


def _seed_run_search_queries(
    connection: sqlite3.Connection,
    run_id: int,
    query_specs: list[object],
) -> None:
    for spec in query_specs:
        if not isinstance(spec, dict):
            continue
        source_key = str(spec.get("source_key") or "").strip()
        domain = str(spec.get("domain") or "").strip()
        query_text = str(spec.get("query") or "").strip()
        if not source_key or not domain or not query_text:
            continue
        connection.execute(
            """
            INSERT INTO run_search_queries (
                run_id, source_key, domain, query_text, status
            )
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (run_id, source_key, domain, query_text),
        )


def _requeue_stale_applying_jobs(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'ready_to_apply',
            status_reason = 'requeued_from_interrupted_run',
            last_updated_at = ?
        WHERE status = 'applying'
        """,
        (format_timestamp(utc_now()),),
    )
    return int(cursor.rowcount)


def _assert_run_can_finish(connection: sqlite3.Connection, run_id: int) -> None:
    run = connection.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"Run {run_id} does not exist.")

    open_jobs = _count_open_jobs(connection)
    query_counts = _count_query_statuses(connection, run_id)
    if (
        open_jobs["ready_jobs"] > 0
        or open_jobs["applying_jobs"] > 0
        or query_counts["pending"] > 0
        or query_counts["in_progress"] > 0
    ):
        raise ValueError(
            "Cannot finish run while work remains: "
            f"ready_jobs={open_jobs['ready_jobs']}, "
            f"applying_jobs={open_jobs['applying_jobs']}, "
            f"queries_pending={query_counts['pending']}, "
            f"queries_in_progress={query_counts['in_progress']}."
        )


def _increment_run_counter(
    connection: sqlite3.Connection, run_id: int, column_name: str, amount: int = 1
) -> None:
    connection.execute(
        f"UPDATE runs SET {column_name} = {column_name} + ? WHERE id = ?",
        (amount, run_id),
    )


def _get_run_notes(connection: sqlite3.Connection, run_id: int) -> dict[str, object]:
    row = connection.execute("SELECT notes FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Run {run_id} does not exist.")
    return _load_notes(row["notes"])


def _set_run_notes(
    connection: sqlite3.Connection, run_id: int, notes: dict[str, object]
) -> None:
    connection.execute(
        "UPDATE runs SET notes = ? WHERE id = ?",
        (json.dumps(notes, separators=(",", ":")), run_id),
    )


def _load_notes(raw_notes: str | None) -> dict[str, object]:
    if not raw_notes:
        return {"seen_job_keys": [], "requeued_jobs_count": 0}
    try:
        notes = json.loads(raw_notes)
    except json.JSONDecodeError:
        return {"seen_job_keys": [], "requeued_jobs_count": 0}
    if not isinstance(notes, dict):
        return {"seen_job_keys": [], "requeued_jobs_count": 0}
    if not isinstance(notes.get("seen_job_keys"), list):
        notes["seen_job_keys"] = []
    if not isinstance(notes.get("requeued_jobs_count"), int):
        notes["requeued_jobs_count"] = 0
    return notes
