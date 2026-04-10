from __future__ import annotations

from pathlib import Path
import json
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from job_apply_bot.db import (
    claim_search_result,
    complete_query,
    ensure_worker_session,
    get_query,
    insert_search_results,
    list_search_results,
    prepare_run,
    start_run,
    update_search_result_status,
    update_worker_session,
    workflow_status,
)
from job_apply_bot.supervisor import (
    _validate_query_worker_payload,
    apply_job_with_codex,
    discover_next_candidate_with_codex,
    run_workflow,
)


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

    def _query_results_page(
        self,
        *results: dict[str, object],
        next_page: dict[str, object] | None,
    ) -> dict[str, object]:
        return {
            "outcome": "results_page",
            "results": list(results),
            "next_page": next_page,
            "query_error": None,
        }

    def _query_exhausted(self) -> dict[str, object]:
        return {
            "outcome": "exhausted",
            "results": [],
            "next_page": None,
            "query_error": None,
        }

    def _query_failed(self, message: str) -> dict[str, object]:
        return {
            "outcome": "query_failed",
            "results": [],
            "next_page": None,
            "query_error": message,
        }

    def _result_item(
        self,
        url: str,
        *,
        title: str | None = None,
        snippet: str | None = None,
        visible_date: str | None = "2 hours ago",
        page_number: int = 1,
        rank: int = 1,
    ) -> dict[str, object]:
        return {
            "url": url,
            "title": title,
            "snippet": snippet,
            "visible_date": visible_date,
            "page_number": page_number,
            "rank": rank,
        }

    def _resolved_job(
        self,
        *,
        raw_url: str,
        source: str = "greenhouse",
        title: str = "Software Engineer",
        company: str = "Acme",
        location: str = "Remote, United States",
        posted_at: str = "2 hours ago",
    ) -> dict[str, object]:
        return {
            "outcome": "resolved_job",
            "job": {
                "raw_url": raw_url,
                "canonical_url": raw_url,
                "source": source,
                "title": title,
                "company": company,
                "location": location,
                "posted_at": posted_at,
                "page_url": raw_url,
            },
            "child_results": [],
            "skip_reason": None,
            "error_message": None,
        }

    def _expanded(self, *child_results: dict[str, object]) -> dict[str, object]:
        return {
            "outcome": "expanded",
            "job": None,
            "child_results": list(child_results),
            "skip_reason": None,
            "error_message": None,
        }

    def _apply_result(self, status: str = "submitted") -> dict[str, object]:
        return {
            "application_status": status,
            "confirmation_text": "Application received" if status == "submitted" else None,
            "confirmation_url": "https://example.com/thanks" if status == "submitted" else None,
            "error_message": None if status == "submitted" else f"{status} outcome",
            "findings": [] if status == "submitted" else [
                {
                    "stage": "submit",
                    "category": f"{status}_category",
                    "summary": f"{status} summary",
                    "detail": "detail",
                    "page_url": "https://example.com",
                }
            ],
        }

    def _make_fake_codex_runner(self, steps: list[dict[str, object]], calls: list[dict[str, object]]):
        lock = threading.Lock()

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

            with lock:
                self.assertTrue(steps, "Unexpected extra Codex invocation")
                step = steps.pop(0)

            output_path = Path(command[command.index("-o") + 1])
            schema_name = None
            if "--output-schema" in command:
                schema_name = Path(command[command.index("--output-schema") + 1]).name
            calls.append(
                {
                    "command": list(command),
                    "cwd": str(cwd),
                    "schema": schema_name,
                    "timeout": timeout,
                    "prompt": input,
                }
            )
            if step.get("expected_schema") is not None:
                self.assertEqual(schema_name, step["expected_schema"])
            if step.get("expect_resume_thread") is not None:
                self.assertIn("resume", command)
                resume_index = command.index("resume")
                self.assertEqual(command[resume_index + 1], step["expect_resume_thread"])

            if "write_json" in step:
                output_path.write_text(json.dumps(step["write_json"]), encoding="utf-8")

            stdout_lines = []
            if step.get("thread_id"):
                stdout_lines.append(
                    json.dumps({"type": "thread.started", "thread_id": step["thread_id"]})
                )
            if step.get("stdout_lines"):
                stdout_lines.extend(step["stdout_lines"])

            if step.get("timeout"):
                raise subprocess.TimeoutExpired(
                    command,
                    timeout,
                    output="\n".join(stdout_lines),
                    stderr=step.get("stderr", ""),
                )

            return subprocess.CompletedProcess(
                command,
                step.get("returncode", 0),
                "\n".join(stdout_lines) if stdout_lines else step.get("stdout", ""),
                step.get("stderr", ""),
            )

        return _fake_run

    def test_query_worker_schema_and_validator_match_batch_contract(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "PROMPTS" / "CODEX_QUERY_WORKER_SCHEMA.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["properties"]["outcome"]["enum"],
            ["results_page", "exhausted", "query_failed"],
        )
        valid = self._query_results_page(
            self._result_item("https://boards.greenhouse.io/acme/jobs/1"),
            next_page={"page_number": 2},
        )
        _validate_query_worker_payload(valid)
        with self.assertRaisesRegex(ValueError, "must include at least one visible result"):
            _validate_query_worker_payload(self._query_results_page(next_page=None))

    def test_query_worker_prompt_mentions_ascii_camoufox_wait(self) -> None:
        prompt_path = Path(__file__).resolve().parents[1] / "PROMPTS" / "CODEX_QUERY_WORKER_PROMPT.md"
        prompt_text = prompt_path.read_text(encoding="utf-8")
        self.assertIn("visible Camoufox window", prompt_text)
        self.assertIn("ASCII status text", prompt_text)
        self.assertIn("Harvest the full visible SERP page only", prompt_text)

    def test_discovery_turn_stores_page_results_and_resumes_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])
            source_key = str(prepared["queries"][0]["source_key"])
            calls: list[dict[str, object]] = []
            steps = [
                {
                    "expected_schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "thread_id": "thread-discovery-1",
                    "write_json": self._query_results_page(
                        self._result_item("https://boards.greenhouse.io/acme/jobs/1", rank=1),
                        self._result_item("https://boards.greenhouse.io/acme/jobs/2", rank=2),
                        next_page={"page_number": 2},
                    ),
                },
                {
                    "expect_resume_thread": "thread-discovery-1",
                    "write_json": self._query_results_page(
                        self._result_item("https://boards.greenhouse.io/acme/jobs/1", rank=1),
                        self._result_item("https://boards.greenhouse.io/acme/jobs/2", rank=2),
                        next_page={"page_number": 3},
                    ),
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
                    source_key=source_key,
                )
                second = discover_next_candidate_with_codex(
                    db_path,
                    repo_root=root,
                    run_id=run_id,
                    source_key=source_key,
                )

            self.assertEqual(first["outcome"], "results_page")
            self.assertEqual(second["outcome"], "results_page")
            query = get_query(db_path, run_id=run_id, source_key=source_key)
            self.assertIsNotNone(query)
            self.assertEqual(query["status"], "in_progress")
            self.assertEqual(query["results_seen"], 4)
            self.assertEqual(json.loads(str(query["cursor_json"])), {"page_number": 3})
            self.assertEqual(len(list_search_results(db_path, run_id=run_id, source_key=source_key)), 2)
            self.assertIn("resume", calls[1]["command"])

    def test_apply_pool_claims_distinct_search_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])
            source_key = str(prepared["queries"][0]["source_key"])
            insert_search_results(
                db_path,
                run_id=run_id,
                source_key=source_key,
                origin_kind="google_result",
                results=[
                    self._result_item("https://boards.greenhouse.io/acme/jobs/1", rank=1),
                    self._result_item("https://boards.greenhouse.io/acme/jobs/2", rank=2),
                ],
            )
            first = claim_search_result(db_path, run_id=run_id, claimed_by="apply-1")
            second = claim_search_result(db_path, run_id=run_id, claimed_by="apply-2")
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(first["status"], "processing")
            self.assertEqual(second["status"], "processing")

    def test_workflow_status_reports_search_result_and_worker_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])
            source_key = str(prepared["queries"][0]["source_key"])
            insert_search_results(
                db_path,
                run_id=run_id,
                source_key=source_key,
                origin_kind="google_result",
                results=[self._result_item("https://boards.greenhouse.io/acme/jobs/1")],
            )
            ensure_worker_session(db_path, run_id=run_id, worker_type="discovery", slot_key=source_key)
            ensure_worker_session(db_path, run_id=run_id, worker_type="apply", slot_key="apply-1")
            update_worker_session(
                db_path,
                run_id=run_id,
                worker_type="discovery",
                slot_key=source_key,
                status="running",
                thread_id="discovery-thread",
            )
            update_worker_session(
                db_path,
                run_id=run_id,
                worker_type="apply",
                slot_key="apply-1",
                status="running",
                thread_id="apply-thread",
            )

            status = workflow_status(db_path, run_id=run_id)
            self.assertEqual(status["search_results_pending"], 1)
            self.assertEqual(status["search_results_processing"], 0)
            self.assertEqual(status["discovery_workers_running"], 1)
            self.assertEqual(status["apply_workers_running"], 1)
            self.assertFalse(status["drained"])

            row = claim_search_result(db_path, run_id=run_id, claimed_by="apply-1")
            self.assertIsNotNone(row)
            update_search_result_status(
                db_path,
                result_id=int(row["id"]),
                status="filtered_out",
                reason="filtered",
            )
            complete_query(db_path, run_id=run_id, source_key=source_key, results_seen=1, jobs_ingested=0)
            update_worker_session(
                db_path,
                run_id=run_id,
                worker_type="discovery",
                slot_key=source_key,
                status="idle",
            )
            update_worker_session(
                db_path,
                run_id=run_id,
                worker_type="apply",
                slot_key="apply-1",
                status="idle",
            )
            drained = workflow_status(db_path, run_id=run_id)
            self.assertTrue(drained["drained"])

    def test_run_workflow_pipelines_query_results_listing_expansion_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            calls: list[dict[str, object]] = []
            steps = [
                {
                    "expected_schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "thread_id": "discovery-thread",
                    "write_json": self._query_results_page(
                        self._result_item("https://boards.greenhouse.io/acme/jobs/1", rank=1),
                        self._result_item("https://boards.greenhouse.io/acme/jobs/listing", rank=2),
                        next_page=None,
                    ),
                },
                {
                    "expected_schema": "CODEX_RESOLVE_WORKER_SCHEMA.json",
                    "thread_id": "apply-thread",
                    "write_json": self._resolved_job(
                        raw_url="https://boards.greenhouse.io/acme/jobs/1"
                    ),
                },
                {
                    "expect_resume_thread": "apply-thread",
                    "write_json": self._apply_result("submitted"),
                },
                {
                    "expect_resume_thread": "apply-thread",
                    "write_json": self._expanded(
                        self._result_item("https://boards.greenhouse.io/acme/jobs/2", rank=1)
                    ),
                },
                {
                    "expect_resume_thread": "apply-thread",
                    "write_json": self._resolved_job(
                        raw_url="https://boards.greenhouse.io/acme/jobs/2",
                        title="Backend Engineer",
                    ),
                },
                {
                    "expect_resume_thread": "apply-thread",
                    "write_json": self._apply_result("submitted"),
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                summary = run_workflow(
                    db_path,
                    repo_root=root,
                    discovery_workers=1,
                    apply_workers=1,
                )

            self.assertEqual(summary["jobs_applied"], 2)
            self.assertEqual(summary["search_summary"]["completed_queries"], 1)
            rows = list_search_results(db_path, run_id=int(summary["id"]))
            statuses = {str(row["url"]): str(row["status"]) for row in rows}
            self.assertEqual(statuses["https://boards.greenhouse.io/acme/jobs/1"], "applied")
            self.assertEqual(statuses["https://boards.greenhouse.io/acme/jobs/listing"], "expanded")
            self.assertEqual(statuses["https://boards.greenhouse.io/acme/jobs/2"], "applied")

    def test_run_workflow_isolates_query_failure_from_other_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse, ashby")
            steps = [
                {
                    "expected_schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": self._query_failed("google blocked greenhouse"),
                },
                {
                    "expected_schema": "CODEX_QUERY_WORKER_SCHEMA.json",
                    "write_json": self._query_results_page(
                        self._result_item("https://jobs.ashbyhq.com/acme/123", rank=1),
                        next_page=None,
                    ),
                },
                {
                    "expected_schema": "CODEX_RESOLVE_WORKER_SCHEMA.json",
                    "write_json": self._resolved_job(
                        raw_url="https://jobs.ashbyhq.com/acme/123",
                        source="ashby",
                    ),
                },
                {
                    "write_json": self._apply_result("submitted"),
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, []),
            ):
                summary = run_workflow(
                    db_path,
                    repo_root=root,
                    discovery_workers=1,
                    apply_workers=1,
                )

            self.assertEqual(summary["jobs_applied"], 1)
            self.assertEqual(summary["search_summary"]["failed_queries"], 1)
            self.assertEqual(summary["search_summary"]["completed_queries"], 1)

    def test_resume_existing_processing_result_requeues_and_uses_saved_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root)
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])
            source_key = str(prepared["queries"][0]["source_key"])
            complete_query(db_path, run_id=run_id, source_key=source_key, results_seen=1, jobs_ingested=0)
            insert_search_results(
                db_path,
                run_id=run_id,
                source_key=source_key,
                origin_kind="google_result",
                results=[self._result_item("https://boards.greenhouse.io/acme/jobs/3")],
            )
            row = claim_search_result(db_path, run_id=run_id, claimed_by="apply-1")
            self.assertIsNotNone(row)
            update_worker_session(
                db_path,
                run_id=run_id,
                worker_type="apply",
                slot_key="apply-1",
                status="running",
                thread_id="saved-apply-thread",
            )
            calls: list[dict[str, object]] = []
            steps = [
                {
                    "expect_resume_thread": "saved-apply-thread",
                    "write_json": self._resolved_job(
                        raw_url="https://boards.greenhouse.io/acme/jobs/3"
                    ),
                },
                {
                    "expect_resume_thread": "saved-apply-thread",
                    "write_json": self._apply_result("submitted"),
                },
            ]

            with patch(
                "job_apply_bot.supervisor.subprocess.run",
                side_effect=self._make_fake_codex_runner(steps, calls),
            ):
                summary = run_workflow(
                    db_path,
                    repo_root=root,
                    run_id=run_id,
                    discovery_workers=1,
                    apply_workers=1,
                )

            self.assertEqual(summary["jobs_applied"], 1)
            self.assertIn("resume", calls[0]["command"])
