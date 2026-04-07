from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from job_apply_bot.db import finish_run, ingest_job, next_job, record_application, start_run


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


if __name__ == "__main__":
    unittest.main()
