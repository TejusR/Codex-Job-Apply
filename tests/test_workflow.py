from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from job_apply_bot.db import (
    finish_run,
    ingest_job,
    next_job,
    record_application,
    record_finding,
    start_run,
)


class WorkflowStateTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
