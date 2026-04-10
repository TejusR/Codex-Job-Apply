from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


RunUiStatus = Literal["running", "needs_resume", "completed", "completed_with_errors"]
ResumeSource = Literal["application_snapshot", "default_profile"]


class ResumeInfo(BaseModel):
    path: str | None
    label: str | None
    source: ResumeSource


class SearchSummary(BaseModel):
    total_queries: int
    completed_queries: int
    failed_queries: int
    pending_queries: int
    in_progress_queries: int
    pending_search_results: int
    processing_search_results: int
    total_search_results: int
    requeued_jobs_count: int


class LiveCounts(BaseModel):
    ready_jobs: int
    applying_jobs: int
    queries_pending: int
    queries_in_progress: int
    search_results_pending: int
    search_results_processing: int
    discovery_workers_running: int
    apply_workers_running: int


class RunAllowedActions(BaseModel):
    resume: bool
    requeue_runner_failures: bool
    finish: bool
    force_finish: bool


class RunSummary(BaseModel):
    id: int
    started_at: str
    finished_at: str | None
    ui_status: RunUiStatus
    jobs_found: int
    jobs_filtered_in: int
    jobs_skipped_old: int
    jobs_skipped_duplicate: int
    jobs_applied: int
    jobs_failed: int
    has_outstanding_work: bool
    findings_total: int
    search_summary: SearchSummary
    live_counts: LiveCounts
    allowed_actions: RunAllowedActions


class QueryRow(BaseModel):
    id: int
    run_id: int
    source_key: str
    domain: str
    query_text: str
    status: str
    started_at: str | None
    finished_at: str | None
    results_seen: int
    jobs_ingested: int
    cursor_json: str | None = None
    last_error: str | None = None


class WorkerSessionRow(BaseModel):
    id: int
    run_id: int
    worker_type: str
    slot_key: str
    thread_id: str | None
    status: str
    started_at: str
    last_used_at: str
    last_error: str | None


class SearchResultRow(BaseModel):
    id: int
    run_id: int
    source_key: str
    parent_result_id: int | None
    origin_kind: str
    url: str
    title: str | None
    snippet: str | None
    visible_date: str | None
    page_number: int | None
    rank: int | None
    status: str
    claimed_by: str | None
    claimed_at: str | None
    finished_at: str | None
    reason: str | None
    job_key: str | None


class ApplicationRow(BaseModel):
    id: int
    job_key: str
    run_id: int | None
    applied_at: str
    status: str
    confirmation_text: str | None
    confirmation_url: str | None
    resume_path_used: str | None
    resume_label_used: str | None
    error_message: str | None


class FindingRow(BaseModel):
    id: int
    job_key: str
    run_id: int
    application_status: str
    stage: str
    category: str
    summary: str
    detail: str | None
    page_url: str | None
    created_at: str


class FindingCategoryCount(BaseModel):
    category: str
    count: int


class LatestFindingRow(BaseModel):
    job_key: str
    application_status: str
    stage: str
    category: str
    summary: str
    detail: str | None
    page_url: str | None
    created_at: str


class FindingsSummary(BaseModel):
    total_findings: int
    by_category: list[FindingCategoryCount]
    latest_for_unsuccessful_jobs: list[LatestFindingRow]


class JobListItem(BaseModel):
    job_key: str
    canonical_url: str
    raw_url: str | None
    source: str | None
    title: str | None
    company: str | None
    location: str | None
    posted_at: str | None
    discovered_at: str
    status: str
    status_reason: str | None
    last_updated_at: str
    latest_application_status: str | None
    latest_applied_at: str | None
    latest_application_run_id: int | None
    resume_info: ResumeInfo


class JobDetail(JobListItem):
    application_history: list[ApplicationRow]
    findings: list[FindingRow]


class RunDetail(BaseModel):
    summary: RunSummary
    queries: list[QueryRow]
    worker_sessions: list[WorkerSessionRow]
    findings_summary: FindingsSummary
    recent_search_results: list[SearchResultRow]
    jobs_preview: list[JobListItem]
    jobs_preview_total: int


class RunsResponse(BaseModel):
    items: list[RunSummary]
    can_start_run: bool
    blocked_by_run_id: int | None


class JobListResponse(BaseModel):
    items: list[JobListItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class RunActionResponse(BaseModel):
    run: RunSummary
    launched: bool = False


class RequeueRunnerFailuresResponse(BaseModel):
    run: RunSummary
    count: int
    job_keys: list[str]


class FinishRunRequest(BaseModel):
    force: bool = False
