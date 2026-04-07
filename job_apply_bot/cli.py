from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import finish_run, ingest_job, next_job, record_application, start_run
from .profile import parse_csv, validate_profile

DEFAULT_DB_PATH = Path("data/job_apply_bot.sqlite3")


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

    finish_parser = subparsers.add_parser("finish-run", help="Finalize an existing run.")
    finish_parser.add_argument("--run-id", type=int, required=True)

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

    next_parser = subparsers.add_parser("next-job", help="Return the next ready job.")
    next_parser.add_argument(
        "--mark-applying",
        action="store_true",
        help="Mark the returned job as applying.",
    )

    record_parser = subparsers.add_parser(
        "record-application", help="Persist an application result."
    )
    record_parser.add_argument("--job-key", required=True)
    record_parser.add_argument(
        "--status",
        required=True,
        choices=["submitted", "failed", "incomplete", "duplicate_skipped"],
    )
    record_parser.add_argument("--confirmation-text")
    record_parser.add_argument("--confirmation-url")
    record_parser.add_argument("--error-message")
    record_parser.add_argument("--run-id", type=int)

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

    if args.command == "finish-run":
        _print_json(finish_run(args.db_path.resolve(), args.run_id))
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
        )
        _print_json(result.to_dict())
        return 0

    if args.command == "next-job":
        result = next_job(args.db_path.resolve(), mark_applying=args.mark_applying)
        _print_json(result)
        return 0

    if args.command == "record-application":
        result = record_application(
            args.db_path.resolve(),
            job_key=args.job_key,
            status=args.status,
            confirmation_text=args.confirmation_text,
            confirmation_url=args.confirmation_url,
            error_message=args.error_message,
            run_id=args.run_id,
        )
        _print_json(result)
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))
