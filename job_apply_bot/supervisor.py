from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import threading
import time

from .db import (
    claim_search_result,
    checkpoint_query_progress,
    complete_query,
    ensure_worker_session,
    fail_query,
    finish_run,
    finish_worker_attempt,
    get_job,
    get_query,
    increment_query_jobs_ingested,
    ingest_job,
    insert_search_results,
    list_run_queries,
    list_run_seen_urls,
    mark_job_applying,
    next_job,
    prepare_run,
    record_application,
    record_finding,
    requeue_processing_search_results,
    requeue_stale_applying_jobs,
    reset_worker_sessions,
    start_worker_attempt,
    update_search_result_status,
    update_worker_session,
    workflow_status,
)
from .profile import ProfileValidationResult, parse_applicant_markdown, validate_profile

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "PROMPTS"
QUERY_WORKER_PROMPT_PATH = PROMPTS_DIR / "CODEX_QUERY_WORKER_PROMPT.md"
RESOLVE_WORKER_PROMPT_PATH = PROMPTS_DIR / "CODEX_RESOLVE_WORKER_PROMPT.md"
APPLY_WORKER_PROMPT_PATH = PROMPTS_DIR / "CODEX_APPLY_WORKER_PROMPT.md"
QUERY_WORKER_SCHEMA_PATH = PROMPTS_DIR / "CODEX_QUERY_WORKER_SCHEMA.json"
RESOLVE_WORKER_SCHEMA_PATH = PROMPTS_DIR / "CODEX_RESOLVE_WORKER_SCHEMA.json"
APPLY_WORKER_SCHEMA_PATH = PROMPTS_DIR / "CODEX_APPLY_WORKER_SCHEMA.json"


def _default_codex_bin() -> str:
    if os.name == "nt":
        for candidate in ("codex.cmd", "codex"):
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return "codex.cmd"
    resolved = shutil.which("codex")
    return resolved or "codex"


DEFAULT_CODEX_BIN = _default_codex_bin()
DEFAULT_QUERY_TIMEOUT_SECONDS = None
DEFAULT_JOB_TIMEOUT_SECONDS = None
DEFAULT_MAX_WORKER_RETRIES = 1
DEFAULT_APPLY_WORKERS = 5

_QUERY_RESULTS_PAGE_OUTCOME = "results_page"
_UNSUCCESSFUL_APPLICATION_STATUSES = {"failed", "blocked", "incomplete"}
_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(slots=True)
class WorkerConfig:
    repo_root: Path
    db_path: Path
    codex_bin: str = DEFAULT_CODEX_BIN
    codex_profile: str | None = None
    query_timeout_seconds: int | None = DEFAULT_QUERY_TIMEOUT_SECONDS
    job_timeout_seconds: int | None = DEFAULT_JOB_TIMEOUT_SECONDS
    max_worker_retries: int = DEFAULT_MAX_WORKER_RETRIES
    discovery_workers: int = 0
    apply_workers: int = DEFAULT_APPLY_WORKERS


class WorkerExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_bundle_dir: Path | None = None,
        result_path: Path | None = None,
        log_path: Path | None = None,
        thread_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_bundle_dir = failure_bundle_dir
        self.result_path = result_path
        self.log_path = log_path
        self.thread_id = thread_id


@dataclass(slots=True)
class WorkerInvocationResult:
    payload: dict[str, object]
    failure_bundle_dir: Path | None
    result_path: Path
    log_path: Path
    thread_id: str | None


def run_workflow(
    db_path: Path,
    *,
    repo_root: Path,
    run_id: int | None = None,
    codex_bin: str = DEFAULT_CODEX_BIN,
    codex_profile: str | None = None,
    query_timeout_seconds: int | None = DEFAULT_QUERY_TIMEOUT_SECONDS,
    job_timeout_seconds: int | None = DEFAULT_JOB_TIMEOUT_SECONDS,
    max_worker_retries: int = DEFAULT_MAX_WORKER_RETRIES,
    discovery_workers: int | str | None = "auto",
    apply_workers: int = DEFAULT_APPLY_WORKERS,
) -> dict[str, object]:
    resolved_repo_root = repo_root.resolve()
    resolved_db_path = db_path.resolve()
    validation = _validated_profile(resolved_repo_root)

    if run_id is None:
        prepared = prepare_run(resolved_db_path, resolved_repo_root)
        run_id = int(prepared["run_id"])
    else:
        workflow_status(resolved_db_path, run_id=run_id)
        requeue_stale_applying_jobs(resolved_db_path, run_id=run_id)
        requeue_processing_search_results(resolved_db_path, run_id=run_id)
        reset_worker_sessions(resolved_db_path, run_id=run_id)

    active_queries = [
        query
        for query in list_run_queries(resolved_db_path, run_id=run_id)
        if str(query["status"]) in {"pending", "in_progress"}
    ]
    resolved_apply_workers = max(1, int(apply_workers))
    resolved_discovery_workers = _resolve_discovery_workers(
        discovery_workers, len(active_queries)
    )
    config = WorkerConfig(
        repo_root=resolved_repo_root,
        db_path=resolved_db_path,
        codex_bin=codex_bin,
        codex_profile=codex_profile,
        query_timeout_seconds=_normalize_timeout_seconds(query_timeout_seconds),
        job_timeout_seconds=_normalize_timeout_seconds(job_timeout_seconds),
        max_worker_retries=max(0, int(max_worker_retries)),
        discovery_workers=resolved_discovery_workers,
        apply_workers=resolved_apply_workers,
    )

    discovery_done = threading.Event()
    apply_futures = []
    with ThreadPoolExecutor(max_workers=resolved_apply_workers) as apply_executor:
        for slot_index in range(1, resolved_apply_workers + 1):
            slot_key = f"apply-{slot_index}"
            apply_futures.append(
                apply_executor.submit(
                    _apply_worker_loop,
                    config,
                    run_id=run_id,
                    slot_key=slot_key,
                    validation=validation,
                    discovery_done_event=discovery_done,
                )
            )

        try:
            if resolved_discovery_workers > 0 and active_queries:
                with ThreadPoolExecutor(
                    max_workers=resolved_discovery_workers
                ) as discovery_executor:
                    discovery_futures = [
                        discovery_executor.submit(
                            _discovery_query_loop,
                            config,
                            run_id=run_id,
                            source_key=str(query["source_key"]),
                            validation=validation,
                        )
                        for query in active_queries
                    ]
                    for future in as_completed(discovery_futures):
                        future.result()
        finally:
            discovery_done.set()

        for future in apply_futures:
            future.result()

    return finish_run(resolved_db_path, run_id)


def discover_next_candidate_with_codex(
    db_path: Path,
    *,
    repo_root: Path,
    run_id: int,
    source_key: str,
    codex_bin: str = DEFAULT_CODEX_BIN,
    codex_profile: str | None = None,
    timeout_seconds: int | None = DEFAULT_QUERY_TIMEOUT_SECONDS,
    max_worker_retries: int = DEFAULT_MAX_WORKER_RETRIES,
    validation: ProfileValidationResult | None = None,
) -> dict[str, object]:
    resolved_repo_root = repo_root.resolve()
    resolved_db_path = db_path.resolve()
    validation_result = validation or _validated_profile(resolved_repo_root)
    query = get_query(resolved_db_path, run_id=run_id, source_key=source_key)
    if query is None:
        raise ValueError(f"Run {run_id} does not have a search query for source '{source_key}'.")
    if query["status"] not in {"pending", "in_progress"}:
        raise ValueError(
            f"Query {source_key} in run {run_id} is not claimable (status={query['status']})."
        )

    config = WorkerConfig(
        repo_root=resolved_repo_root,
        db_path=resolved_db_path,
        codex_bin=codex_bin,
        codex_profile=codex_profile,
        query_timeout_seconds=_normalize_timeout_seconds(timeout_seconds),
        max_worker_retries=max(0, int(max_worker_retries)),
        discovery_workers=1,
        apply_workers=1,
    )
    return _run_query_turn(
        config,
        run_id=run_id,
        source_key=source_key,
        validation=validation_result,
    )


def apply_job_with_codex(
    db_path: Path,
    *,
    repo_root: Path,
    run_id: int,
    job_key: str,
    codex_bin: str = DEFAULT_CODEX_BIN,
    codex_profile: str | None = None,
    timeout_seconds: int | None = DEFAULT_JOB_TIMEOUT_SECONDS,
    max_worker_retries: int = DEFAULT_MAX_WORKER_RETRIES,
    validation: ProfileValidationResult | None = None,
) -> dict[str, object]:
    resolved_repo_root = repo_root.resolve()
    resolved_db_path = db_path.resolve()
    validation_result = validation or _validated_profile(resolved_repo_root)
    job = get_job(resolved_db_path, job_key=job_key)
    if job is None:
        raise ValueError(f"Job {job_key} does not exist.")
    if job["status"] == "ready_to_apply":
        job = mark_job_applying(resolved_db_path, job_key=job_key)
    if job is None or job["status"] != "applying":
        raise ValueError(
            f"Job {job_key} cannot be sent to Codex from status '{job['status'] if job else 'missing'}'."
        )

    config = WorkerConfig(
        repo_root=resolved_repo_root,
        db_path=resolved_db_path,
        codex_bin=codex_bin,
        codex_profile=codex_profile,
        job_timeout_seconds=_normalize_timeout_seconds(timeout_seconds),
        max_worker_retries=max(0, int(max_worker_retries)),
        discovery_workers=1,
        apply_workers=1,
    )
    return _apply_existing_job(
        config,
        run_id=run_id,
        slot_key=f"adhoc-{_safe_filename(job_key)}",
        job=job,
        validation=validation_result,
    )


def _discovery_query_loop(
    config: WorkerConfig,
    *,
    run_id: int,
    source_key: str,
    validation: ProfileValidationResult,
) -> None:
    ensure_worker_session(
        config.db_path,
        run_id=run_id,
        worker_type="discovery",
        slot_key=source_key,
    )
    while True:
        query = get_query(config.db_path, run_id=run_id, source_key=source_key)
        if query is None or query["status"] in {"completed", "failed"}:
            return
        _run_query_turn(
            config,
            run_id=run_id,
            source_key=source_key,
            validation=validation,
        )


def _run_query_turn(
    config: WorkerConfig,
    *,
    run_id: int,
    source_key: str,
    validation: ProfileValidationResult,
) -> dict[str, object]:
    query = get_query(config.db_path, run_id=run_id, source_key=source_key)
    if query is None:
        raise ValueError(f"Run {run_id} does not have a search query for source '{source_key}'.")
    if query["status"] not in {"pending", "in_progress"}:
        return {
            "outcome": "query_failed",
            "results": [],
            "next_page": None,
            "query_error": f"Query {source_key} is not claimable from status {query['status']}.",
        }

    checkpoint_query_progress(
        config.db_path,
        run_id=run_id,
        source_key=source_key,
        results_seen=int(query.get("results_seen") or 0),
        jobs_ingested=int(query.get("jobs_ingested") or 0),
        cursor_json=_normalize_optional_string(query.get("cursor_json")),
    )
    refreshed_query = get_query(config.db_path, run_id=run_id, source_key=source_key)
    if refreshed_query is None:
        raise ValueError(f"Run {run_id} does not have a search query for source '{source_key}'.")

    prompt_text = _build_query_worker_prompt(
        config.repo_root,
        validation,
        refreshed_query,
        list_run_seen_urls(config.db_path, run_id=run_id),
    )
    try:
        invocation = _invoke_codex_session_turn(
            config,
            run_id=run_id,
            session_worker_type="discovery",
            slot_key=source_key,
            worker_type="query",
            target_key=source_key,
            schema_path=QUERY_WORKER_SCHEMA_PATH,
            prompt_text=prompt_text,
            timeout_seconds=config.query_timeout_seconds,
            validator=_validate_query_worker_payload,
        )
    except WorkerExecutionError as exc:
        current_query = get_query(config.db_path, run_id=run_id, source_key=source_key)
        results_seen = int(current_query.get("results_seen") or 0) if current_query else 0
        jobs_ingested = int(current_query.get("jobs_ingested") or 0) if current_query else 0
        cursor_json = (
            _normalize_optional_string(current_query.get("cursor_json"))
            if current_query is not None
            else None
        )
        fail_query(
            config.db_path,
            run_id=run_id,
            source_key=source_key,
            message=str(exc),
            results_seen=results_seen,
            jobs_ingested=jobs_ingested,
            cursor_json=cursor_json,
        )
        return {
            "outcome": "query_failed",
            "results": [],
            "next_page": None,
            "query_error": str(exc),
        }

    payload = invocation.payload
    current_query = get_query(config.db_path, run_id=run_id, source_key=source_key)
    results_seen = int(current_query.get("results_seen") or 0) if current_query else 0
    jobs_ingested = int(current_query.get("jobs_ingested") or 0) if current_query else 0
    outcome = str(payload["outcome"])

    if outcome == _QUERY_RESULTS_PAGE_OUTCOME:
        results = payload["results"]
        insert_summary = insert_search_results(
            config.db_path,
            run_id=run_id,
            source_key=source_key,
            origin_kind="google_result",
            results=results,
        )
        results_seen += len(results)
        next_page = payload.get("next_page")
        if next_page is None:
            complete_query(
                config.db_path,
                run_id=run_id,
                source_key=source_key,
                results_seen=results_seen,
                jobs_ingested=jobs_ingested,
                cursor_json=None,
            )
        else:
            checkpoint_query_progress(
                config.db_path,
                run_id=run_id,
                source_key=source_key,
                results_seen=results_seen,
                jobs_ingested=jobs_ingested,
                cursor_json=_dump_json(next_page),
            )
        return {
            **payload,
            "inserted_count": insert_summary["inserted_count"],
        }

    if outcome == "exhausted":
        complete_query(
            config.db_path,
            run_id=run_id,
            source_key=source_key,
            results_seen=results_seen,
            jobs_ingested=jobs_ingested,
            cursor_json=None,
        )
        return payload

    if outcome == "query_failed":
        fail_query(
            config.db_path,
            run_id=run_id,
            source_key=source_key,
            message=str(payload.get("query_error") or "Codex query worker failed."),
            results_seen=results_seen,
            jobs_ingested=jobs_ingested,
            cursor_json=_normalize_optional_string(current_query.get("cursor_json"))
            if current_query is not None
            else None,
        )
        return payload

    raise ValueError(f"Unsupported discovery outcome: {outcome}")


def _apply_worker_loop(
    config: WorkerConfig,
    *,
    run_id: int,
    slot_key: str,
    validation: ProfileValidationResult,
    discovery_done_event: threading.Event,
) -> None:
    ensure_worker_session(
        config.db_path,
        run_id=run_id,
        worker_type="apply",
        slot_key=slot_key,
    )
    while True:
        job = next_job(config.db_path, mark_applying=True)
        if job is not None:
            _apply_existing_job(
                config,
                run_id=run_id,
                slot_key=slot_key,
                job=job,
                validation=validation,
            )
            continue

        search_result = claim_search_result(
            config.db_path,
            run_id=run_id,
            claimed_by=slot_key,
        )
        if search_result is not None:
            _process_search_result(
                config,
                run_id=run_id,
                slot_key=slot_key,
                search_result=search_result,
                validation=validation,
            )
            continue

        status = workflow_status(config.db_path, run_id=run_id)
        if (
            discovery_done_event.is_set()
            and status["ready_jobs"] == 0
            and status["applying_jobs"] == 0
            and status["search_results_pending"] == 0
            and status["search_results_processing"] == 0
        ):
            update_worker_session(
                config.db_path,
                run_id=run_id,
                worker_type="apply",
                slot_key=slot_key,
                status="idle",
                last_error=None,
            )
            return
        time.sleep(0.2)


def _process_search_result(
    config: WorkerConfig,
    *,
    run_id: int,
    slot_key: str,
    search_result: dict[str, object],
    validation: ProfileValidationResult,
) -> None:
    result_id = int(search_result["id"])
    source_key = str(search_result["source_key"])
    try:
        invocation = _resolve_search_result_with_codex(
            config,
            run_id=run_id,
            slot_key=slot_key,
            search_result=search_result,
            validation=validation,
        )
    except WorkerExecutionError as exc:
        update_search_result_status(
            config.db_path,
            result_id=result_id,
            status="failed",
            reason=str(exc),
        )
        return

    payload = invocation.payload
    outcome = str(payload["outcome"])
    if outcome == "expanded":
        child_results = payload["child_results"]
        insert_summary = insert_search_results(
            config.db_path,
            run_id=run_id,
            source_key=source_key,
            parent_result_id=result_id,
            origin_kind="listing_child",
            results=child_results,
        )
        update_search_result_status(
            config.db_path,
            result_id=result_id,
            status="expanded",
            reason=f"expanded_to_{insert_summary['inserted_count']}_child_results",
        )
        return

    if outcome == "skip_result":
        update_search_result_status(
            config.db_path,
            result_id=result_id,
            status="filtered_out",
            reason=str(payload.get("skip_reason") or "result_skipped"),
        )
        return

    if outcome == "result_failed":
        update_search_result_status(
            config.db_path,
            result_id=result_id,
            status="failed",
            reason=str(payload.get("error_message") or "result_resolution_failed"),
        )
        return

    if outcome != "resolved_job":
        raise ValueError(f"Unsupported resolve outcome: {outcome}")

    job_payload = payload["job"]
    profile_payload = validation.to_dict()["profile"]
    ingest_result = ingest_job(
        config.db_path,
        run_id=run_id,
        raw_url=str(job_payload["raw_url"]),
        canonical_url=_as_nullable_string(job_payload.get("canonical_url")),
        source=_as_nullable_string(job_payload.get("source")) or source_key,
        title=_as_nullable_string(job_payload.get("title")),
        company=_as_nullable_string(job_payload.get("company")),
        location=_as_nullable_string(job_payload.get("location")),
        posted_at=_as_nullable_string(job_payload.get("posted_at")),
        discovered_at=None,
        role_keywords=_coerce_string_list(profile_payload.get("target_role_keywords")),
        allowed_locations=_coerce_string_list(profile_payload.get("allowed_locations")),
        allow_unverifiable_freshness=True,
    )
    increment_query_jobs_ingested(
        config.db_path,
        run_id=run_id,
        source_key=source_key,
        amount=1,
    )

    if ingest_result.status == "ready_to_apply":
        claimed_job = mark_job_applying(config.db_path, job_key=ingest_result.job_key)
        if claimed_job is not None and claimed_job["status"] == "applying":
            apply_result = _apply_existing_job(
                config,
                run_id=run_id,
                slot_key=slot_key,
                job=claimed_job,
                validation=validation,
            )
            application = apply_result["application"]
            update_search_result_status(
                config.db_path,
                result_id=result_id,
                status=_map_application_status_to_search_result_status(
                    str(application["status"])
                ),
                reason=_as_nullable_string(application.get("error_message")),
                job_key=ingest_result.job_key,
            )
            return

        current_job = get_job(config.db_path, job_key=ingest_result.job_key)
        update_search_result_status(
            config.db_path,
            result_id=result_id,
            status="ingested",
            reason=_as_nullable_string(current_job.get("status_reason")) if current_job else None,
            job_key=ingest_result.job_key,
        )
        return

    if ingest_result.status == "duplicate_skipped":
        update_search_result_status(
            config.db_path,
            result_id=result_id,
            status="duplicate_skipped",
            reason=ingest_result.status_reason,
            job_key=ingest_result.job_key,
        )
        return

    update_search_result_status(
        config.db_path,
        result_id=result_id,
        status="filtered_out",
        reason=ingest_result.status_reason or ingest_result.action,
        job_key=ingest_result.job_key,
    )


def _resolve_search_result_with_codex(
    config: WorkerConfig,
    *,
    run_id: int,
    slot_key: str,
    search_result: dict[str, object],
    validation: ProfileValidationResult,
) -> WorkerInvocationResult:
    runtime_context = _build_resolve_worker_context(
        config.repo_root,
        validation,
        search_result,
    )
    return _invoke_codex_session_turn(
        config,
        run_id=run_id,
        session_worker_type="apply",
        slot_key=slot_key,
        worker_type="resolve",
        target_key=f"result-{search_result['id']}",
        schema_path=RESOLVE_WORKER_SCHEMA_PATH,
        runtime_context=runtime_context,
        prompt_template_path=RESOLVE_WORKER_PROMPT_PATH,
        timeout_seconds=config.job_timeout_seconds,
        validator=_validate_resolve_worker_payload,
    )


def _apply_existing_job(
    config: WorkerConfig,
    *,
    run_id: int,
    slot_key: str,
    job: dict[str, object],
    validation: ProfileValidationResult,
) -> dict[str, object]:
    job_key = str(job["job_key"])
    resume_path_used, resume_label_used = _resume_snapshot_from_validation(validation)
    runtime_context = _build_apply_worker_context(
        config.repo_root,
        validation,
        job,
    )

    try:
        invocation = _invoke_codex_session_turn(
            config,
            run_id=run_id,
            session_worker_type="apply",
            slot_key=slot_key,
            worker_type="apply",
            target_key=job_key,
            schema_path=APPLY_WORKER_SCHEMA_PATH,
            runtime_context=runtime_context,
            prompt_template_path=APPLY_WORKER_PROMPT_PATH,
            timeout_seconds=config.job_timeout_seconds,
            validator=_validate_apply_worker_payload,
        )
    except WorkerExecutionError as exc:
        error_message = str(exc)
        application = record_application(
            config.db_path,
            job_key=job_key,
            status="failed",
            confirmation_text=None,
            confirmation_url=str(job.get("canonical_url") or job.get("raw_url") or ""),
            error_message=error_message,
            run_id=run_id,
            resume_path_used=resume_path_used,
            resume_label_used=resume_label_used,
        )
        failure_manifest_path = _write_failure_manifest(
            exc.failure_bundle_dir,
            run_id=run_id,
            job_key=job_key,
            application_status="failed",
            error_message=error_message,
            result_path=exc.result_path,
            log_path=exc.log_path,
            worker_result=None,
        )
        finding = record_finding(
            config.db_path,
            job_key=job_key,
            run_id=run_id,
            application_status="failed",
            stage="worker",
            category="codex_worker_error",
            summary="Codex apply worker did not return a valid result",
            detail=_detail_with_bundle_path(
                error_message,
                failure_manifest_path.parent if failure_manifest_path is not None else None,
            ),
            page_url=str(job.get("canonical_url") or job.get("raw_url") or ""),
        )
        return {
            "worker_result": {
                "application_status": "failed",
                "confirmation_text": None,
                "confirmation_url": None,
                "error_message": error_message,
                "findings": [finding],
            },
            "application": application,
            "findings": [finding],
        }

    payload = invocation.payload
    application = record_application(
        config.db_path,
        job_key=job_key,
        status=str(payload["application_status"]),
        confirmation_text=_as_nullable_string(payload.get("confirmation_text")),
        confirmation_url=_as_nullable_string(payload.get("confirmation_url")),
        error_message=_as_nullable_string(payload.get("error_message")),
        run_id=run_id,
        resume_path_used=resume_path_used,
        resume_label_used=resume_label_used,
    )

    findings: list[dict[str, object]] = []
    if payload["application_status"] in _UNSUCCESSFUL_APPLICATION_STATUSES:
        failure_manifest_path = _write_failure_manifest(
            invocation.failure_bundle_dir,
            run_id=run_id,
            job_key=job_key,
            application_status=str(payload["application_status"]),
            error_message=_as_nullable_string(payload.get("error_message")),
            result_path=invocation.result_path,
            log_path=invocation.log_path,
            worker_result=payload,
        )
        for finding_payload in payload["findings"]:
            findings.append(
                record_finding(
                    config.db_path,
                    job_key=job_key,
                    run_id=run_id,
                    application_status=str(payload["application_status"]),
                    stage=str(finding_payload["stage"]),
                    category=str(finding_payload["category"]),
                    summary=str(finding_payload["summary"]),
                    detail=_detail_with_bundle_path(
                        _as_nullable_string(finding_payload.get("detail")),
                        failure_manifest_path.parent if failure_manifest_path is not None else None,
                    ),
                    page_url=_as_nullable_string(finding_payload.get("page_url")),
                )
            )
    else:
        _cleanup_failure_bundle(invocation.failure_bundle_dir)

    return {
        "worker_result": payload,
        "application": application,
        "findings": findings,
    }


def _validated_profile(repo_root: Path) -> ProfileValidationResult:
    validation = validate_profile(repo_root)
    if validation.ok:
        return validation
    missing_items = validation.missing_required_fields + validation.missing_required_files
    raise ValueError(
        "Profile validation failed. Missing required items: " + ", ".join(missing_items)
    )


def _resume_snapshot_from_validation(
    validation: ProfileValidationResult,
) -> tuple[str | None, str | None]:
    profile = validation.to_dict().get("profile", {})
    if not isinstance(profile, dict):
        return None, None

    resume_path = _normalize_optional_string(profile.get("resume_path"))
    resume_label = Path(resume_path).name if resume_path else None
    return resume_path, resume_label


def _resolve_discovery_workers(
    discovery_workers: int | str | None, query_count: int
) -> int:
    if query_count <= 0:
        return 0
    if discovery_workers is None:
        return query_count
    if isinstance(discovery_workers, str):
        value = discovery_workers.strip().lower()
        if not value or value == "auto":
            return query_count
        return max(1, min(query_count, int(value)))
    return max(1, min(query_count, int(discovery_workers)))


def _invoke_codex_session_turn(
    config: WorkerConfig,
    *,
    run_id: int,
    session_worker_type: str,
    slot_key: str,
    worker_type: str,
    target_key: str,
    schema_path: Path,
    prompt_text: str | None = None,
    runtime_context: dict[str, object] | None = None,
    prompt_template_path: Path | None = None,
    timeout_seconds: int | None,
    validator,
) -> WorkerInvocationResult:
    if not schema_path.exists():
        raise WorkerExecutionError(f"Schema file is missing: {schema_path}")

    ensure_worker_session(
        config.db_path,
        run_id=run_id,
        worker_type=session_worker_type,
        slot_key=slot_key,
    )
    artifact_dir = _artifact_dir(config.db_path, run_id, worker_type)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    safe_target_key = _safe_filename(target_key)
    artifact_sequence = _next_artifact_sequence(artifact_dir, safe_target_key)
    session = update_worker_session(
        config.db_path,
        run_id=run_id,
        worker_type=session_worker_type,
        slot_key=slot_key,
        status="idle",
        last_error=None,
    )
    thread_id = _normalize_optional_string(session.get("thread_id"))
    last_error = f"Codex {worker_type} worker did not complete."
    last_failure_bundle_dir: Path | None = None
    last_result_path: Path | None = None
    last_log_path: Path | None = None

    for attempt_number in range(1, config.max_worker_retries + 2):
        result_path = (
            artifact_dir
            / f"{safe_target_key}.invocation-{artifact_sequence}.attempt-{attempt_number}.result.json"
        )
        log_path = (
            artifact_dir
            / f"{safe_target_key}.invocation-{artifact_sequence}.attempt-{attempt_number}.log.txt"
        )
        failure_bundle_dir: Path | None = None
        prompt_for_attempt = prompt_text
        if runtime_context is not None and prompt_template_path is not None:
            prompt_for_attempt, failure_bundle_dir = _prepare_worker_prompt(
                bundle_enabled=worker_type == "apply",
                bundle_root=artifact_dir,
                runtime_context=runtime_context,
                prompt_template_path=prompt_template_path,
                safe_target_key=safe_target_key,
                artifact_sequence=artifact_sequence,
                attempt_number=attempt_number,
            )
        if prompt_for_attempt is None:
            raise WorkerExecutionError(
                f"Codex {worker_type} worker prompt was not provided."
            )

        if result_path.exists():
            result_path.unlink()
        if log_path.exists():
            log_path.unlink()

        attempt = start_worker_attempt(
            config.db_path,
            run_id=run_id,
            worker_type=worker_type,
            target_key=target_key,
            attempt_number=attempt_number,
            result_path=result_path,
            log_path=log_path,
        )
        update_worker_session(
            config.db_path,
            run_id=run_id,
            worker_type=session_worker_type,
            slot_key=slot_key,
            status="running",
            thread_id=thread_id,
            last_error=None,
        )
        last_failure_bundle_dir = failure_bundle_dir
        last_result_path = result_path
        last_log_path = log_path
        command = _build_codex_command(
            config=config,
            result_path=result_path,
            schema_path=schema_path,
            thread_id=thread_id,
        )

        try:
            completed = subprocess.run(
                command,
                cwd=config.repo_root,
                input=prompt_for_attempt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            timeout_stdout = _coerce_timeout_stream(exc.stdout)
            timeout_stderr = _coerce_timeout_stream(exc.stderr)
            thread_id = _coalesce_thread_id(
                thread_id,
                stdout=timeout_stdout,
                stderr=timeout_stderr,
            )
            last_error = _worker_timeout_message(worker_type, timeout_seconds)
            _write_worker_log(
                log_path,
                command=command,
                prompt_text=prompt_for_attempt,
                stdout=timeout_stdout,
                stderr=timeout_stderr,
                exit_code=None,
                error_message=last_error,
            )
            recovered_payload = _recover_timed_out_worker_payload(
                result_path,
                stdout=timeout_stdout,
                stderr=timeout_stderr,
                validator=validator,
            )
            if recovered_payload is not None:
                finish_worker_attempt(
                    config.db_path,
                    attempt_id=int(attempt["id"]),
                    status="succeeded",
                    exit_code=None,
                    error_message=None,
                )
                update_worker_session(
                    config.db_path,
                    run_id=run_id,
                    worker_type=session_worker_type,
                    slot_key=slot_key,
                    status="idle",
                    thread_id=thread_id,
                    last_error=None,
                )
                return WorkerInvocationResult(
                    payload=recovered_payload,
                    failure_bundle_dir=failure_bundle_dir,
                    result_path=result_path,
                    log_path=log_path,
                    thread_id=thread_id,
                )
            _write_failure_manifest(
                failure_bundle_dir,
                run_id=run_id,
                job_key=target_key,
                application_status="failed" if worker_type == "apply" else None,
                error_message=last_error,
                result_path=result_path,
                log_path=log_path,
                worker_result=None,
            )
            finish_worker_attempt(
                config.db_path,
                attempt_id=int(attempt["id"]),
                status="timed_out",
                exit_code=None,
                error_message=last_error,
            )
            update_worker_session(
                config.db_path,
                run_id=run_id,
                worker_type=session_worker_type,
                slot_key=slot_key,
                status="idle",
                thread_id=thread_id,
                last_error=last_error,
            )
            raise WorkerExecutionError(
                last_error,
                failure_bundle_dir=failure_bundle_dir,
                result_path=result_path,
                log_path=log_path,
                thread_id=thread_id,
            )

        thread_id = _coalesce_thread_id(
            thread_id,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        _write_worker_log(
            log_path,
            command=command,
            prompt_text=prompt_for_attempt,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            error_message=None,
        )
        if completed.returncode != 0:
            last_error = (
                f"Codex {worker_type} worker exited with code {completed.returncode}."
            )
            _write_failure_manifest(
                failure_bundle_dir,
                run_id=run_id,
                job_key=target_key,
                application_status="failed" if worker_type == "apply" else None,
                error_message=last_error,
                result_path=result_path,
                log_path=log_path,
                worker_result=None,
            )
            finish_worker_attempt(
                config.db_path,
                attempt_id=int(attempt["id"]),
                status="cli_failed",
                exit_code=completed.returncode,
                error_message=last_error,
            )
            update_worker_session(
                config.db_path,
                run_id=run_id,
                worker_type=session_worker_type,
                slot_key=slot_key,
                status="idle",
                thread_id=thread_id,
                last_error=last_error,
            )
            continue

        if not result_path.exists():
            last_error = (
                f"Codex {worker_type} worker did not produce an output file at {result_path}."
            )
            _write_failure_manifest(
                failure_bundle_dir,
                run_id=run_id,
                job_key=target_key,
                application_status="failed" if worker_type == "apply" else None,
                error_message=last_error,
                result_path=result_path,
                log_path=log_path,
                worker_result=None,
            )
            finish_worker_attempt(
                config.db_path,
                attempt_id=int(attempt["id"]),
                status="missing_output",
                exit_code=completed.returncode,
                error_message=last_error,
            )
            update_worker_session(
                config.db_path,
                run_id=run_id,
                worker_type=session_worker_type,
                slot_key=slot_key,
                status="idle",
                thread_id=thread_id,
                last_error=last_error,
            )
            continue

        try:
            payload = _load_valid_worker_payload(result_path, validator=validator)
            if payload is None:
                raise ValueError("Output file was missing after existence check.")
        except ValueError as exc:
            last_error = f"Codex {worker_type} worker returned invalid output: {exc}"
            _write_failure_manifest(
                failure_bundle_dir,
                run_id=run_id,
                job_key=target_key,
                application_status="failed" if worker_type == "apply" else None,
                error_message=last_error,
                result_path=result_path,
                log_path=log_path,
                worker_result=None,
            )
            finish_worker_attempt(
                config.db_path,
                attempt_id=int(attempt["id"]),
                status="invalid_output",
                exit_code=completed.returncode,
                error_message=last_error,
            )
            update_worker_session(
                config.db_path,
                run_id=run_id,
                worker_type=session_worker_type,
                slot_key=slot_key,
                status="idle",
                thread_id=thread_id,
                last_error=last_error,
            )
            continue

        finish_worker_attempt(
            config.db_path,
            attempt_id=int(attempt["id"]),
            status="succeeded",
            exit_code=completed.returncode,
            error_message=None,
        )
        update_worker_session(
            config.db_path,
            run_id=run_id,
            worker_type=session_worker_type,
            slot_key=slot_key,
            status="idle",
            thread_id=thread_id,
            last_error=None,
        )
        return WorkerInvocationResult(
            payload=payload,
            failure_bundle_dir=failure_bundle_dir,
            result_path=result_path,
            log_path=log_path,
            thread_id=thread_id,
        )

    raise WorkerExecutionError(
        last_error,
        failure_bundle_dir=last_failure_bundle_dir,
        result_path=last_result_path,
        log_path=last_log_path,
        thread_id=thread_id,
    )


def _build_codex_command(
    *,
    config: WorkerConfig,
    result_path: Path,
    schema_path: Path,
    thread_id: str | None,
) -> list[str]:
    command = [
        config.codex_bin,
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--color",
        "never",
        "-C",
        str(config.repo_root),
        "--json",
        "-o",
        str(result_path),
    ]
    if config.codex_profile:
        command.extend(["-p", config.codex_profile])
    if thread_id:
        command.extend(["resume", thread_id, "-"])
    else:
        command.extend(["--output-schema", str(schema_path)])
    return command


def _normalize_timeout_seconds(timeout_seconds: int | None) -> int | None:
    if timeout_seconds is None:
        return None
    return max(1, int(timeout_seconds))


def _worker_timeout_message(worker_type: str, timeout_seconds: int | None) -> str:
    if timeout_seconds is None:
        return f"Codex {worker_type} worker timed out."
    return f"Codex {worker_type} worker timed out after {timeout_seconds} seconds."


def _recover_timed_out_worker_payload(
    result_path: Path,
    *,
    stdout: str,
    stderr: str,
    validator,
) -> dict[str, object] | None:
    try:
        payload = _load_valid_worker_payload(result_path, validator=validator)
    except ValueError:
        payload = None
    if payload is not None:
        return payload

    last_valid_payload: dict[str, object] | None = None
    for stream in (stdout, stderr):
        recovered = _load_last_valid_worker_payload_from_stream(
            stream,
            validator=validator,
        )
        if recovered is not None:
            last_valid_payload = recovered
    return last_valid_payload


def _build_query_worker_prompt(
    repo_root: Path,
    validation: ProfileValidationResult,
    query: dict[str, object],
    seen_urls: list[str],
) -> str:
    template = _load_text(QUERY_WORKER_PROMPT_PATH)
    context = {
        "repo_root": str(repo_root),
        "docs": {
            "workflow": str(repo_root / "WORKFLOW.md"),
            "search_spec": str(repo_root / "SEARCH_SPEC.md"),
            "mcp_setup": str(repo_root / "MCP_SETUP.md"),
            "env": str(repo_root / ".env"),
            "applicant_md": str(repo_root / "applicant.md"),
        },
        "profile": validation.to_dict()["profile"],
        "query": {
            "run_id": query["run_id"],
            "source_key": query["source_key"],
            "domain": query["domain"],
            "query_text": query["query_text"],
            "status": query["status"],
            "results_seen": query["results_seen"],
            "jobs_ingested": query["jobs_ingested"],
            "cursor": _load_cursor_payload(query.get("cursor_json")),
        },
        "current_run_seen_urls": seen_urls,
    }
    return _compose_prompt(template, context)


def _build_resolve_worker_context(
    repo_root: Path,
    validation: ProfileValidationResult,
    search_result: dict[str, object],
) -> dict[str, object]:
    applicant_notes = parse_applicant_markdown(repo_root / "applicant.md")
    return {
        "repo_root": str(repo_root),
        "docs": {
            "workflow": str(repo_root / "WORKFLOW.md"),
            "application_rules": str(repo_root / "APPLICATION_RULES.md"),
            "search_spec": str(repo_root / "SEARCH_SPEC.md"),
            "mcp_setup": str(repo_root / "MCP_SETUP.md"),
            "env": str(repo_root / ".env"),
            "applicant_md": str(repo_root / "applicant.md"),
        },
        "profile": validation.to_dict()["profile"],
        "search_result": search_result,
        "applicant_sections": applicant_notes.sections,
    }


def _build_apply_worker_context(
    repo_root: Path,
    validation: ProfileValidationResult,
    job: dict[str, object],
) -> dict[str, object]:
    applicant_notes = parse_applicant_markdown(repo_root / "applicant.md")
    return {
        "repo_root": str(repo_root),
        "docs": {
            "workflow": str(repo_root / "WORKFLOW.md"),
            "application_rules": str(repo_root / "APPLICATION_RULES.md"),
            "mcp_setup": str(repo_root / "MCP_SETUP.md"),
            "env": str(repo_root / ".env"),
            "applicant_md": str(repo_root / "applicant.md"),
        },
        "profile": validation.to_dict()["profile"],
        "job": job,
        "applicant_sections": applicant_notes.sections,
        "allowed_application_statuses": [
            "submitted",
            "failed",
            "blocked",
            "incomplete",
            "duplicate_skipped",
        ],
    }


def _compose_prompt(template: str, context: dict[str, object]) -> str:
    return (
        template.strip()
        + "\n\nRuntime context follows. Treat it as authoritative input for this run.\n"
        + "Return only JSON that matches the provided schema.\n\n"
        + "```json\n"
        + json.dumps(context, indent=2, sort_keys=True)
        + "\n```\n"
    )


def _validate_query_worker_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Query worker output must be a JSON object.")
    for field_name in ("outcome", "results", "next_page", "query_error"):
        if field_name not in payload:
            raise ValueError(f"Query worker output is missing '{field_name}'.")
    outcome = payload.get("outcome")
    if outcome not in {_QUERY_RESULTS_PAGE_OUTCOME, "exhausted", "query_failed"}:
        raise ValueError(f"Unsupported query worker outcome: {outcome!r}")

    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("Query worker output must include a results list.")
    for result in results:
        _validate_result_item(result)

    next_page = payload.get("next_page")
    if next_page is not None and not isinstance(next_page, dict):
        raise ValueError("Field 'next_page' must be an object or null.")

    if outcome == _QUERY_RESULTS_PAGE_OUTCOME:
        if not results:
            raise ValueError("Results-page outcome must include at least one visible result.")
        _require_null_field(payload, "query_error")
        return

    if outcome == "exhausted":
        if results:
            raise ValueError("Exhausted outcome must not include results.")
        _require_null_field(payload, "next_page")
        _require_null_field(payload, "query_error")
        return

    if results:
        raise ValueError("Query-failed outcome must not include results.")
    _require_null_field(payload, "next_page")
    _require_non_empty_string(payload, "query_error")


def _validate_resolve_worker_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Resolve worker output must be a JSON object.")
    for field_name in ("outcome", "job", "child_results", "skip_reason", "error_message"):
        if field_name not in payload:
            raise ValueError(f"Resolve worker output is missing '{field_name}'.")

    outcome = payload.get("outcome")
    if outcome not in {"resolved_job", "expanded", "skip_result", "result_failed"}:
        raise ValueError(f"Unsupported resolve worker outcome: {outcome!r}")

    child_results = payload.get("child_results")
    if not isinstance(child_results, list):
        raise ValueError("Resolve worker output must include a child_results list.")
    for result in child_results:
        _validate_result_item(result)

    if outcome == "resolved_job":
        _validate_candidate_payload(payload.get("job"))
        if child_results:
            raise ValueError("Resolved-job outcome must not include child results.")
        _require_null_field(payload, "skip_reason")
        _require_null_field(payload, "error_message")
        return

    _require_null_field(payload, "job")
    if outcome == "expanded":
        if not child_results:
            raise ValueError("Expanded outcome must include at least one child result.")
        _require_null_field(payload, "skip_reason")
        _require_null_field(payload, "error_message")
        return

    if outcome == "skip_result":
        if child_results:
            raise ValueError("Skip-result outcome must not include child results.")
        _require_non_empty_string(payload, "skip_reason")
        _require_null_field(payload, "error_message")
        return

    if child_results:
        raise ValueError("Result-failed outcome must not include child results.")
    _require_null_field(payload, "skip_reason")
    _require_non_empty_string(payload, "error_message")


def _validate_apply_worker_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Apply worker output must be a JSON object.")
    for field_name in (
        "application_status",
        "confirmation_text",
        "confirmation_url",
        "error_message",
        "findings",
    ):
        if field_name not in payload:
            raise ValueError(f"Apply worker output is missing '{field_name}'.")
    status = payload.get("application_status")
    if status not in {
        "submitted",
        "failed",
        "blocked",
        "incomplete",
        "duplicate_skipped",
    }:
        raise ValueError(f"Unsupported application status: {status!r}")

    for field_name in ("confirmation_text", "confirmation_url", "error_message"):
        _require_nullable_string(payload, field_name)

    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError("Apply worker output must include a findings list.")
    if status in _UNSUCCESSFUL_APPLICATION_STATUSES and not findings:
        raise ValueError(
            f"Application status '{status}' requires at least one structured finding."
        )

    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("Each finding must be an object.")
        for field_name in ("stage", "category", "summary", "detail", "page_url"):
            if field_name not in finding:
                raise ValueError(f"Finding output is missing '{field_name}'.")
        for field_name in ("stage", "category", "summary"):
            _require_non_empty_string(finding, field_name)
        for field_name in ("detail", "page_url"):
            _require_nullable_string(finding, field_name)


def _prepare_worker_prompt(
    *,
    bundle_enabled: bool,
    bundle_root: Path,
    runtime_context: dict[str, object],
    prompt_template_path: Path,
    safe_target_key: str,
    artifact_sequence: int,
    attempt_number: int,
) -> tuple[str, Path | None]:
    template = _load_text(prompt_template_path)
    if not bundle_enabled:
        return _compose_prompt(template, runtime_context), None

    failure_bundle_dir = (
        bundle_root
        / f"{safe_target_key}.invocation-{artifact_sequence}.attempt-{attempt_number}.failure"
    )
    failure_bundle_paths = _failure_bundle_paths(failure_bundle_dir)
    context = dict(runtime_context)
    context["failure_bundle"] = {
        key: str(path) for key, path in failure_bundle_paths.items()
    }
    failure_bundle_dir.mkdir(parents=True, exist_ok=True)
    _write_json_file(failure_bundle_paths["runtime_context_path"], context)
    prompt_text = _compose_prompt(template, context)
    failure_bundle_paths["prompt_path"].write_text(prompt_text, encoding="utf-8")
    return prompt_text, failure_bundle_dir


def _artifact_dir(db_path: Path, run_id: int, worker_type: str) -> Path:
    return db_path.resolve().parent / "codex_worker_artifacts" / f"run-{run_id}" / worker_type


def _failure_bundle_paths(bundle_dir: Path) -> dict[str, Path]:
    return {
        "bundle_dir": bundle_dir,
        "runtime_context_path": bundle_dir / "runtime_context.json",
        "prompt_path": bundle_dir / "prompt.txt",
        "failure_manifest_path": bundle_dir / "failure_manifest.json",
        "playwright_snapshot_path": bundle_dir / "playwright_snapshot.md",
        "playwright_screenshot_path": bundle_dir / "playwright_screenshot.png",
        "page_html_path": bundle_dir / "page_html.html",
        "console_path": bundle_dir / "console.json",
        "network_path": bundle_dir / "network.json",
    }


def _write_failure_manifest(
    bundle_dir: Path | None,
    *,
    run_id: int,
    job_key: str,
    application_status: str | None,
    error_message: str | None,
    result_path: Path | None,
    log_path: Path | None,
    worker_result: dict[str, object] | None,
) -> Path | None:
    if bundle_dir is None:
        return None

    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_paths = _failure_bundle_paths(bundle_dir)
    manifest = {
        "run_id": run_id,
        "job_key": job_key,
        "application_status": application_status,
        "error_message": error_message,
        "worker_result": worker_result,
        "result_path": str(result_path) if result_path is not None else None,
        "log_path": str(log_path) if log_path is not None else None,
        "artifacts": {
            key: {
                "path": str(path),
                "exists": path.exists(),
            }
            for key, path in bundle_paths.items()
            if key != "bundle_dir"
        },
    }
    _write_json_file(bundle_paths["failure_manifest_path"], manifest)
    return bundle_paths["failure_manifest_path"]


def _write_worker_log(
    log_path: Path,
    *,
    command: list[str],
    prompt_text: str,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    error_message: str | None,
) -> None:
    safe_stdout = _coerce_timeout_stream(stdout)
    safe_stderr = _coerce_timeout_stream(stderr)
    contents = [
        "COMMAND:",
        json.dumps(command),
        "",
        f"EXIT_CODE: {exit_code}",
    ]
    if error_message:
        contents.extend(["", "ERROR:", error_message])
    contents.extend(
        [
            "",
            "PROMPT:",
            prompt_text,
            "",
            "STDOUT:",
            safe_stdout,
            "",
            "STDERR:",
            safe_stderr,
            "",
        ]
    )
    log_path.write_text("\n".join(contents), encoding="utf-8")


def _write_json_file(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _coerce_timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _coalesce_thread_id(
    current_thread_id: str | None,
    *,
    stdout: str,
    stderr: str,
) -> str | None:
    return current_thread_id or _extract_thread_id(stdout) or _extract_thread_id(stderr)


def _extract_thread_id(stream: str | None) -> str | None:
    if not stream:
        return None
    for raw_line in stream.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(payload, dict)
            and payload.get("type") == "thread.started"
            and isinstance(payload.get("thread_id"), str)
            and payload["thread_id"].strip()
        ):
            return payload["thread_id"]
    return None


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _safe_filename(value: str) -> str:
    normalized = _SAFE_FILENAME_PATTERN.sub("-", value).strip("-")
    return normalized or "worker"


def _cleanup_failure_bundle(bundle_dir: Path | None) -> None:
    if bundle_dir is None or not bundle_dir.exists():
        return
    shutil.rmtree(bundle_dir, ignore_errors=True)


def _detail_with_bundle_path(detail: str | None, bundle_dir: Path | None) -> str | None:
    if bundle_dir is None:
        return detail
    bundle_line = f"Failure bundle: {bundle_dir}"
    if detail is None or not detail.strip():
        return bundle_line
    if bundle_line in detail:
        return detail
    return f"{detail}\n\n{bundle_line}"


def _next_artifact_sequence(artifact_dir: Path, safe_target_key: str) -> int:
    pattern = re.compile(
        rf"^{re.escape(safe_target_key)}\.invocation-(\d+)\.attempt-\d+\.(?:result\.json|log\.txt)$"
    )
    highest = 0
    for path in artifact_dir.iterdir():
        match = pattern.match(path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _load_valid_worker_payload(
    result_path: Path,
    *,
    validator,
) -> dict[str, object] | None:
    if not result_path.exists():
        return None

    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"could not parse output file {result_path}: {exc}") from exc

    validator(payload)
    return payload


def _load_last_valid_worker_payload_from_stream(
    stream: str,
    *,
    validator,
) -> dict[str, object] | None:
    last_valid_payload: dict[str, object] | None = None
    for raw_line in stream.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            validator(payload)
        except ValueError:
            continue
        if isinstance(payload, dict):
            last_valid_payload = payload
    return last_valid_payload


def _validate_result_item(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Each result item must be an object.")
    for field_name in ("url", "title", "snippet", "visible_date", "page_number", "rank"):
        if field_name not in payload:
            raise ValueError(f"Result item is missing '{field_name}'.")
    _require_non_empty_string(payload, "url")
    for field_name in ("title", "snippet", "visible_date"):
        _require_nullable_string(payload, field_name)
    _require_integer(payload, "page_number")
    _require_integer(payload, "rank")


def _validate_candidate_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Resolved job must be an object.")
    for field_name in (
        "raw_url",
        "canonical_url",
        "source",
        "title",
        "company",
        "location",
        "posted_at",
        "page_url",
    ):
        if field_name not in payload:
            raise ValueError(f"Resolved job is missing '{field_name}'.")
    _require_non_empty_string(payload, "raw_url")
    _require_non_empty_string(payload, "source")
    _require_non_empty_string(payload, "page_url")
    for field_name in ("canonical_url", "title", "company", "location", "posted_at"):
        _require_nullable_string(payload, field_name)


def _require_non_empty_string(payload: dict[str, object], field_name: str) -> None:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Field '{field_name}' must be a non-empty string.")


def _require_null_field(payload: dict[str, object], field_name: str) -> None:
    if payload.get(field_name) is not None:
        raise ValueError(f"Field '{field_name}' must be null.")


def _require_nullable_string(payload: dict[str, object], field_name: str) -> None:
    value = payload.get(field_name)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Field '{field_name}' must be a string or null.")


def _require_integer(payload: dict[str, object], field_name: str) -> None:
    value = payload.get(field_name)
    if not isinstance(value, int):
        raise ValueError(f"Field '{field_name}' must be an integer.")


def _load_cursor_payload(value: object) -> object | None:
    text = _normalize_optional_string(value)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _dump_json(value: object) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _as_nullable_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _map_application_status_to_search_result_status(application_status: str) -> str:
    if application_status == "submitted":
        return "applied"
    if application_status in {"blocked", "incomplete", "failed"}:
        return application_status
    if application_status == "duplicate_skipped":
        return "duplicate_skipped"
    return "failed"
