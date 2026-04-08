from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
import subprocess

from .db import (
    claim_query,
    complete_query,
    fail_query,
    finish_run,
    get_job,
    get_query,
    get_query_skipped_results,
    ingest_job,
    list_run_seen_urls,
    next_job,
    prepare_run,
    record_application,
    record_finding,
    record_query_skipped_result,
    requeue_stale_applying_jobs,
    start_worker_attempt,
    finish_worker_attempt,
    workflow_status,
)
from .profile import ProfileValidationResult, parse_applicant_markdown, validate_profile

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "PROMPTS"
QUERY_WORKER_PROMPT_PATH = PROMPTS_DIR / "CODEX_QUERY_WORKER_PROMPT.md"
APPLY_WORKER_PROMPT_PATH = PROMPTS_DIR / "CODEX_APPLY_WORKER_PROMPT.md"
QUERY_WORKER_SCHEMA_PATH = PROMPTS_DIR / "CODEX_QUERY_WORKER_SCHEMA.json"
APPLY_WORKER_SCHEMA_PATH = PROMPTS_DIR / "CODEX_APPLY_WORKER_SCHEMA.json"

DEFAULT_CODEX_BIN = "codex"
DEFAULT_QUERY_TIMEOUT_SECONDS = 300
DEFAULT_JOB_TIMEOUT_SECONDS = 600
DEFAULT_MAX_WORKER_RETRIES = 1

_UNSUCCESSFUL_APPLICATION_STATUSES = {"failed", "blocked", "incomplete"}
_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(slots=True)
class WorkerConfig:
    repo_root: Path
    db_path: Path
    codex_bin: str = DEFAULT_CODEX_BIN
    codex_profile: str | None = None
    query_timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS
    job_timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS
    max_worker_retries: int = DEFAULT_MAX_WORKER_RETRIES


class WorkerExecutionError(RuntimeError):
    pass


def run_workflow(
    db_path: Path,
    *,
    repo_root: Path,
    run_id: int | None = None,
    codex_bin: str = DEFAULT_CODEX_BIN,
    codex_profile: str | None = None,
    query_timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS,
    job_timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    max_worker_retries: int = DEFAULT_MAX_WORKER_RETRIES,
) -> dict[str, object]:
    resolved_repo_root = repo_root.resolve()
    resolved_db_path = db_path.resolve()
    validation = _validated_profile(resolved_repo_root)
    config = WorkerConfig(
        repo_root=resolved_repo_root,
        db_path=resolved_db_path,
        codex_bin=codex_bin,
        codex_profile=codex_profile,
        query_timeout_seconds=max(1, int(query_timeout_seconds)),
        job_timeout_seconds=max(1, int(job_timeout_seconds)),
        max_worker_retries=max(0, int(max_worker_retries)),
    )

    if run_id is None:
        prepared = prepare_run(resolved_db_path, resolved_repo_root)
        run_id = int(prepared["run_id"])
    else:
        workflow_status(resolved_db_path, run_id=run_id)
        requeue_stale_applying_jobs(resolved_db_path, run_id=run_id)

    while True:
        status = workflow_status(resolved_db_path, run_id=run_id)
        if status["drained"]:
            break

        _drain_backlog(config, run_id=run_id, validation=validation)
        status = workflow_status(resolved_db_path, run_id=run_id)
        if status["drained"]:
            break

        query = claim_query(resolved_db_path, run_id=run_id)
        if query is None:
            status = workflow_status(resolved_db_path, run_id=run_id)
            if status["drained"]:
                break
            raise RuntimeError(
                f"Run {run_id} still has unresolved work, but no claimable query remained."
            )

        _drain_query(
            config,
            run_id=run_id,
            query=query,
            validation=validation,
        )

    return finish_run(resolved_db_path, run_id)


def discover_next_candidate_with_codex(
    db_path: Path,
    *,
    repo_root: Path,
    run_id: int,
    source_key: str,
    codex_bin: str = DEFAULT_CODEX_BIN,
    codex_profile: str | None = None,
    timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS,
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
        query_timeout_seconds=max(1, int(timeout_seconds)),
        max_worker_retries=max(0, int(max_worker_retries)),
    )
    prompt_text = _build_query_worker_prompt(
        config.repo_root,
        validation_result,
        query,
        list_run_seen_urls(resolved_db_path, run_id=run_id),
        get_query_skipped_results(resolved_db_path, run_id=run_id, source_key=source_key),
    )
    try:
        payload = _invoke_codex_worker(
            config,
            run_id=run_id,
            worker_type="query",
            target_key=source_key,
            schema_path=QUERY_WORKER_SCHEMA_PATH,
            prompt_text=prompt_text,
            timeout_seconds=config.query_timeout_seconds,
            validator=_validate_query_worker_payload,
        )
    except WorkerExecutionError as exc:
        return {"outcome": "query_failed", "error_message": str(exc)}

    if payload["outcome"] == "skip_result":
        record_query_skipped_result(
            resolved_db_path,
            run_id=run_id,
            source_key=source_key,
            url=str(payload["result_url"]),
            reason=str(payload["skip_reason"]),
        )
    return payload


def apply_job_with_codex(
    db_path: Path,
    *,
    repo_root: Path,
    run_id: int,
    job_key: str,
    codex_bin: str = DEFAULT_CODEX_BIN,
    codex_profile: str | None = None,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    max_worker_retries: int = DEFAULT_MAX_WORKER_RETRIES,
    validation: ProfileValidationResult | None = None,
) -> dict[str, object]:
    resolved_repo_root = repo_root.resolve()
    resolved_db_path = db_path.resolve()
    validation_result = validation or _validated_profile(resolved_repo_root)
    job = get_job(resolved_db_path, job_key=job_key)
    if job is None:
        raise ValueError(f"Job {job_key} does not exist.")
    if job["status"] not in {"ready_to_apply", "applying"}:
        raise ValueError(
            f"Job {job_key} cannot be sent to Codex from status '{job['status']}'."
        )

    config = WorkerConfig(
        repo_root=resolved_repo_root,
        db_path=resolved_db_path,
        codex_bin=codex_bin,
        codex_profile=codex_profile,
        job_timeout_seconds=max(1, int(timeout_seconds)),
        max_worker_retries=max(0, int(max_worker_retries)),
    )
    prompt_text = _build_apply_worker_prompt(config.repo_root, validation_result, job)

    try:
        payload = _invoke_codex_worker(
            config,
            run_id=run_id,
            worker_type="apply",
            target_key=job_key,
            schema_path=APPLY_WORKER_SCHEMA_PATH,
            prompt_text=prompt_text,
            timeout_seconds=config.job_timeout_seconds,
            validator=_validate_apply_worker_payload,
        )
    except WorkerExecutionError as exc:
        error_message = str(exc)
        application = record_application(
            resolved_db_path,
            job_key=job_key,
            status="failed",
            confirmation_text=None,
            confirmation_url=str(job.get("canonical_url") or job.get("raw_url") or ""),
            error_message=error_message,
            run_id=run_id,
        )
        finding = record_finding(
            resolved_db_path,
            job_key=job_key,
            run_id=run_id,
            application_status="failed",
            stage="worker",
            category="codex_worker_error",
            summary="Codex apply worker did not return a valid result",
            detail=error_message,
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

    application = record_application(
        resolved_db_path,
        job_key=job_key,
        status=str(payload["application_status"]),
        confirmation_text=_as_nullable_string(payload.get("confirmation_text")),
        confirmation_url=_as_nullable_string(payload.get("confirmation_url")),
        error_message=_as_nullable_string(payload.get("error_message")),
        run_id=run_id,
    )

    findings: list[dict[str, object]] = []
    if payload["application_status"] in _UNSUCCESSFUL_APPLICATION_STATUSES:
        for finding_payload in payload["findings"]:
            findings.append(
                record_finding(
                    resolved_db_path,
                    job_key=job_key,
                    run_id=run_id,
                    application_status=str(payload["application_status"]),
                    stage=str(finding_payload["stage"]),
                    category=str(finding_payload["category"]),
                    summary=str(finding_payload["summary"]),
                    detail=_as_nullable_string(finding_payload.get("detail")),
                    page_url=_as_nullable_string(finding_payload.get("page_url")),
                )
            )

    return {
        "worker_result": payload,
        "application": application,
        "findings": findings,
    }


def _drain_backlog(
    config: WorkerConfig, *, run_id: int, validation: ProfileValidationResult
) -> None:
    while True:
        job = next_job(config.db_path, mark_applying=True)
        if job is None:
            return
        apply_job_with_codex(
            config.db_path,
            repo_root=config.repo_root,
            run_id=run_id,
            job_key=str(job["job_key"]),
            codex_bin=config.codex_bin,
            codex_profile=config.codex_profile,
            timeout_seconds=config.job_timeout_seconds,
            max_worker_retries=config.max_worker_retries,
            validation=validation,
        )


def _drain_query(
    config: WorkerConfig,
    *,
    run_id: int,
    query: dict[str, object],
    validation: ProfileValidationResult,
) -> None:
    source_key = str(query["source_key"])
    results_seen = int(query.get("results_seen") or 0)
    jobs_ingested = int(query.get("jobs_ingested") or 0)
    profile_payload = validation.to_dict()["profile"]

    while True:
        payload = discover_next_candidate_with_codex(
            config.db_path,
            repo_root=config.repo_root,
            run_id=run_id,
            source_key=source_key,
            codex_bin=config.codex_bin,
            codex_profile=config.codex_profile,
            timeout_seconds=config.query_timeout_seconds,
            max_worker_retries=config.max_worker_retries,
            validation=validation,
        )
        outcome = str(payload["outcome"])
        if outcome == "candidate":
            candidate = payload["candidate"]
            results_seen += 1
            ingest_result = ingest_job(
                config.db_path,
                run_id=run_id,
                raw_url=str(candidate["raw_url"]),
                canonical_url=_as_nullable_string(candidate.get("canonical_url")),
                source=str(candidate["source"]),
                title=_as_nullable_string(candidate.get("title")),
                company=_as_nullable_string(candidate.get("company")),
                location=_as_nullable_string(candidate.get("location")),
                posted_at=_as_nullable_string(candidate.get("posted_at")),
                discovered_at=None,
                role_keywords=_coerce_string_list(profile_payload.get("target_role_keywords")),
                allowed_locations=_coerce_string_list(profile_payload.get("allowed_locations")),
                allow_unverifiable_freshness=True,
            )
            jobs_ingested += 1
            if ingest_result.status == "ready_to_apply":
                apply_job_with_codex(
                    config.db_path,
                    repo_root=config.repo_root,
                    run_id=run_id,
                    job_key=ingest_result.job_key,
                    codex_bin=config.codex_bin,
                    codex_profile=config.codex_profile,
                    timeout_seconds=config.job_timeout_seconds,
                    max_worker_retries=config.max_worker_retries,
                    validation=validation,
                )
            continue

        if outcome == "skip_result":
            results_seen += 1
            continue

        if outcome == "exhausted":
            complete_query(
                config.db_path,
                run_id=run_id,
                source_key=source_key,
                results_seen=results_seen,
                jobs_ingested=jobs_ingested,
            )
            return

        if outcome == "query_failed":
            fail_query(
                config.db_path,
                run_id=run_id,
                source_key=source_key,
                message=str(payload.get("error_message") or "Codex query worker failed."),
                results_seen=results_seen,
                jobs_ingested=jobs_ingested,
            )
            return

        raise ValueError(f"Unsupported discovery outcome: {outcome}")


def _validated_profile(repo_root: Path) -> ProfileValidationResult:
    validation = validate_profile(repo_root)
    if validation.ok:
        return validation
    missing_items = validation.missing_required_fields + validation.missing_required_files
    raise ValueError(
        "Profile validation failed. Missing required items: " + ", ".join(missing_items)
    )


def _invoke_codex_worker(
    config: WorkerConfig,
    *,
    run_id: int,
    worker_type: str,
    target_key: str,
    schema_path: Path,
    prompt_text: str,
    timeout_seconds: int,
    validator,
) -> dict[str, object]:
    if not schema_path.exists():
        raise WorkerExecutionError(f"Schema file is missing: {schema_path}")

    artifact_dir = _artifact_dir(config.db_path, run_id, worker_type)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    safe_target_key = _safe_filename(target_key)
    last_error = f"Codex {worker_type} worker did not complete."

    for attempt_number in range(1, config.max_worker_retries + 2):
        result_path = artifact_dir / f"{safe_target_key}.attempt-{attempt_number}.result.json"
        log_path = artifact_dir / f"{safe_target_key}.attempt-{attempt_number}.log.txt"
        if result_path.exists():
            result_path.unlink()

        attempt = start_worker_attempt(
            config.db_path,
            run_id=run_id,
            worker_type=worker_type,
            target_key=target_key,
            attempt_number=attempt_number,
            result_path=result_path,
            log_path=log_path,
        )
        command = [
            config.codex_bin,
            "exec",
            "--ephemeral",
            "--full-auto",
            "--color",
            "never",
            "-C",
            str(config.repo_root),
            "--output-schema",
            str(schema_path),
            "-o",
            str(result_path),
        ]
        if config.codex_profile:
            command.extend(["-p", config.codex_profile])

        try:
            completed = subprocess.run(
                command,
                cwd=config.repo_root,
                input=prompt_text,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last_error = f"Codex {worker_type} worker timed out after {timeout_seconds} seconds."
            _write_worker_log(
                log_path,
                command=command,
                prompt_text=prompt_text,
                stdout=_coerce_timeout_stream(exc.stdout),
                stderr=_coerce_timeout_stream(exc.stderr),
                exit_code=None,
                error_message=last_error,
            )
            finish_worker_attempt(
                config.db_path,
                attempt_id=int(attempt["id"]),
                status="timed_out",
                exit_code=None,
                error_message=last_error,
            )
            continue

        _write_worker_log(
            log_path,
            command=command,
            prompt_text=prompt_text,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            error_message=None,
        )
        if completed.returncode != 0:
            last_error = (
                f"Codex {worker_type} worker exited with code {completed.returncode}."
            )
            finish_worker_attempt(
                config.db_path,
                attempt_id=int(attempt["id"]),
                status="cli_failed",
                exit_code=completed.returncode,
                error_message=last_error,
            )
            continue

        if not result_path.exists():
            last_error = (
                f"Codex {worker_type} worker did not produce an output file at {result_path}."
            )
            finish_worker_attempt(
                config.db_path,
                attempt_id=int(attempt["id"]),
                status="missing_output",
                exit_code=completed.returncode,
                error_message=last_error,
            )
            continue

        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            validator(payload)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            last_error = f"Codex {worker_type} worker returned invalid output: {exc}"
            finish_worker_attempt(
                config.db_path,
                attempt_id=int(attempt["id"]),
                status="invalid_output",
                exit_code=completed.returncode,
                error_message=last_error,
            )
            continue

        finish_worker_attempt(
            config.db_path,
            attempt_id=int(attempt["id"]),
            status="succeeded",
            exit_code=completed.returncode,
            error_message=None,
        )
        return payload

    raise WorkerExecutionError(last_error)


def _build_query_worker_prompt(
    repo_root: Path,
    validation: ProfileValidationResult,
    query: dict[str, object],
    seen_urls: list[str],
    skipped_results: list[dict[str, object]],
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
        },
        "current_run_seen_urls": seen_urls,
        "query_skipped_results": skipped_results,
    }
    return _compose_prompt(template, context)


def _build_apply_worker_prompt(
    repo_root: Path,
    validation: ProfileValidationResult,
    job: dict[str, object],
) -> str:
    template = _load_text(APPLY_WORKER_PROMPT_PATH)
    applicant_notes = parse_applicant_markdown(repo_root / "applicant.md")
    context = {
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
    return _compose_prompt(template, context)


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
    outcome = payload.get("outcome")
    if outcome not in {"candidate", "skip_result", "exhausted", "query_failed"}:
        raise ValueError(f"Unsupported query worker outcome: {outcome!r}")

    if outcome == "candidate":
        candidate = payload.get("candidate")
        if not isinstance(candidate, dict):
            raise ValueError("Candidate outcome must include a candidate object.")
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
            if field_name not in candidate:
                raise ValueError(f"Candidate output is missing '{field_name}'.")
        _require_non_empty_string(candidate, "raw_url")
        _require_non_empty_string(candidate, "source")
        _require_non_empty_string(candidate, "page_url")
        for field_name in ("canonical_url", "title", "company", "location", "posted_at"):
            _require_nullable_string(candidate, field_name)
        return

    if outcome == "skip_result":
        _require_non_empty_string(payload, "result_url")
        _require_non_empty_string(payload, "skip_reason")
        return

    if outcome == "query_failed":
        _require_non_empty_string(payload, "error_message")


def _validate_apply_worker_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Apply worker output must be a JSON object.")
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
        if field_name in payload:
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
        for field_name in ("stage", "category", "summary"):
            _require_non_empty_string(finding, field_name)
        for field_name in ("detail", "page_url"):
            if field_name in finding:
                _require_nullable_string(finding, field_name)


def _artifact_dir(db_path: Path, run_id: int, worker_type: str) -> Path:
    return db_path.resolve().parent / "codex_worker_artifacts" / f"run-{run_id}" / worker_type


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
            stdout,
            "",
            "STDERR:",
            stderr,
            "",
        ]
    )
    log_path.write_text("\n".join(contents), encoding="utf-8")


def _coerce_timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _safe_filename(value: str) -> str:
    normalized = _SAFE_FILENAME_PATTERN.sub("-", value).strip("-")
    return normalized or "worker"


def _require_non_empty_string(payload: dict[str, object], field_name: str) -> None:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Field '{field_name}' must be a non-empty string.")


def _require_nullable_string(payload: dict[str, object], field_name: str) -> None:
    value = payload.get(field_name)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Field '{field_name}' must be a string or null.")


def _as_nullable_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
