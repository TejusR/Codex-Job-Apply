from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import (
    claim_query,
    complete_query,
    fail_query,
    finish_run,
    ingest_job,
    next_job,
    next_query,
    prepare_run,
    record_application,
    record_finding,
    requeue_runner_failures,
    start_run,
    workflow_status,
)
from .profile import parse_csv, validate_profile
from .supervisor import (
    DEFAULT_CODEX_BIN,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    DEFAULT_MAX_WORKER_RETRIES,
    DEFAULT_QUERY_TIMEOUT_SECONDS,
    apply_job_with_codex,
    discover_next_candidate_with_codex,
    run_workflow,
)

DEFAULT_DB_PATH = Path("data/job_apply_bot.sqlite3")
DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job_apply_bot",
        description="Support tooling for the Codex-driven job application workflow.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path. Defaults to data/job_apply_bot.sqlite3",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate-profile", help="Validate .env and applicant.md inputs."
    )
    validate_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing .env and applicant.md",
    )

    subparsers.add_parser("start-run", help="Create a new run row.")

    prepare_parser = subparsers.add_parser(
        "prepare-run",
        help="Validate the profile, create a run, seed search queries, and recover backlog.",
    )
    prepare_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing .env and applicant.md",
    )

    finish_parser = subparsers.add_parser("finish-run", help="Finalize an existing run.")
    finish_parser.add_argument("--run-id", type=int, required=True)
    finish_parser.add_argument(
        "--force",
        action="store_true",
        help="Finish the run even when pending queries or ready/applying jobs remain.",
    )

    ingest_parser = subparsers.add_parser("ingest-job", help="Normalize and store one job.")
    ingest_parser.add_argument("--run-id", type=int, required=True)
    ingest_parser.add_argument("--raw-url", required=True)
    ingest_parser.add_argument("--canonical-url")
    ingest_parser.add_argument("--source")
    ingest_parser.add_argument("--title")
    ingest_parser.add_argument("--company")
    ingest_parser.add_argument("--location")
    ingest_parser.add_argument("--posted-at")
    ingest_parser.add_argument("--discovered-at")
    ingest_parser.add_argument("--role-keywords")
    ingest_parser.add_argument("--allowed-locations")
    ingest_parser.add_argument(
        "--allow-unverifiable-freshness",
        action="store_true",
        help="Keep jobs with unverified freshness eligible for application.",
    )

    next_parser = subparsers.add_parser("next-job", help="Return the next ready job.")
    next_parser.add_argument(
        "--mark-applying",
        action="store_true",
        help="Mark the returned job as applying.",
    )

    next_query_parser = subparsers.add_parser(
        "next-query", help="Return the next pending query for a run and mark it in progress."
    )
    next_query_parser.add_argument("--run-id", type=int, required=True)

    claim_query_parser = subparsers.add_parser(
        "claim-query",
        help="Return the oldest in-progress query, or claim the next pending query for a run.",
    )
    claim_query_parser.add_argument("--run-id", type=int, required=True)

    complete_query_parser = subparsers.add_parser(
        "complete-query", help="Mark a run search query as completed."
    )
    complete_query_parser.add_argument("--run-id", type=int, required=True)
    complete_query_parser.add_argument("--source-key", required=True)
    complete_query_parser.add_argument("--results-seen", type=int)
    complete_query_parser.add_argument("--jobs-ingested", type=int)

    fail_query_parser = subparsers.add_parser(
        "fail-query", help="Mark a run search query as failed and continue the run."
    )
    fail_query_parser.add_argument("--run-id", type=int, required=True)
    fail_query_parser.add_argument("--source-key", required=True)
    fail_query_parser.add_argument("--message", required=True)
    fail_query_parser.add_argument("--results-seen", type=int)
    fail_query_parser.add_argument("--jobs-ingested", type=int)

    workflow_status_parser = subparsers.add_parser(
        "workflow-status",
        help="Return run/query backlog status and whether the workflow is drained.",
    )
    workflow_status_parser.add_argument("--run-id", type=int, required=True)

    record_parser = subparsers.add_parser(
        "record-application", help="Persist an application result."
    )
    record_parser.add_argument("--job-key", required=True)
    record_parser.add_argument(
        "--status",
        required=True,
        choices=["submitted", "failed", "incomplete", "duplicate_skipped", "blocked"],
    )
    record_parser.add_argument("--confirmation-text")
    record_parser.add_argument("--confirmation-url")
    record_parser.add_argument("--error-message")
    record_parser.add_argument("--run-id", type=int)
    record_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing .env and applicant.md",
    )
    record_parser.add_argument("--resume-path-used")
    record_parser.add_argument("--resume-label-used")

    finding_parser = subparsers.add_parser(
        "record-finding", help="Persist a structured workflow finding."
    )
    finding_parser.add_argument("--job-key", required=True)
    finding_parser.add_argument("--run-id", type=int, required=True)
    finding_parser.add_argument(
        "--application-status",
        required=True,
        choices=["failed", "incomplete", "blocked"],
    )
    finding_parser.add_argument("--stage", required=True)
    finding_parser.add_argument("--category", required=True)
    finding_parser.add_argument("--summary", required=True)
    finding_parser.add_argument("--detail")
    finding_parser.add_argument("--page-url")

    discover_parser = subparsers.add_parser(
        "discover-next-candidate-with-codex",
        help="Run one Codex-backed discovery page harvest for a claimed query.",
    )
    discover_parser.add_argument("--run-id", type=int, required=True)
    discover_parser.add_argument("--source-key", required=True)
    discover_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing workflow docs, .env, and applicant.md",
    )
    discover_parser.add_argument(
        "--codex-bin",
        default=DEFAULT_CODEX_BIN,
        help="Codex executable to invoke. Defaults to 'codex'.",
    )
    discover_parser.add_argument("--codex-profile")
    discover_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_QUERY_TIMEOUT_SECONDS,
        help="Timeout for a single query worker attempt. Omit for no overall timeout.",
    )
    discover_parser.add_argument(
        "--max-worker-retries",
        type=int,
        default=DEFAULT_MAX_WORKER_RETRIES,
        help="How many times to retry an invalid or failed worker after the first attempt.",
    )

    apply_parser = subparsers.add_parser(
        "apply-job-with-codex",
        help="Run a fresh Codex worker for one job and persist the terminal result.",
    )
    apply_parser.add_argument("--run-id", type=int, required=True)
    apply_parser.add_argument("--job-key", required=True)
    apply_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing workflow docs, .env, and applicant.md",
    )
    apply_parser.add_argument(
        "--codex-bin",
        default=DEFAULT_CODEX_BIN,
        help="Codex executable to invoke. Defaults to 'codex'.",
    )
    apply_parser.add_argument("--codex-profile")
    apply_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_JOB_TIMEOUT_SECONDS,
        help="Timeout for a single apply worker attempt. Omit for no overall timeout.",
    )
    apply_parser.add_argument(
        "--max-worker-retries",
        type=int,
        default=DEFAULT_MAX_WORKER_RETRIES,
        help="How many times to retry an invalid or failed worker after the first attempt.",
    )

    run_workflow_parser = subparsers.add_parser(
        "run-workflow",
        help="Supervise the full workflow with parallel discovery/apply worker pools.",
    )
    run_workflow_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing workflow docs, .env, and applicant.md",
    )
    run_workflow_parser.add_argument("--run-id", type=int)
    run_workflow_parser.add_argument(
        "--codex-bin",
        default=DEFAULT_CODEX_BIN,
        help="Codex executable to invoke. Defaults to 'codex'.",
    )
    run_workflow_parser.add_argument("--codex-profile")
    run_workflow_parser.add_argument(
        "--query-timeout-seconds",
        type=int,
        default=DEFAULT_QUERY_TIMEOUT_SECONDS,
        help="Timeout for a single query worker attempt. Omit for no overall timeout.",
    )
    run_workflow_parser.add_argument(
        "--job-timeout-seconds",
        type=int,
        default=DEFAULT_JOB_TIMEOUT_SECONDS,
        help="Timeout for a single job worker attempt. Omit for no overall timeout.",
    )
    run_workflow_parser.add_argument(
        "--max-worker-retries",
        type=int,
        default=DEFAULT_MAX_WORKER_RETRIES,
        help="How many times to retry an invalid or failed worker after the first attempt.",
    )
    run_workflow_parser.add_argument(
        "--discovery-workers",
        default="auto",
        help="Discovery worker concurrency. Use 'auto' for one slot per enabled board/query.",
    )
    run_workflow_parser.add_argument(
        "--apply-workers",
        type=int,
        default=5,
        help="Apply/resolution worker concurrency. Defaults to 5.",
    )

    requeue_failures_parser = subparsers.add_parser(
        "requeue-runner-failures",
        help="Requeue jobs in a run whose latest failure was caused by an internal Codex worker error.",
    )
    requeue_failures_parser.add_argument("--run-id", type=int, required=True)

    dashboard_parser = subparsers.add_parser(
        "serve-dashboard",
        help="Serve the FastAPI workflow dashboard and static frontend assets.",
    )
    dashboard_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing the workflow and frontend directories.",
    )
    dashboard_parser.add_argument(
        "--host",
        default=DEFAULT_DASHBOARD_HOST,
        help=f"Host interface to bind. Defaults to {DEFAULT_DASHBOARD_HOST}.",
    )
    dashboard_parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_DASHBOARD_PORT,
        help=f"Port to bind. Defaults to {DEFAULT_DASHBOARD_PORT}.",
    )
    dashboard_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload while developing the dashboard.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate-profile":
        result = validate_profile(args.repo_root.resolve())
        _print_json(result.to_dict())
        return 0 if result.ok else 2

    if args.command == "start-run":
        _print_json(start_run(args.db_path.resolve()))
        return 0

    if args.command == "prepare-run":
        _print_json(prepare_run(args.db_path.resolve(), args.repo_root.resolve()))
        return 0

    if args.command == "finish-run":
        _print_json(finish_run(args.db_path.resolve(), args.run_id, force=args.force))
        return 0

    if args.command == "ingest-job":
        result = ingest_job(
            args.db_path.resolve(),
            run_id=args.run_id,
            raw_url=args.raw_url,
            canonical_url=args.canonical_url,
            source=args.source,
            title=args.title,
            company=args.company,
            location=args.location,
            posted_at=args.posted_at,
            discovered_at=args.discovered_at,
            role_keywords=parse_csv(args.role_keywords),
            allowed_locations=parse_csv(args.allowed_locations),
            allow_unverifiable_freshness=args.allow_unverifiable_freshness,
        )
        _print_json(result.to_dict())
        return 0

    if args.command == "next-job":
        result = next_job(args.db_path.resolve(), mark_applying=args.mark_applying)
        _print_json(result)
        return 0

    if args.command == "next-query":
        result = next_query(args.db_path.resolve(), run_id=args.run_id)
        _print_json(result)
        return 0

    if args.command == "claim-query":
        result = claim_query(args.db_path.resolve(), run_id=args.run_id)
        _print_json(result)
        return 0

    if args.command == "complete-query":
        result = complete_query(
            args.db_path.resolve(),
            run_id=args.run_id,
            source_key=args.source_key,
            results_seen=args.results_seen,
            jobs_ingested=args.jobs_ingested,
        )
        _print_json(result)
        return 0

    if args.command == "fail-query":
        result = fail_query(
            args.db_path.resolve(),
            run_id=args.run_id,
            source_key=args.source_key,
            message=args.message,
            results_seen=args.results_seen,
            jobs_ingested=args.jobs_ingested,
        )
        _print_json(result)
        return 0

    if args.command == "workflow-status":
        result = workflow_status(args.db_path.resolve(), run_id=args.run_id)
        _print_json(result)
        return 0

    if args.command == "record-application":
        resume_path_used, resume_label_used = _resolve_resume_snapshot(
            repo_root=args.repo_root.resolve(),
            explicit_path=args.resume_path_used,
            explicit_label=args.resume_label_used,
        )
        result = record_application(
            args.db_path.resolve(),
            job_key=args.job_key,
            status=args.status,
            confirmation_text=args.confirmation_text,
            confirmation_url=args.confirmation_url,
            error_message=args.error_message,
            run_id=args.run_id,
            resume_path_used=resume_path_used,
            resume_label_used=resume_label_used,
        )
        _print_json(result)
        return 0

    if args.command == "record-finding":
        result = record_finding(
            args.db_path.resolve(),
            job_key=args.job_key,
            run_id=args.run_id,
            application_status=args.application_status,
            stage=args.stage,
            category=args.category,
            summary=args.summary,
            detail=args.detail,
            page_url=args.page_url,
        )
        _print_json(result)
        return 0

    if args.command == "discover-next-candidate-with-codex":
        result = discover_next_candidate_with_codex(
            args.db_path.resolve(),
            repo_root=args.repo_root.resolve(),
            run_id=args.run_id,
            source_key=args.source_key,
            codex_bin=args.codex_bin,
            codex_profile=args.codex_profile,
            timeout_seconds=args.timeout_seconds,
            max_worker_retries=args.max_worker_retries,
        )
        _print_json(result)
        return 0

    if args.command == "apply-job-with-codex":
        result = apply_job_with_codex(
            args.db_path.resolve(),
            repo_root=args.repo_root.resolve(),
            run_id=args.run_id,
            job_key=args.job_key,
            codex_bin=args.codex_bin,
            codex_profile=args.codex_profile,
            timeout_seconds=args.timeout_seconds,
            max_worker_retries=args.max_worker_retries,
        )
        _print_json(result)
        return 0

    if args.command == "run-workflow":
        result = run_workflow(
            args.db_path.resolve(),
            repo_root=args.repo_root.resolve(),
            run_id=args.run_id,
            codex_bin=args.codex_bin,
            codex_profile=args.codex_profile,
            query_timeout_seconds=args.query_timeout_seconds,
            job_timeout_seconds=args.job_timeout_seconds,
            max_worker_retries=args.max_worker_retries,
            discovery_workers=args.discovery_workers,
            apply_workers=args.apply_workers,
        )
        _print_json(result)
        return 0

    if args.command == "requeue-runner-failures":
        result = requeue_runner_failures(args.db_path.resolve(), run_id=args.run_id)
        _print_json(result)
        return 0

    if args.command == "serve-dashboard":
        from .dashboard_server import serve_dashboard

        return serve_dashboard(
            repo_root=args.repo_root.resolve(),
            db_path=args.db_path.resolve(),
            host=args.host,
            port=args.port,
            reload=args.reload,
        )

    parser.error(f"Unsupported command: {args.command}")
    return 2


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _resolve_resume_snapshot(
    *,
    repo_root: Path,
    explicit_path: str | None,
    explicit_label: str | None,
) -> tuple[str | None, str | None]:
    resume_path = (explicit_path or "").strip() or None
    resume_label = (explicit_label or "").strip() or None
    if resume_path is None:
        profile = validate_profile(repo_root).to_dict().get("profile", {})
        if isinstance(profile, dict):
            profile_resume = profile.get("resume_path")
            if isinstance(profile_resume, str) and profile_resume.strip():
                resume_path = profile_resume.strip()
    if resume_label is None and resume_path is not None:
        resume_label = Path(resume_path).name
    return resume_path, resume_label
