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

ATTEMPTED_APPLICATION_STATUSES = ("submitted", "failed", "incomplete", "duplicate_skipped")
TERMINAL_JOB_STATUSES = ("applied", "duplicate_skipped", "applying")

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
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at);",
    "CREATE INDEX IF NOT EXISTS idx_applications_job_key ON applications(job_key);",
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
    started_at = format_timestamp(utc_now())
    notes = json.dumps({"seen_job_keys": []}, separators=(",", ":"))
    with managed_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO runs (started_at, notes)
            VALUES (?, ?)
            """,
            (started_at, notes),
        )
        run_id = cursor.lastrowid
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return {"id": run_id}
    payload = dict(row)
    payload["notes"] = _load_notes(payload.get("notes"))
    return payload


def finish_run(db_path: Path, run_id: int) -> dict[str, object]:
    finished_at = format_timestamp(utc_now())
    with managed_connection(db_path) as connection:
        connection.execute(
            "UPDATE runs SET finished_at = COALESCE(finished_at, ?) WHERE id = ?",
            (finished_at, run_id),
        )
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Run {run_id} does not exist.")
    summary = dict(row)
    summary["notes"] = _load_notes(summary.get("notes"))
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
        if job_key in run_notes["seen_job_keys"]:
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
        run_notes["seen_job_keys"].append(job_key)
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
    if status not in {"submitted", "failed", "incomplete", "duplicate_skipped"}:
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
            elif status in {"failed", "incomplete"}:
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


def _map_application_status(status: str, error_message: str | None) -> tuple[str, str | None]:
    if status == "submitted":
        return "applied", None
    if status == "duplicate_skipped":
        return "duplicate_skipped", error_message or "duplicate_skip_recorded"
    if status == "incomplete":
        return "failed", error_message or "missing_required_application_data"
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
    if application_row is not None and application_row["status"] in ATTEMPTED_APPLICATION_STATUSES:
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


def _increment_run_counter(
    connection: sqlite3.Connection, run_id: int, column_name: str, amount: int = 1
) -> None:
    connection.execute(
        f"UPDATE runs SET {column_name} = {column_name} + ? WHERE id = ?",
        (amount, run_id),
    )


def _get_run_notes(connection: sqlite3.Connection, run_id: int) -> dict[str, list[str]]:
    row = connection.execute("SELECT notes FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Run {run_id} does not exist.")
    return _load_notes(row["notes"])


def _set_run_notes(
    connection: sqlite3.Connection, run_id: int, notes: dict[str, list[str]]
) -> None:
    connection.execute(
        "UPDATE runs SET notes = ? WHERE id = ?",
        (json.dumps(notes, separators=(",", ":")), run_id),
    )


def _load_notes(raw_notes: str | None) -> dict[str, list[str]]:
    if not raw_notes:
        return {"seen_job_keys": []}
    try:
        notes = json.loads(raw_notes)
    except json.JSONDecodeError:
        return {"seen_job_keys": []}
    notes.setdefault("seen_job_keys", [])
    return notes
