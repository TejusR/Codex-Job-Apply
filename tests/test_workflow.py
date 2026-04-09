from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from job_apply_bot.db import (
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
from job_apply_bot.profile import validate_profile


class WorkflowStateTests(unittest.TestCase):
    def _write_valid_profile(
        self, root: Path, *, enabled_search_sites: str = "greenhouse, ashby"
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

    def test_unverifiable_freshness_can_be_kept_ready_to_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite3"
            run = start_run(db_path)
            run_id = int(run["id"])

            result = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="New",
                discovered_at="2026-04-07T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
                allow_unverifiable_freshness=True,
            )

            self.assertEqual(result.action, "ready_to_apply_unverifiable_date")
            self.assertEqual(result.status, "ready_to_apply")
            self.assertEqual(result.status_reason, "unverified_freshness_allowed")

            job = next_job(db_path, mark_applying=False)
            self.assertIsNotNone(job)
            self.assertEqual(job["job_key"], result.job_key)
            self.assertEqual(job["status_reason"], "unverified_freshness_allowed")

    def test_ingest_duplicate_and_record_application(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite3"
            run = start_run(db_path)
            run_id = int(run["id"])

            first = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345?utm_source=google",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-07T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )
            second = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-07T12:05:00Z",
                role_keywords=[],
                allowed_locations=[],
            )

            self.assertEqual(first.action, "ready_to_apply")
            self.assertEqual(second.action, "duplicate_same_run")

            job = next_job(db_path, mark_applying=True)
            self.assertIsNotNone(job)

            application = record_application(
                db_path,
                job_key=first.job_key,
                status="submitted",
                confirmation_text="Application received",
                confirmation_url="https://example.com/thanks",
                error_message=None,
                run_id=run_id,
            )
            summary = finish_run(db_path, run_id)

            self.assertEqual(application["status"], "submitted")
            self.assertEqual(summary["jobs_applied"], 1)
            self.assertEqual(summary["jobs_skipped_duplicate"], 1)

    def test_failed_jobs_can_be_rediscovered_on_later_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite3"
            first_run = start_run(db_path)
            first_run_id = int(first_run["id"])

            first = ingest_job(
                db_path,
                run_id=first_run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-07T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )
            record_application(
                db_path,
                job_key=first.job_key,
                status="failed",
                confirmation_text=None,
                confirmation_url="https://boards.greenhouse.io/acme/jobs/12345",
                error_message="Submission outcome could not be verified.",
                run_id=first_run_id,
            )

            second_run = start_run(db_path)
            second_run_id = int(second_run["id"])
            rediscovered = ingest_job(
                db_path,
                run_id=second_run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="1 hour ago",
                discovered_at="2026-04-07T13:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )

            self.assertEqual(rediscovered.action, "ready_to_apply")
            job = next_job(db_path, mark_applying=False)
            self.assertIsNotNone(job)
            self.assertEqual(job["job_key"], first.job_key)
            self.assertEqual(job["status"], "ready_to_apply")

    def test_terminal_application_statuses_stay_duplicates(self) -> None:
        terminal_statuses = ("submitted", "incomplete", "duplicate_skipped", "blocked")

        for status in terminal_statuses:
            with self.subTest(status=status):
                with tempfile.TemporaryDirectory() as temp_dir:
                    db_path = Path(temp_dir) / "jobs.sqlite3"
                    first_run = start_run(db_path)
                    first_run_id = int(first_run["id"])

                    ingested = ingest_job(
                        db_path,
                        run_id=first_run_id,
                        raw_url="https://jobs.ashbyhq.com/acme/1234/application",
                        canonical_url=None,
                        source=None,
                        title="Software Engineer",
                        company="Acme",
                        location="Remote, United States",
                        posted_at="2 hours ago",
                        discovered_at="2026-04-07T12:00:00Z",
                        role_keywords=[],
                        allowed_locations=[],
                    )
                    record_application(
                        db_path,
                        job_key=ingested.job_key,
                        status=status,
                        confirmation_text=None,
                        confirmation_url="https://jobs.ashbyhq.com/acme/1234/application",
                        error_message=f"{status} outcome",
                        run_id=first_run_id,
                    )

                    second_run = start_run(db_path)
                    second_run_id = int(second_run["id"])
                    duplicate = ingest_job(
                        db_path,
                        run_id=second_run_id,
                        raw_url="https://jobs.ashbyhq.com/acme/1234/application",
                        canonical_url=None,
                        source=None,
                        title="Software Engineer",
                        company="Acme",
                        location="Remote, United States",
                        posted_at="1 hour ago",
                        discovered_at="2026-04-07T13:00:00Z",
                        role_keywords=[],
                        allowed_locations=[],
                    )

                    self.assertEqual(duplicate.action, "duplicate_existing_attempt")
                    self.assertEqual(
                        duplicate.status_reason, f"existing_application_{status}"
                    )

    def test_requeue_runner_failures_only_requeues_codex_worker_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite3"
            run = start_run(db_path)
            run_id = int(run["id"])

            runner_failed = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-07T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )
            normal_failed = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://jobs.ashbyhq.com/acme/999/application",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="1 hour ago",
                discovered_at="2026-04-07T12:30:00Z",
                role_keywords=[],
                allowed_locations=[],
            )

            record_application(
                db_path,
                job_key=runner_failed.job_key,
                status="failed",
                confirmation_text=None,
                confirmation_url=runner_failed.canonical_url,
                error_message="Codex apply worker exited with code 1.",
                run_id=run_id,
            )
            record_finding(
                db_path,
                job_key=runner_failed.job_key,
                run_id=run_id,
                application_status="failed",
                stage="worker",
                category="codex_worker_error",
                summary="Worker crashed",
                detail="Failure bundle: data/example",
                page_url=runner_failed.canonical_url,
            )

            record_application(
                db_path,
                job_key=normal_failed.job_key,
                status="failed",
                confirmation_text=None,
                confirmation_url=normal_failed.canonical_url,
                error_message="Submission outcome could not be verified.",
                run_id=run_id,
            )
            record_finding(
                db_path,
                job_key=normal_failed.job_key,
                run_id=run_id,
                application_status="failed",
                stage="submit",
                category="confirmation_missing",
                summary="No confirmation page",
                detail="Observed submit with no receipt.",
                page_url=normal_failed.canonical_url,
            )

            requeued = requeue_runner_failures(db_path, run_id=run_id)

            self.assertEqual(requeued["count"], 1)
            self.assertEqual(requeued["job_keys"], [runner_failed.job_key])

            job = next_job(db_path, mark_applying=True)
            self.assertIsNotNone(job)
            self.assertEqual(job["job_key"], runner_failed.job_key)
            record_application(
                db_path,
                job_key=runner_failed.job_key,
                status="submitted",
                confirmation_text="Application received",
                confirmation_url="https://boards.greenhouse.io/acme/jobs/12345/thanks",
                error_message=None,
                run_id=run_id,
            )

            summary = finish_run(db_path, run_id)
            self.assertEqual(summary["jobs_applied"], 1)
            self.assertEqual(summary["jobs_failed"], 2)

    def test_finish_run_includes_findings_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite3"
            run = start_run(db_path)
            run_id = int(run["id"])

            failed_job = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-07T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )
            blocked_job = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://jobs.ashbyhq.com/acme/999/application",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="1 hour ago",
                discovered_at="2026-04-07T12:30:00Z",
                role_keywords=[],
                allowed_locations=[],
            )

            record_application(
                db_path,
                job_key=failed_job.job_key,
                status="failed",
                confirmation_text=None,
                confirmation_url=failed_job.canonical_url,
                error_message="Outcome unverifiable.",
                run_id=run_id,
            )
            record_application(
                db_path,
                job_key=blocked_job.job_key,
                status="blocked",
                confirmation_text=None,
                confirmation_url=blocked_job.canonical_url,
                error_message="Employer requires account login.",
                run_id=run_id,
            )

            record_finding(
                db_path,
                job_key=failed_job.job_key,
                run_id=run_id,
                application_status="failed",
                stage="submit",
                category="confirmation_missing",
                summary="No confirmation after submit attempt",
                detail="The submit button disappeared but no success message appeared.",
                page_url=failed_job.canonical_url,
            )
            record_finding(
                db_path,
                job_key=failed_job.job_key,
                run_id=run_id,
                application_status="failed",
                stage="submit",
                category="confirmation_missing",
                summary="Retry also ended without a confirmation screen",
                detail="Two attempts completed with no receipt page.",
                page_url=failed_job.canonical_url,
            )
            record_finding(
                db_path,
                job_key=blocked_job.job_key,
                run_id=run_id,
                application_status="blocked",
                stage="open",
                category="login_required",
                summary="Employer redirects to a credentialed portal",
                detail="The workflow cannot proceed without a pre-existing employer account.",
                page_url=blocked_job.canonical_url,
            )

            summary = finish_run(db_path, run_id)

            self.assertEqual(summary["jobs_failed"], 2)
            self.assertEqual(summary["findings_summary"]["total_findings"], 3)
            self.assertEqual(
                summary["findings_summary"]["by_category"],
                [
                    {"category": "confirmation_missing", "count": 2},
                    {"category": "login_required", "count": 1},
                ],
            )
            latest = summary["findings_summary"]["latest_for_unsuccessful_jobs"]
            self.assertEqual(len(latest), 2)
            self.assertEqual(
                {item["job_key"] for item in latest},
                {failed_job.job_key, blocked_job.job_key},
            )
            failed_latest = next(
                item for item in latest if item["job_key"] == failed_job.job_key
            )
            self.assertEqual(
                failed_latest["summary"],
                "Retry also ended without a confirmation screen",
            )

    def test_prepare_run_seeds_queries_from_profile_and_requeues_applying_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse, ashby, lever")

            interrupted_run = start_run(db_path)
            interrupted_run_id = int(interrupted_run["id"])
            ingested = ingest_job(
                db_path,
                run_id=interrupted_run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
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
            self.assertEqual(job["job_key"], ingested.job_key)
            self.assertEqual(job["status"], "applying")

            validation = validate_profile(root)
            prepared = prepare_run(db_path, root)

            self.assertEqual(prepared["requeued_jobs_count"], 1)
            self.assertEqual(
                [item["source_key"] for item in prepared["queries"]],
                [item["source_key"] for item in validation.to_dict()["google_search_queries"]],
            )

            resumed_job = next_job(db_path, mark_applying=False)
            self.assertIsNotNone(resumed_job)
            self.assertEqual(resumed_job["job_key"], ingested.job_key)
            self.assertEqual(resumed_job["status"], "ready_to_apply")
            self.assertEqual(
                resumed_job["status_reason"], "requeued_from_interrupted_run"
            )

            status = workflow_status(db_path, run_id=int(prepared["run_id"]))
            self.assertEqual(status["ready_jobs"], 1)
            self.assertEqual(status["applying_jobs"], 0)
            self.assertEqual(status["queries_pending"], 3)
            self.assertFalse(status["drained"])

    def test_query_lifecycle_updates_workflow_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse, ashby")

            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])

            first_query = next_query(db_path, run_id=run_id)
            self.assertIsNotNone(first_query)
            self.assertEqual(first_query["status"], "in_progress")

            status = workflow_status(db_path, run_id=run_id)
            self.assertEqual(status["queries_pending"], 1)
            self.assertEqual(status["queries_in_progress"], 1)
            self.assertFalse(status["drained"])

            completed = complete_query(
                db_path,
                run_id=run_id,
                source_key=first_query["source_key"],
                results_seen=12,
                jobs_ingested=3,
            )
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["results_seen"], 12)
            self.assertEqual(completed["jobs_ingested"], 3)

            second_query = next_query(db_path, run_id=run_id)
            self.assertIsNotNone(second_query)
            failed = fail_query(
                db_path,
                run_id=run_id,
                source_key=second_query["source_key"],
                message="Google rate limited the query.",
                results_seen=6,
                jobs_ingested=1,
            )
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["last_error"], "Google rate limited the query.")

            done_status = workflow_status(db_path, run_id=run_id)
            self.assertEqual(done_status["queries_pending"], 0)
            self.assertEqual(done_status["queries_in_progress"], 0)
            self.assertEqual(done_status["queries_completed"], 1)
            self.assertEqual(done_status["queries_failed"], 1)
            self.assertTrue(done_status["drained"])
            self.assertTrue(done_status["drained_with_errors"])

    def test_finish_run_refuses_unresolved_work_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse")

            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])

            with self.assertRaisesRegex(ValueError, "Cannot finish run while work remains"):
                finish_run(db_path, run_id)

            forced_summary = finish_run(db_path, run_id, force=True)
            self.assertEqual(forced_summary["search_summary"]["total_queries"], 1)
            self.assertEqual(forced_summary["search_summary"]["pending_queries"], 1)
            self.assertEqual(forced_summary["search_summary"]["requeued_jobs_count"], 0)

    def test_finish_run_returns_search_summary_for_drained_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse, ashby")

            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])

            first_query = next_query(db_path, run_id=run_id)
            second_query = None
            self.assertIsNotNone(first_query)
            complete_query(db_path, run_id=run_id, source_key=first_query["source_key"])
            second_query = next_query(db_path, run_id=run_id)
            self.assertIsNotNone(second_query)
            fail_query(
                db_path,
                run_id=run_id,
                source_key=second_query["source_key"],
                message="Search engine blocked this query.",
            )

            summary = finish_run(db_path, run_id)

            self.assertEqual(summary["search_summary"]["total_queries"], 2)
            self.assertEqual(summary["search_summary"]["completed_queries"], 1)
            self.assertEqual(summary["search_summary"]["failed_queries"], 1)
            self.assertEqual(summary["search_summary"]["pending_queries"], 0)
            self.assertEqual(summary["search_summary"]["in_progress_queries"], 0)


if __name__ == "__main__":
    unittest.main()
