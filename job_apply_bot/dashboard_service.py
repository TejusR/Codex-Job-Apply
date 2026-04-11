from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math
import sqlite3
import subprocess
import sys

from .db import (
    finish_run,
    list_worker_sessions,
    managed_connection,
    prepare_run,
    requeue_runner_failures,
)
from .dashboard_models import (
    ApplicationRow,
    FindingCategoryCount,
    FindingRow,
    FindingsSummary,
    JobDetail,
    JobListItem,
    JobListResponse,
    LatestFindingRow,
    LiveCounts,
    QueryRow,
    RequeueRunnerFailuresResponse,
    ResumeInfo,
    RunActionResponse,
    RunAllowedActions,
    RunDetail,
    RunsResponse,
    RunSummary,
    SearchResultRow,
    SearchSummary,
    WorkerSessionRow,
)
from .profile import validate_profile


LATEST_APPLICATION_CTE = """
WITH latest_application AS (
    SELECT applications.*
    FROM applications
    JOIN (
        SELECT job_key, MAX(id) AS max_id
        FROM applications
        GROUP BY job_key
    ) latest
      ON latest.max_id = applications.id
)
"""


@dataclass(slots=True)
class ProfileResumeDefaults:
    path: str | None
    label: str | None


class DashboardError(RuntimeError):
    pass


class DashboardConflictError(DashboardError):
    pass


class DashboardNotFoundError(DashboardError):
    pass


def list_runs_overview(db_path: Path) -> RunsResponse:
    with managed_connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM runs
            ORDER BY id DESC
            """
        ).fetchall()
        items = [_build_run_summary(connection, row) for row in rows]

    blocked_by = next(
        (item.id for item in items if item.ui_status in {"running", "needs_resume"}),
        None,
    )
    return RunsResponse(
        items=items,
        can_start_run=blocked_by is None,
        blocked_by_run_id=blocked_by,
    )


def get_run_detail(db_path: Path, repo_root: Path, run_id: int) -> RunDetail:
    with managed_connection(db_path) as connection:
        run_row = connection.execute(
            """
            SELECT *
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if run_row is None:
            raise DashboardNotFoundError(f"Run {run_id} does not exist.")

        summary = _build_run_summary(connection, run_row)
        query_rows = connection.execute(
            """
            SELECT *
            FROM run_search_queries
            WHERE run_id = ?
            ORDER BY
                CASE status
                    WHEN 'in_progress' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'failed' THEN 2
                    WHEN 'completed' THEN 3
                    ELSE 4
                END,
                id ASC
            """,
            (run_id,),
        ).fetchall()
        recent_results = connection.execute(
            """
            SELECT *
            FROM run_search_results
            WHERE run_id = ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (run_id,),
        ).fetchall()
        findings_summary = _build_findings_summary(connection, run_id)

    jobs_preview_response = list_jobs(
        db_path,
        repo_root=repo_root,
        run_id=run_id,
        page=1,
        page_size=8,
    )

    return RunDetail(
        summary=summary,
        queries=[QueryRow.model_validate(dict(row)) for row in query_rows],
        worker_sessions=[
            WorkerSessionRow.model_validate(item)
            for item in list_worker_sessions(db_path, run_id=run_id)
        ],
        findings_summary=findings_summary,
        recent_search_results=[
            SearchResultRow.model_validate(dict(row)) for row in recent_results
        ],
        jobs_preview=jobs_preview_response.items,
        jobs_preview_total=jobs_preview_response.total,
    )


def list_jobs(
    db_path: Path,
    *,
    repo_root: Path,
    run_id: int | None = None,
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> JobListResponse:
    resolved_page = max(1, int(page))
    resolved_page_size = max(1, min(100, int(page_size)))
    offset = (resolved_page - 1) * resolved_page_size
    profile_defaults = load_profile_resume_defaults(repo_root)

    with managed_connection(db_path) as connection:
        filters, params = _build_job_filters(
            connection,
            run_id=run_id,
            status=status,
            source=source,
            q=q,
        )
        where_sql = ""
        if filters:
            where_sql = "WHERE " + " AND ".join(filters)

        total_row = connection.execute(
            f"""
            {LATEST_APPLICATION_CTE}
            SELECT COUNT(*) AS count
            FROM jobs
            LEFT JOIN latest_application
              ON latest_application.job_key = jobs.job_key
            {where_sql}
            """,
            tuple(params),
        ).fetchone()
        total = int(total_row["count"]) if total_row is not None else 0
        available_sources = _list_job_sources(
            connection,
            run_id=run_id,
            status=status,
            q=q,
        )

        rows = connection.execute(
            f"""
            {LATEST_APPLICATION_CTE}
            SELECT
                jobs.job_key,
                jobs.canonical_url,
                jobs.raw_url,
                jobs.source,
                jobs.title,
                jobs.company,
                jobs.location,
                jobs.posted_at,
                jobs.discovered_at,
                jobs.status,
                jobs.status_reason,
                jobs.last_updated_at,
                latest_application.status AS latest_application_status,
                latest_application.applied_at AS latest_applied_at,
                latest_application.run_id AS latest_application_run_id,
                latest_application.resume_path_used AS latest_resume_path_used,
                latest_application.resume_label_used AS latest_resume_label_used
            FROM jobs
            LEFT JOIN latest_application
              ON latest_application.job_key = jobs.job_key
            {where_sql}
            ORDER BY jobs.last_updated_at DESC, jobs.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, resolved_page_size, offset),
        ).fetchall()

    items = [_job_list_item_from_row(dict(row), profile_defaults) for row in rows]
    total_pages = max(1, math.ceil(total / resolved_page_size)) if total else 1
    return JobListResponse(
        items=items,
        available_sources=available_sources,
        page=resolved_page,
        page_size=resolved_page_size,
        total=total,
        total_pages=total_pages,
    )


def get_job_detail(db_path: Path, *, repo_root: Path, job_key: str) -> JobDetail:
    profile_defaults = load_profile_resume_defaults(repo_root)
    with managed_connection(db_path) as connection:
        row = connection.execute(
            f"""
            {LATEST_APPLICATION_CTE}
            SELECT
                jobs.job_key,
                jobs.canonical_url,
                jobs.raw_url,
                jobs.source,
                jobs.title,
                jobs.company,
                jobs.location,
                jobs.posted_at,
                jobs.discovered_at,
                jobs.status,
                jobs.status_reason,
                jobs.last_updated_at,
                latest_application.status AS latest_application_status,
                latest_application.applied_at AS latest_applied_at,
                latest_application.run_id AS latest_application_run_id,
                latest_application.resume_path_used AS latest_resume_path_used,
                latest_application.resume_label_used AS latest_resume_label_used
            FROM jobs
            LEFT JOIN latest_application
              ON latest_application.job_key = jobs.job_key
            WHERE jobs.job_key = ?
            """,
            (job_key,),
        ).fetchone()
        if row is None:
            raise DashboardNotFoundError(f"Job {job_key} does not exist.")

        application_rows = connection.execute(
            """
            SELECT *
            FROM applications
            WHERE job_key = ?
            ORDER BY id DESC
            """,
            (job_key,),
        ).fetchall()
        finding_rows = connection.execute(
            """
            SELECT *
            FROM application_findings
            WHERE job_key = ?
            ORDER BY created_at DESC, id DESC
            """,
            (job_key,),
        ).fetchall()

    payload = dict(row)
    item = _job_list_item_from_row(payload, profile_defaults)
    return JobDetail(
        **item.model_dump(),
        application_history=[
            ApplicationRow.model_validate(dict(application_row))
            for application_row in application_rows
        ],
        findings=[FindingRow.model_validate(dict(finding_row)) for finding_row in finding_rows],
    )


def start_run_workflow(db_path: Path, *, repo_root: Path) -> RunActionResponse:
    _assert_no_other_active_run(db_path)
    prepared = prepare_run(db_path, repo_root)
    run_id = int(prepared["run_id"])
    _launch_run_workflow(db_path, repo_root=repo_root, run_id=run_id)
    run = _get_run_summary(db_path, run_id)
    return RunActionResponse(run=run, launched=True)


def resume_run_workflow(db_path: Path, *, repo_root: Path, run_id: int) -> RunActionResponse:
    run = _get_run_summary(db_path, run_id)
    if run.finished_at is not None or not run.has_outstanding_work:
        raise DashboardConflictError(f"Run {run_id} has no remaining work to resume.")
    if run.ui_status == "running":
        raise DashboardConflictError(f"Run {run_id} is already running.")
    _assert_no_other_active_run(db_path, allowed_run_id=run_id)
    _launch_run_workflow(db_path, repo_root=repo_root, run_id=run_id)
    return RunActionResponse(run=_get_run_summary(db_path, run_id), launched=True)


def requeue_failed_jobs_for_run(
    db_path: Path,
    *,
    run_id: int,
) -> RequeueRunnerFailuresResponse:
    run = _get_run_summary(db_path, run_id)
    if run.finished_at is not None:
        raise DashboardConflictError(f"Run {run_id} is already finished.")
    result = requeue_runner_failures(db_path, run_id=run_id)
    return RequeueRunnerFailuresResponse(
        run=_get_run_summary(db_path, run_id),
        count=int(result["count"]),
        job_keys=[str(item) for item in result["job_keys"]],
    )


def finish_run_from_dashboard(
    db_path: Path,
    *,
    run_id: int,
    force: bool,
) -> RunActionResponse:
    run = _get_run_summary(db_path, run_id)
    if run.ui_status == "running":
        raise DashboardConflictError(f"Run {run_id} is still running.")
    try:
        finish_run(db_path, run_id, force=force)
    except ValueError as exc:
        raise DashboardConflictError(str(exc)) from exc
    return RunActionResponse(run=_get_run_summary(db_path, run_id), launched=False)


def load_profile_resume_defaults(repo_root: Path) -> ProfileResumeDefaults:
    profile = validate_profile(repo_root).to_dict().get("profile", {})
    if not isinstance(profile, dict):
        return ProfileResumeDefaults(path=None, label=None)

    resume_path = _normalize_optional_string(profile.get("resume_path"))
    return ProfileResumeDefaults(
        path=resume_path,
        label=Path(resume_path).name if resume_path else None,
    )


def _build_run_summary(
    connection: sqlite3.Connection,
    run_row: sqlite3.Row,
) -> RunSummary:
    run_id = int(run_row["id"])
    notes = _load_notes(run_row["notes"])
    query_counts = _count_statuses(connection, "run_search_queries", run_id=run_id)
    search_result_counts = _count_statuses(connection, "run_search_results", run_id=run_id)
    worker_counts = _count_running_workers(connection, run_id=run_id)
    ready_jobs, applying_jobs = _count_run_jobs_by_status(
        connection,
        _seen_job_keys(notes),
    )
    has_outstanding_work = (
        ready_jobs > 0
        or applying_jobs > 0
        or query_counts["pending"] > 0
        or query_counts["in_progress"] > 0
        or search_result_counts["pending"] > 0
        or search_result_counts["processing"] > 0
    )
    has_errors = int(run_row["jobs_failed"]) > 0 or query_counts["failed"] > 0
    finished_at = _normalize_optional_string(run_row["finished_at"])
    if finished_at is not None or not has_outstanding_work:
        ui_status = "completed_with_errors" if has_errors else "completed"
    elif worker_counts["discovery_workers_running"] > 0 or worker_counts["apply_workers_running"] > 0:
        ui_status = "running"
    else:
        ui_status = "needs_resume"

    return RunSummary(
        id=run_id,
        started_at=str(run_row["started_at"]),
        finished_at=finished_at,
        ui_status=ui_status,
        jobs_found=int(run_row["jobs_found"]),
        jobs_filtered_in=int(run_row["jobs_filtered_in"]),
        jobs_skipped_old=int(run_row["jobs_skipped_old"]),
        jobs_skipped_duplicate=int(run_row["jobs_skipped_duplicate"]),
        jobs_applied=int(run_row["jobs_applied"]),
        jobs_failed=int(run_row["jobs_failed"]),
        has_outstanding_work=has_outstanding_work,
        findings_total=_count_findings_total(connection, run_id),
        search_summary=SearchSummary(
            total_queries=query_counts["total"],
            completed_queries=query_counts["completed"],
            failed_queries=query_counts["failed"],
            pending_queries=query_counts["pending"],
            in_progress_queries=query_counts["in_progress"],
            pending_search_results=search_result_counts["pending"],
            processing_search_results=search_result_counts["processing"],
            total_search_results=search_result_counts["total"],
            requeued_jobs_count=int(notes.get("requeued_jobs_count", 0) or 0),
        ),
        live_counts=LiveCounts(
            ready_jobs=ready_jobs,
            applying_jobs=applying_jobs,
            queries_pending=query_counts["pending"],
            queries_in_progress=query_counts["in_progress"],
            search_results_pending=search_result_counts["pending"],
            search_results_processing=search_result_counts["processing"],
            discovery_workers_running=worker_counts["discovery_workers_running"],
            apply_workers_running=worker_counts["apply_workers_running"],
        ),
        allowed_actions=_allowed_actions(
            finished_at=finished_at,
            has_outstanding_work=has_outstanding_work,
            ui_status=ui_status,
        ),
    )


def _allowed_actions(
    *,
    finished_at: str | None,
    has_outstanding_work: bool,
    ui_status: str,
) -> RunAllowedActions:
    is_running = ui_status == "running"
    is_unfinished = finished_at is None
    can_resume = is_unfinished and has_outstanding_work and ui_status == "needs_resume"
    can_finish = is_unfinished and not is_running
    return RunAllowedActions(
        resume=can_resume,
        requeue_runner_failures=can_resume,
        finish=can_finish,
        force_finish=can_finish and has_outstanding_work,
    )


def _build_findings_summary(
    connection: sqlite3.Connection,
    run_id: int,
) -> FindingsSummary:
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

    latest_for_unsuccessful_jobs: list[LatestFindingRow] = []
    seen_job_keys: set[str] = set()
    for row in latest_rows:
        job_key = str(row["job_key"])
        if job_key in seen_job_keys:
            continue
        seen_job_keys.add(job_key)
        latest_for_unsuccessful_jobs.append(LatestFindingRow.model_validate(dict(row)))

    return FindingsSummary(
        total_findings=sum(int(row["count"]) for row in category_rows),
        by_category=[
            FindingCategoryCount.model_validate(dict(row)) for row in category_rows
        ],
        latest_for_unsuccessful_jobs=latest_for_unsuccessful_jobs,
    )


def _build_job_filters(
    connection: sqlite3.Connection,
    *,
    run_id: int | None,
    status: str | None,
    source: str | None,
    q: str | None,
) -> tuple[list[str], list[object]]:
    filters: list[str] = []
    params: list[object] = []
    if run_id is not None:
        job_keys = _job_keys_for_run(connection, run_id)
        if not job_keys:
            return ["1 = 0"], []
        placeholders = ", ".join("?" for _ in job_keys)
        filters.append(f"jobs.job_key IN ({placeholders})")
        params.extend(job_keys)
    if status:
        filters.append("jobs.status = ?")
        params.append(status)
    if source:
        filters.append("jobs.source = ?")
        params.append(source)
    search_text = _normalize_optional_string(q)
    if search_text:
        filters.append(
            """
            (
                LOWER(COALESCE(jobs.title, '')) LIKE ?
                OR LOWER(COALESCE(jobs.company, '')) LIKE ?
                OR LOWER(COALESCE(jobs.location, '')) LIKE ?
                OR LOWER(COALESCE(jobs.source, '')) LIKE ?
                OR LOWER(COALESCE(jobs.canonical_url, '')) LIKE ?
                OR LOWER(COALESCE(jobs.job_key, '')) LIKE ?
            )
            """.strip()
        )
        term = f"%{search_text.lower()}%"
        params.extend([term, term, term, term, term, term])
    return filters, params


def _list_job_sources(
    connection: sqlite3.Connection,
    *,
    run_id: int | None,
    status: str | None,
    q: str | None,
) -> list[str]:
    filters, params = _build_job_filters(
        connection,
        run_id=run_id,
        status=status,
        source=None,
        q=q,
    )
    filters.extend(
        [
            "jobs.source IS NOT NULL",
            "TRIM(jobs.source) <> ''",
        ]
    )
    where_sql = "WHERE " + " AND ".join(filters)
    rows = connection.execute(
        f"""
        SELECT DISTINCT jobs.source
        FROM jobs
        {where_sql}
        ORDER BY LOWER(jobs.source) ASC
        """,
        tuple(params),
    ).fetchall()
    return [str(row["source"]) for row in rows]


def _job_list_item_from_row(
    row: dict[str, object],
    profile_defaults: ProfileResumeDefaults,
) -> JobListItem:
    resume_info = _resume_info_from_row(row, profile_defaults)
    return JobListItem(
        job_key=str(row["job_key"]),
        canonical_url=str(row["canonical_url"]),
        raw_url=_normalize_optional_string(row.get("raw_url")),
        source=_normalize_optional_string(row.get("source")),
        title=_normalize_optional_string(row.get("title")),
        company=_normalize_optional_string(row.get("company")),
        location=_normalize_optional_string(row.get("location")),
        posted_at=_normalize_optional_string(row.get("posted_at")),
        discovered_at=str(row["discovered_at"]),
        status=str(row["status"]),
        status_reason=_normalize_optional_string(row.get("status_reason")),
        last_updated_at=str(row["last_updated_at"]),
        latest_application_status=_normalize_optional_string(
            row.get("latest_application_status")
        ),
        latest_applied_at=_normalize_optional_string(row.get("latest_applied_at")),
        latest_application_run_id=_normalize_optional_int(
            row.get("latest_application_run_id")
        ),
        resume_info=resume_info,
    )


def _resume_info_from_row(
    row: dict[str, object],
    profile_defaults: ProfileResumeDefaults,
) -> ResumeInfo:
    snapshot_path = _normalize_optional_string(row.get("latest_resume_path_used"))
    snapshot_label = _normalize_optional_string(row.get("latest_resume_label_used"))
    if snapshot_path or snapshot_label:
        return ResumeInfo(
            path=snapshot_path,
            label=snapshot_label or (Path(snapshot_path).name if snapshot_path else None),
            source="application_snapshot",
        )

    return ResumeInfo(
        path=profile_defaults.path,
        label=profile_defaults.label,
        source="default_profile",
    )


def _assert_no_other_active_run(db_path: Path, *, allowed_run_id: int | None = None) -> None:
    overview = list_runs_overview(db_path)
    blocked_by = overview.blocked_by_run_id
    if blocked_by is not None and blocked_by != allowed_run_id:
        raise DashboardConflictError(
            f"Run {blocked_by} must finish or be resumed before starting another run."
        )


def _launch_run_workflow(db_path: Path, *, repo_root: Path, run_id: int) -> Path:
    log_dir = db_path.resolve().parent / "dashboard_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run-{run_id}.log"
    command = [
        sys.executable,
        "-m",
        "job_apply_bot",
        "--db-path",
        str(db_path.resolve()),
        "run-workflow",
        "--repo-root",
        str(repo_root.resolve()),
        "--run-id",
        str(run_id),
    ]
    with log_path.open("a", encoding="utf-8") as log_handle:
        subprocess.Popen(
            command,
            cwd=repo_root.resolve(),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return log_path


def _get_run_summary(db_path: Path, run_id: int) -> RunSummary:
    with managed_connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise DashboardNotFoundError(f"Run {run_id} does not exist.")
        return _build_run_summary(connection, row)


def _count_statuses(
    connection: sqlite3.Connection,
    table_name: str,
    *,
    run_id: int,
) -> dict[str, int]:
    rows = connection.execute(
        f"""
        SELECT status, COUNT(*) AS count
        FROM {table_name}
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
        "processing": 0,
        "total": 0,
    }
    for row in rows:
        status = str(row["status"])
        count = int(row["count"])
        if status in counts:
            counts[status] = count
        counts["total"] += count
    return counts


def _count_running_workers(
    connection: sqlite3.Connection,
    *,
    run_id: int,
) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT worker_type, COUNT(*) AS count
        FROM codex_worker_sessions
        WHERE run_id = ? AND status = 'running'
        GROUP BY worker_type
        """,
        (run_id,),
    ).fetchall()
    counts = {
        "discovery_workers_running": 0,
        "apply_workers_running": 0,
    }
    for row in rows:
        if row["worker_type"] == "discovery":
            counts["discovery_workers_running"] = int(row["count"])
        elif row["worker_type"] == "apply":
            counts["apply_workers_running"] = int(row["count"])
    return counts


def _count_run_jobs_by_status(
    connection: sqlite3.Connection,
    job_keys: list[str],
) -> tuple[int, int]:
    if not job_keys:
        return 0, 0
    placeholders = ", ".join("?" for _ in job_keys)
    rows = connection.execute(
        f"""
        SELECT status, COUNT(*) AS count
        FROM jobs
        WHERE job_key IN ({placeholders}) AND status IN ('ready_to_apply', 'applying')
        GROUP BY status
        """,
        tuple(job_keys),
    ).fetchall()
    ready_jobs = 0
    applying_jobs = 0
    for row in rows:
        if row["status"] == "ready_to_apply":
            ready_jobs = int(row["count"])
        elif row["status"] == "applying":
            applying_jobs = int(row["count"])
    return ready_jobs, applying_jobs


def _count_findings_total(connection: sqlite3.Connection, run_id: int) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM application_findings
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    return int(row["count"]) if row is not None else 0


def _job_keys_for_run(connection: sqlite3.Connection, run_id: int) -> list[str]:
    run = connection.execute(
        """
        SELECT notes
        FROM runs
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    if run is None:
        raise DashboardNotFoundError(f"Run {run_id} does not exist.")
    return _seen_job_keys(_load_notes(run["notes"]))


def _seen_job_keys(notes: dict[str, object]) -> list[str]:
    raw = notes.get("seen_job_keys", [])
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def _load_notes(raw_value: object) -> dict[str, object]:
    if isinstance(raw_value, dict):
        return raw_value
    if not raw_value:
        return {}
    try:
        payload = json.loads(str(raw_value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
