from __future__ import annotations

from pathlib import Path
import json
import re
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from job_apply_bot.db import (
    get_query,
    get_query_skipped_results,
    ingest_job,
    next_job,
    next_query,
    prepare_run,
    start_run,
)
from job_apply_bot.supervisor import apply_job_with_codex, discover_next_candidate_with_codex, run_workflow

_CONTEXT_BLOCK_PATTERN = re.compile(r"```json\n(.*)\n```", re.DOTALL)


class SupervisorWorkflowTests(unittest.TestCase):
    def _write_valid_profile(
        self, root: Path, *, enabled_search_sites: str = "greenhouse"
    ) -> None:
        (root / "resume.pdf").write_text("stub", encoding="utf-8")
        (root / ".env").write_text(
            "\n".join(
                (
                    "APPLICANT_FULL_NAME=Tejus Ramesh",
                    "APPLICANT_EMAIL=rameshtejus@gmail.com",
                    "APPLICANT_PHONE=(480)-810-7760",
                    "APPLICANT_LOCATION=Tempe, AZ",
                    "APPLICANT_RESUME_PATH=resume.pdf",
                    "APPLICANT_US_WORK_AUTHORIZED=true",
                    "APPLICANT_REQUIRES_VISA_SPONSORSHIP=false",
                    "APPLICANT_TARGET_ROLE_KEYWORDS=software engineer, backend engineer",
                    "APPLICANT_ALLOWED_LOCATIONS=United States, Remote, Arizona",
                    f"APPLICANT_ENABLED_SEARCH_SITES={enabled_search_sites}",
                )
            ),
            encoding="utf-8",
        )
        (root / "applicant.md").write_text(
            "\n".join(
                (
                    "# Applicant Details",
                    "",
                    "## Work Authorization Notes",
                    "Authorized to work in the United States.",
                    "",
                    "## Reusable Highlights",
                    "Built backend systems and full-stack applications.",
                )
            ),
            encoding="utf-8",
        )

    def _make_fake_codex_runner(self, steps: list[object], calls: list[dict[str, object]]):
        def _fake_run(
            command: list[str],
            *,
            cwd: Path,
            input: str,
            text: bool,
            encoding: str | None = None,
            errors: str | None = None,
            capture_output: bool,
            timeout: int | None,
            check: bool,
        ) -> subprocess.CompletedProcess[str]:
            self.assertTrue(text)
            self.assertEqual(encoding, "utf-8")
            self.assertEqual(errors, "replace")
            self.assertTrue(capture_output)
            self.assertFalse(check)
            self.assertTrue(steps, "Unexpected extra Codex invocation")
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
            self.assertNotIn("--full-auto", command)

            output_path = Path(command[command.index("-o") + 1])
            schema_name = Path(command[command.index("--output-schema") + 1]).name
            match = _CONTEXT_BLOCK_PATTERN.search(input)
            self.assertIsNotNone(match, "Runtime context JSON block missing from prompt")
            context = json.loads(match.group(1))
            calls.append(
                {
                    "command": list(command),
                    "cwd": str(cwd),
                    "schema": schema_name,
                    "timeout": timeout,
                    "context": context,
                }
            )

            step = steps.pop(0)
            if isinstance(step, BaseException):
                raise step
            if not isinstance(step, dict):
                raise AssertionError(f"Unsupported fake step: {step!r}")

            expected_schema = step.get("schema")
            if expected_schema is not None:
                self.assertEqual(schema_name, expected_schema)

            bundle_artifacts = step.get("write_bundle_artifacts")
            if bundle_artifacts:
                failure_bundle = context.get("failure_bundle")
                self.assertIsInstance(
                    failure_bundle,
                    dict,
                    "Failure bundle paths were not provided in the apply worker context",
                )
                for artifact_key, contents in bundle_artifacts.items():
                    artifact_path = Path(str(failure_bundle[artifact_key]))
                    artifact_path.parent.mkdir(parents=True, exist_ok=True)
                    if isinstance(contents, bytes):
                        artifact_path.write_bytes(contents)
                    else:
                        artifact_path.write_text(str(contents), encoding="utf-8")

            if step.get("timeout"):
                if "write_json" in step:
                    output_path.write_text(
                        json.dumps(step["write_json"]), encoding="utf-8"
                    )
                elif "write_text" in step:
                    output_path.write_text(str(step["write_text"]), encoding="utf-8")
                raise subprocess.TimeoutExpired(
                    command,
                    timeout,
                    output=step.get("stdout", ""),
                    stderr=step.get("stderr", ""),
                )

            if "write_json" in step:
                output_path.write_text(
                    json.dumps(step["write_json"]), encoding="utf-8"
                )
            elif "write_text" in step:
                output_path.write_text(str(step["write_text"]), encoding="utf-8")

            return subprocess.CompletedProcess(
                command,
                step.get("returncode", 0),
                step.get("stdout", ""),
                step.get("stderr", ""),
            )

        return _fake_run

    def _read_attempt_statuses(self, db_path: Path) -> list[tuple[str, int | None]]:
        connection = sqlite3.connect(db_path)
        try:
            rows = connection.execute(
                "SELECT status, exit_code FROM codex_worker_attempts ORDER BY id ASC"
            ).fetchall()
        finally:
            connection.close()
        return [(str(status), exit_code) for status, exit_code in rows]

    def test_run_workflow_supervises_successful_query_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            calls: list[dict[str, object]] = []
            steps: list[object] = [
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {
                        "outcome": "candidate",
                        "candidate": {
                            "raw_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "canonical_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "source": "greenhouse",
                            "title": "Software Engineer",
                            "company": "Acme",
                            "location": "Remote, United States",
                            "posted_at": "2 hours ago",
                            "page_url": "https://boards.greenhouse.io/acme/jobs/12345"
                        }
                    },
                },
                {
                    "schema": "CODEX_APPLY_WORKER_SCHEMA.json",
                    "write_json": {
                        "application_status": "submitted",
                        "confirmation_text": "Application received",
                        "confirmation_url": "https://boards.greenhouse.io/acme/jobs/12345/thanks",
                        "error_message": None,
                        "findings": [],
                    },
                },
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {"outcome": "exhausted"},
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                summary = run_workflow(db_path, repo_root=root)

            self.assertEqual(summary["jobs_applied"], 1)
            self.assertEqual(summary["search_summary"]["completed_queries"], 1)
            self.assertEqual(
                [call["schema"] for call in calls],
                [
                    "CODEX_QUERY_WORKER_SCHEMA.json",
                    "CODEX_APPLY_WORKER_SCHEMA.json",
                    "CODEX_QUERY_WORKER_SCHEMA.json",
                ],
            )
            self.assertEqual([call["timeout"] for call in calls], [None, None, None])
            self.assertIn(
                "https://boards.greenhouse.io/acme/jobs/12345",
                calls[2]["context"]["current_run_seen_urls"],
            )
            self.assertFalse(
                Path(calls[1]["context"]["failure_bundle"]["bundle_dir"]).exists()
            )

    def test_run_workflow_reclaims_in_progress_query_before_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse, ashby")
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])
            first_query = next_query(db_path, run_id=run_id)
            self.assertIsNotNone(first_query)

            calls: list[dict[str, object]] = []
            steps: list[object] = [
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {"outcome": "exhausted"},
                },
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {"outcome": "exhausted"},
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                summary = run_workflow(db_path, repo_root=root, run_id=run_id)

            self.assertEqual(summary["search_summary"]["completed_queries"], 2)
            self.assertEqual(
                [call["context"]["query"]["source_key"] for call in calls],
                ["greenhouse", "ashby"],
            )

    def test_run_workflow_uses_explicit_worker_timeouts_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            calls: list[dict[str, object]] = []
            steps: list[object] = [
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {
                        "outcome": "candidate",
                        "candidate": {
                            "raw_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "canonical_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "source": "greenhouse",
                            "title": "Software Engineer",
                            "company": "Acme",
                            "location": "Remote, United States",
                            "posted_at": "2 hours ago",
                            "page_url": "https://boards.greenhouse.io/acme/jobs/12345",
                        },
                    },
                },
                {
                    "schema": "CODEX_APPLY_WORKER_SCHEMA.json",
                    "write_json": {
                        "application_status": "submitted",
                        "confirmation_text": "Application received",
                        "confirmation_url": "https://boards.greenhouse.io/acme/jobs/12345/thanks",
                        "error_message": None,
                        "findings": [],
                    },
                },
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {"outcome": "exhausted"},
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                summary = run_workflow(
                    db_path,
                    repo_root=root,
                    query_timeout_seconds=17,
                    job_timeout_seconds=29,
                )

            self.assertEqual(summary["jobs_applied"], 1)
            self.assertEqual([call["timeout"] for call in calls], [17, 29, 17])

    def test_run_workflow_drains_backlog_before_claiming_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])
            ingested = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source="greenhouse",
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-07T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )
            self.assertEqual(ingested.status, "ready_to_apply")

            calls: list[dict[str, object]] = []
            steps: list[object] = [
                {
                    "schema": "CODEX_APPLY_WORKER_SCHEMA.json",
                    "write_json": {
                        "application_status": "submitted",
                        "confirmation_text": "Applied",
                        "confirmation_url": "https://boards.greenhouse.io/acme/jobs/12345/thanks",
                        "error_message": None,
                        "findings": [],
                    },
                },
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {"outcome": "exhausted"},
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                summary = run_workflow(db_path, repo_root=root, run_id=run_id)

            self.assertEqual(summary["jobs_applied"], 1)
            self.assertEqual(
                [call["schema"] for call in calls],
                ["CODEX_APPLY_WORKER_SCHEMA.json", "CODEX_QUERY_WORKER_SCHEMA.json"],
            )

    def test_run_workflow_retries_discovery_then_fails_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            calls: list[dict[str, object]] = []
            steps: list[object] = [
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_text": "{not valid json",
                },
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_text": "{still not valid json",
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                summary = run_workflow(db_path, repo_root=root)

            self.assertEqual(summary["search_summary"]["failed_queries"], 1)
            self.assertEqual(summary["search_summary"]["pending_queries"], 0)
            self.assertEqual(summary["search_summary"]["in_progress_queries"], 0)
            self.assertTrue(summary["finished_at"])
            self.assertEqual(
                self._read_attempt_statuses(db_path),
                [("invalid_output", 0), ("invalid_output", 0)],
            )

    def test_apply_job_with_codex_retries_then_records_worker_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            run = start_run(db_path)
            run_id = int(run["id"])
            ingested = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source="greenhouse",
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-07T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )
            job = next_job(db_path, mark_applying=True)
            self.assertIsNotNone(job)

            calls: list[dict[str, object]] = []
            steps: list[object] = [
                {
                    "schema": "CODEX_APPLY_WORKER_SCHEMA.json",
                    "write_text": "{bad json",
                },
                {
                    "schema": "CODEX_APPLY_WORKER_SCHEMA.json",
                    "write_text": "{still bad json",
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                result = apply_job_with_codex(
                    db_path,
                    repo_root=root,
                    run_id=run_id,
                    job_key=ingested.job_key,
                )

            self.assertEqual(result["application"]["status"], "failed")
            self.assertEqual(result["findings"][0]["category"], "codex_worker_error")
            failure_bundle_dir = Path(calls[-1]["context"]["failure_bundle"]["bundle_dir"])
            self.assertTrue((failure_bundle_dir / "runtime_context.json").exists())
            self.assertTrue((failure_bundle_dir / "prompt.txt").exists())
            self.assertTrue((failure_bundle_dir / "failure_manifest.json").exists())
            self.assertIn(str(failure_bundle_dir), result["findings"][0]["detail"])
            self.assertEqual(
                self._read_attempt_statuses(db_path),
                [("invalid_output", 0), ("invalid_output", 0)],
            )

    def test_discovery_skip_results_persist_and_feed_next_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])
            query = next_query(db_path, run_id=run_id)
            self.assertIsNotNone(query)

            calls: list[dict[str, object]] = []
            skipped_url = "https://boards.greenhouse.io/acme/jobs/search"
            steps: list[object] = [
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {
                        "outcome": "skip_result",
                        "result_url": skipped_url,
                        "skip_reason": "Listing page requires authentication before child jobs are visible.",
                    },
                },
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {"outcome": "exhausted"},
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                first = discover_next_candidate_with_codex(
                    db_path,
                    repo_root=root,
                    run_id=run_id,
                    source_key=str(query["source_key"]),
                )
                checkpointed = get_query(
                    db_path, run_id=run_id, source_key=str(query["source_key"])
                )
                second = discover_next_candidate_with_codex(
                    db_path,
                    repo_root=root,
                    run_id=run_id,
                    source_key=str(query["source_key"]),
                )

            self.assertEqual(first["outcome"], "skip_result")
            self.assertIsNotNone(checkpointed)
            self.assertEqual(checkpointed["status"], "in_progress")
            self.assertEqual(checkpointed["results_seen"], 1)
            self.assertEqual(checkpointed["jobs_ingested"], 0)
            self.assertEqual(second["outcome"], "exhausted")
            skipped_rows = get_query_skipped_results(
                db_path, run_id=run_id, source_key=str(query["source_key"])
            )
            self.assertEqual(skipped_rows[0]["url"], skipped_url)
            self.assertEqual(
                calls[1]["context"]["query_skipped_results"][0]["url"], skipped_url
            )

    def test_candidate_ingestion_checkpoints_query_progress_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse")
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])
            source_key = str(prepared["queries"][0]["source_key"])

            calls: list[dict[str, object]] = []
            steps: list[object] = [
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {
                        "outcome": "candidate",
                        "candidate": {
                            "raw_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "canonical_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "source": "greenhouse",
                            "title": "Software Engineer",
                            "company": "Acme",
                            "location": "Remote, United States",
                            "posted_at": "2 hours ago",
                            "page_url": "https://boards.greenhouse.io/acme/jobs/12345",
                        },
                    },
                }
            ]

            def _assert_checkpointed(*args: object, **kwargs: object) -> None:
                checkpointed = get_query(db_path, run_id=run_id, source_key=source_key)
                self.assertIsNotNone(checkpointed)
                self.assertEqual(checkpointed["status"], "in_progress")
                self.assertEqual(checkpointed["results_seen"], 1)
                self.assertEqual(checkpointed["jobs_ingested"], 1)
                raise RuntimeError("stop after checkpoint")

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ), patch(
                "job_apply_bot.supervisor.apply_job_with_codex",
                side_effect=_assert_checkpointed,
            ):
                with self.assertRaisesRegex(RuntimeError, "stop after checkpoint"):
                    run_workflow(db_path, repo_root=root, run_id=run_id)

            checkpointed = get_query(db_path, run_id=run_id, source_key=source_key)
            self.assertIsNotNone(checkpointed)
            self.assertEqual(checkpointed["results_seen"], 1)
            self.assertEqual(checkpointed["jobs_ingested"], 1)

    def test_apply_job_with_codex_records_findings_for_unsuccessful_results(self) -> None:
        unsuccessful_statuses = ("blocked", "incomplete", "failed")

        for status in unsuccessful_statuses:
            with self.subTest(status=status):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    db_path = root / "jobs.sqlite3"
                    self._write_valid_profile(root)
                    run = start_run(db_path)
                    run_id = int(run["id"])
                    ingested = ingest_job(
                        db_path,
                        run_id=run_id,
                        raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                        canonical_url=None,
                        source="greenhouse",
                        title="Software Engineer",
                        company="Acme",
                        location="Remote, United States",
                        posted_at="2 hours ago",
                        discovered_at="2026-04-07T12:00:00Z",
                        role_keywords=[],
                        allowed_locations=[],
                    )
                    job = next_job(db_path, mark_applying=True)
                    self.assertIsNotNone(job)

                    calls: list[dict[str, object]] = []
                    steps: list[object] = [
                        {
                            "schema": "CODEX_APPLY_WORKER_SCHEMA.json",
                            "write_json": {
                                "application_status": status,
                                "confirmation_text": None,
                                "confirmation_url": None,
                                "error_message": f"{status} outcome",
                                "findings": [
                                    {
                                        "stage": "submit",
                                        "category": f"{status}_category",
                                        "summary": f"{status} summary",
                                        "detail": "detail",
                                        "page_url": "https://boards.greenhouse.io/acme/jobs/12345",
                                    }
                                ],
                            },
                            "write_bundle_artifacts": {
                                "playwright_snapshot_path": "# Snapshot",
                                "console_path": '{"level":"info"}',
                            },
                        }
                    ]

                    with patch(
                        "job_apply_bot.supervisor.subprocess.run",
                        side_effect=self._make_fake_codex_runner(steps, calls),
                    ):
                        result = apply_job_with_codex(
                            db_path,
                            repo_root=root,
                            run_id=run_id,
                            job_key=ingested.job_key,
                        )

                    self.assertEqual(result["application"]["status"], status)
                    self.assertEqual(len(result["findings"]), 1)
                    self.assertEqual(result["findings"][0]["category"], f"{status}_category")
                    failure_bundle_dir = Path(
                        calls[0]["context"]["failure_bundle"]["bundle_dir"]
                    )
                    self.assertTrue((failure_bundle_dir / "runtime_context.json").exists())
                    self.assertTrue((failure_bundle_dir / "prompt.txt").exists())
                    self.assertTrue((failure_bundle_dir / "failure_manifest.json").exists())
                    self.assertTrue((failure_bundle_dir / "playwright_snapshot.md").exists())
                    self.assertTrue((failure_bundle_dir / "console.json").exists())
                    self.assertIn(str(failure_bundle_dir), result["findings"][0]["detail"])

    def test_discovery_timeout_with_valid_output_file_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])
            query = next_query(db_path, run_id=run_id)
            self.assertIsNotNone(query)

            calls: list[dict[str, object]] = []
            steps: list[object] = [
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "timeout": True,
                    "write_json": {
                        "outcome": "candidate",
                        "candidate": {
                            "raw_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "canonical_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "source": "greenhouse",
                            "title": "Software Engineer",
                            "company": "Acme",
                            "location": "Remote, United States",
                            "posted_at": "2 hours ago",
                            "page_url": "https://boards.greenhouse.io/acme/jobs/12345",
                        },
                        "result_url": None,
                        "skip_reason": None,
                        "error_message": None,
                    },
                }
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                result = discover_next_candidate_with_codex(
                    db_path,
                    repo_root=root,
                    run_id=run_id,
                    source_key=str(query["source_key"]),
                )

            self.assertEqual(result["outcome"], "candidate")
            self.assertEqual(
                self._read_attempt_statuses(db_path),
                [("succeeded", None)],
            )

    def test_run_workflow_tolerates_missing_worker_streams(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            calls: list[dict[str, object]] = []
            steps: list[object] = [
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {
                        "outcome": "candidate",
                        "candidate": {
                            "raw_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "canonical_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "source": "greenhouse",
                            "title": "Software Engineer",
                            "company": "Acme",
                            "location": "Remote, United States",
                            "posted_at": "2 hours ago",
                            "page_url": "https://boards.greenhouse.io/acme/jobs/12345",
                        },
                        "result_url": None,
                        "skip_reason": None,
                        "error_message": None,
                    },
                    "stdout": None,
                    "stderr": None,
                },
                {
                    "schema": "CODEX_APPLY_WORKER_SCHEMA.json",
                    "write_json": {
                        "application_status": "submitted",
                        "confirmation_text": "Application received",
                        "confirmation_url": "https://boards.greenhouse.io/acme/jobs/12345/thanks",
                        "error_message": None,
                        "findings": [],
                    },
                    "stdout": None,
                    "stderr": None,
                },
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {
                        "outcome": "exhausted",
                        "candidate": None,
                        "result_url": None,
                        "skip_reason": None,
                        "error_message": None,
                    },
                    "stdout": None,
                    "stderr": None,
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                summary = run_workflow(db_path, repo_root=root)

            self.assertEqual(summary["jobs_applied"], 1)
            self.assertEqual(summary["search_summary"]["completed_queries"], 1)

    def test_run_workflow_resumes_checkpointed_query_progress_after_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse, ashby")
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])

            first_calls: list[dict[str, object]] = []
            first_steps: list[object] = [
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {
                        "outcome": "candidate",
                        "candidate": {
                            "raw_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "canonical_url": "https://boards.greenhouse.io/acme/jobs/12345",
                            "source": "greenhouse",
                            "title": "Software Engineer",
                            "company": "Acme",
                            "location": "Remote, United States",
                            "posted_at": "2 hours ago",
                            "page_url": "https://boards.greenhouse.io/acme/jobs/12345",
                        },
                    },
                },
                {
                    "schema": "CODEX_APPLY_WORKER_SCHEMA.json",
                    "write_json": {
                        "application_status": "submitted",
                        "confirmation_text": "Application received",
                        "confirmation_url": "https://boards.greenhouse.io/acme/jobs/12345/thanks",
                        "error_message": None,
                        "findings": [],
                    },
                },
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {
                        "outcome": "skip_result",
                        "result_url": "https://boards.greenhouse.io/acme/jobs/search",
                        "skip_reason": "Listing page requires authentication before child jobs are visible.",
                    },
                },
                RuntimeError("worker crashed mid-query"),
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(first_steps, first_calls),
            ):
                with self.assertRaisesRegex(RuntimeError, "worker crashed mid-query"):
                    run_workflow(db_path, repo_root=root, run_id=run_id)

            greenhouse_after_crash = get_query(db_path, run_id=run_id, source_key="greenhouse")
            ashby_after_crash = get_query(db_path, run_id=run_id, source_key="ashby")
            self.assertIsNotNone(greenhouse_after_crash)
            self.assertIsNotNone(ashby_after_crash)
            self.assertEqual(greenhouse_after_crash["status"], "in_progress")
            self.assertEqual(greenhouse_after_crash["results_seen"], 2)
            self.assertEqual(greenhouse_after_crash["jobs_ingested"], 1)
            self.assertEqual(ashby_after_crash["status"], "pending")

            resumed_calls: list[dict[str, object]] = []
            resumed_steps: list[object] = [
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {
                        "outcome": "candidate",
                        "candidate": {
                            "raw_url": "https://boards.greenhouse.io/acme/jobs/67890",
                            "canonical_url": "https://boards.greenhouse.io/acme/jobs/67890",
                            "source": "greenhouse",
                            "title": "Backend Engineer",
                            "company": "Acme",
                            "location": "Remote, United States",
                            "posted_at": "1 hour ago",
                            "page_url": "https://boards.greenhouse.io/acme/jobs/67890",
                        },
                    },
                },
                {
                    "schema": "CODEX_APPLY_WORKER_SCHEMA.json",
                    "write_json": {
                        "application_status": "submitted",
                        "confirmation_text": "Application received",
                        "confirmation_url": "https://boards.greenhouse.io/acme/jobs/67890/thanks",
                        "error_message": None,
                        "findings": [],
                    },
                },
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {"outcome": "exhausted"},
                },
                {
                    "schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": {"outcome": "exhausted"},
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(resumed_steps, resumed_calls),
            ):
                summary = run_workflow(db_path, repo_root=root, run_id=run_id)

            greenhouse_final = get_query(db_path, run_id=run_id, source_key="greenhouse")
            ashby_final = get_query(db_path, run_id=run_id, source_key="ashby")
            self.assertIsNotNone(greenhouse_final)
            self.assertIsNotNone(ashby_final)
            self.assertEqual(greenhouse_final["status"], "completed")
            self.assertEqual(greenhouse_final["results_seen"], 3)
            self.assertEqual(greenhouse_final["jobs_ingested"], 2)
            self.assertEqual(ashby_final["status"], "completed")
            query_source_keys = [
                call["context"]["query"]["source_key"]
                for call in resumed_calls
                if call["schema"] == "CODEX_QUERY_WORKER_SCHEMA.json"
            ]
            self.assertEqual(query_source_keys, ["greenhouse", "greenhouse", "ashby"])
            self.assertEqual(summary["search_summary"]["completed_queries"], 2)

    def test_query_worker_prompt_contract_mentions_manual_camoufox_wait(self) -> None:
        prompt_path = Path(__file__).resolve().parents[1] / "PROMPTS" / "CODEX_QUERY_WORKER_PROMPT.md"
        prompt_text = prompt_path.read_text(encoding="utf-8")

        self.assertIn("visible Camoufox window", prompt_text)
        self.assertIn("ASCII status text", prompt_text)
        self.assertIn("no overall timeout", prompt_text)
        self.assertIn("continue the same discovery step", prompt_text)
        self.assertIn("Use `query_failed` only", prompt_text)

    def test_apply_worker_prompt_contract_mentions_manual_camoufox_wait(self) -> None:
        prompt_path = Path(__file__).resolve().parents[1] / "PROMPTS" / "CODEX_APPLY_WORKER_PROMPT.md"
        prompt_text = prompt_path.read_text(encoding="utf-8")

        self.assertIn("visible Camoufox window", prompt_text)
        self.assertIn("ASCII status text", prompt_text)
        self.assertIn("no overall timeout", prompt_text)
        self.assertIn("continue the current application in that same Camoufox session", prompt_text)
        self.assertIn("Do not use `blocked` only because a CAPTCHA appeared", prompt_text)

    def test_apply_worker_schema_requires_all_finding_fields(self) -> None:
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "PROMPTS"
            / "CODEX_APPLY_WORKER_SCHEMA.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        finding_items = schema["properties"]["findings"]["items"]
        self.assertEqual(
            sorted(finding_items["required"]),
            sorted(finding_items["properties"].keys()),
        )


if __name__ == "__main__":
    unittest.main()
