from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from job_apply_bot.dashboard_api import create_app
from job_apply_bot.db import (
    create_resume_customization,
    finish_run,
    ingest_job,
    insert_search_results,
    prepare_run,
    record_application,
    record_finding,
    start_run,
    update_worker_session,
)


class DashboardApiTests(unittest.TestCase):
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

    def _client(self, root: Path, db_path: Path) -> TestClient:
        return TestClient(create_app(repo_root=root, db_path=db_path))

    def test_record_application_stores_run_and_resume_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite3"
            run = start_run(db_path)
            run_id = int(run["id"])

            ingested = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-09T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )

            record_application(
                db_path,
                job_key=ingested.job_key,
                status="submitted",
                confirmation_text="Submitted",
                confirmation_url="https://boards.greenhouse.io/acme/jobs/12345/thanks",
                error_message=None,
                run_id=run_id,
                resume_path_used="resume/custom-resume.pdf",
                resume_label_used="custom-resume.pdf",
            )

            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            try:
                row = connection.execute(
                    """
                    SELECT run_id, resume_path_used, resume_label_used
                    FROM applications
                    WHERE job_key = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (ingested.job_key,),
                ).fetchone()
            finally:
                connection.close()

            self.assertIsNotNone(row)
            self.assertEqual(row["run_id"], run_id)
            self.assertEqual(row["resume_path_used"], "resume/custom-resume.pdf")
            self.assertEqual(row["resume_label_used"], "custom-resume.pdf")

    def test_runs_endpoint_migrates_legacy_applications_table_before_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"

            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE applications (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      job_key TEXT NOT NULL,
                      applied_at TEXT NOT NULL DEFAULT (datetime('now')),
                      status TEXT NOT NULL,
                      confirmation_text TEXT,
                      confirmation_url TEXT,
                      error_message TEXT
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()

            client = self._client(root, db_path)
            response = client.get("/api/runs")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["items"], [])

            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            try:
                column_rows = connection.execute(
                    "PRAGMA table_info(applications)"
                ).fetchall()
                index_row = connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'index' AND name = 'idx_applications_resume_customization_id'
                    """
                ).fetchone()
            finally:
                connection.close()

            column_names = {str(row["name"]) for row in column_rows}
            self.assertIn("run_id", column_names)
            self.assertIn("resume_customization_id", column_names)
            self.assertIn("resume_path_used", column_names)
            self.assertIn("resume_label_used", column_names)
            self.assertIsNotNone(index_row)

    def test_start_run_endpoint_rejects_second_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse")

            with patch("job_apply_bot.dashboard_service.subprocess.Popen") as popen:
                client = self._client(root, db_path)
                started = client.post("/api/runs")
                blocked = client.post("/api/runs")
                runs = client.get("/api/runs")

            self.assertEqual(started.status_code, 200, started.text)
            self.assertTrue(started.json()["launched"])
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertEqual(popen.call_count, 1)
            payload = runs.json()
            self.assertFalse(payload["can_start_run"])
            self.assertEqual(payload["blocked_by_run_id"], started.json()["run"]["id"])

    def test_run_detail_endpoint_returns_queries_workers_and_search_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse")
            prepared = prepare_run(db_path, root)
            run_id = int(prepared["run_id"])
            source_key = str(prepared["queries"][0]["source_key"])
            insert_search_results(
                db_path,
                run_id=run_id,
                source_key=source_key,
                origin_kind="google_result",
                results=[
                    {
                        "url": "https://boards.greenhouse.io/acme/jobs/12345",
                        "title": "Software Engineer",
                        "snippet": "Acme backend role",
                        "visible_date": "2 hours ago",
                        "page_number": 1,
                        "rank": 1,
                    }
                ],
            )
            update_worker_session(
                db_path,
                run_id=run_id,
                worker_type="discovery",
                slot_key=source_key,
                status="running",
                thread_id="thread-discovery-1",
            )

            client = self._client(root, db_path)
            response = client.get(f"/api/runs/{run_id}")

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["summary"]["ui_status"], "running")
            self.assertEqual(len(payload["queries"]), 1)
            self.assertEqual(len(payload["worker_sessions"]), 1)
            self.assertEqual(len(payload["recent_search_results"]), 1)

    def test_jobs_endpoints_include_snapshot_and_default_resume_info(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse")
            run = start_run(db_path)
            run_id = int(run["id"])

            applied_job = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-09T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )
            record_application(
                db_path,
                job_key=applied_job.job_key,
                status="submitted",
                confirmation_text="Submitted",
                confirmation_url="https://boards.greenhouse.io/acme/jobs/12345/thanks",
                error_message=None,
                run_id=run_id,
                resume_customization_id=None,
                resume_path_used="resume/tailored-acme.pdf",
                resume_label_used="tailored-acme.pdf",
            )

            fallback_job = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/99999",
                canonical_url=None,
                source=None,
                title="Backend Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="1 hour ago",
                discovered_at="2026-04-09T13:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )

            client = self._client(root, db_path)
            list_response = client.get(
                f"/api/jobs?run_id={run_id}&status=ready_to_apply&page=1&page_size=10"
            )
            applied_detail = client.get(f"/api/jobs/{applied_job.job_key}")
            fallback_detail = client.get(f"/api/jobs/{fallback_job.job_key}")

            self.assertEqual(list_response.status_code, 200, list_response.text)
            self.assertEqual(list_response.json()["total"], 1)
            self.assertEqual(list_response.json()["available_sources"], ["greenhouse"])
            self.assertEqual(applied_detail.status_code, 200, applied_detail.text)
            self.assertEqual(
                applied_detail.json()["resume_info"]["source"], "application_snapshot"
            )
            self.assertEqual(
                fallback_detail.json()["resume_info"]["source"], "default_profile"
            )
            self.assertEqual(
                fallback_detail.json()["resume_info"]["label"], "resume.pdf"
            )

    def test_resume_customization_endpoints_expose_preview_and_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse")
            rendered_pdf = root / "tailored.pdf"
            rendered_pdf.write_text("pdf-stub", encoding="utf-8")
            run = start_run(db_path)
            run_id = int(run["id"])

            job = ingest_job(
                db_path,
                run_id=run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-09T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )
            customization = create_resume_customization(
                db_path,
                job_key=job.job_key,
                run_id=run_id,
                status="succeeded",
                source_template_path=str(root / "resume-template.tex"),
                job_description_hash="hash-1",
                rendered_tex_path=str(root / "tailored.tex"),
                rendered_pdf_path=str(rendered_pdf),
                preview_content="# Preview",
                customization_payload_json="{}",
                compiler="pdflatex",
                error_message=None,
            )
            record_application(
                db_path,
                job_key=job.job_key,
                status="submitted",
                confirmation_text="Submitted",
                confirmation_url="https://boards.greenhouse.io/acme/jobs/12345/thanks",
                error_message=None,
                run_id=run_id,
                resume_customization_id=int(customization["id"]),
                resume_path_used=str(rendered_pdf),
                resume_label_used="tailored.pdf",
            )

            client = self._client(root, db_path)
            job_detail = client.get(f"/api/jobs/{job.job_key}")
            customization_detail = client.get(
                f"/api/resume-customizations/{customization['id']}"
            )
            customization_file = client.get(
                f"/api/resume-customizations/{customization['id']}/file"
            )

            self.assertEqual(job_detail.status_code, 200, job_detail.text)
            self.assertEqual(
                job_detail.json()["resume_info"]["source"],
                "job_tailored",
            )
            self.assertEqual(
                job_detail.json()["application_history"][0]["resume_info"]["source"],
                "job_tailored",
            )
            self.assertEqual(customization_detail.status_code, 200, customization_detail.text)
            self.assertEqual(customization_detail.json()["preview_content"], "# Preview")
            self.assertEqual(customization_file.status_code, 200, customization_file.text)

    def test_requeue_and_finish_endpoints_handle_conflicts_and_force_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse")
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
                discovered_at="2026-04-09T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )
            record_application(
                db_path,
                job_key=failed_job.job_key,
                status="failed",
                confirmation_text=None,
                confirmation_url=failed_job.canonical_url,
                error_message="Codex apply worker exited with code 1.",
                run_id=run_id,
                resume_path_used="resume/default.pdf",
                resume_label_used="default.pdf",
            )
            record_finding(
                db_path,
                job_key=failed_job.job_key,
                run_id=run_id,
                application_status="failed",
                stage="worker",
                category="codex_worker_error",
                summary="Worker crashed",
                detail="Traceback captured",
                page_url=failed_job.canonical_url,
            )

            client = self._client(root, db_path)
            requeued = client.post(f"/api/runs/{run_id}/requeue-runner-failures")
            not_finished = client.post(f"/api/runs/{run_id}/finish", json={"force": False})
            forced = client.post(f"/api/runs/{run_id}/finish", json={"force": True})

            self.assertEqual(requeued.status_code, 200, requeued.text)
            self.assertEqual(requeued.json()["count"], 1)
            self.assertEqual(not_finished.status_code, 409, not_finished.text)
            self.assertEqual(forced.status_code, 200, forced.text)
            self.assertIsNotNone(forced.json()["run"]["finished_at"])

    def test_finished_runs_do_not_inherit_other_run_live_job_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse")

            finished_run = start_run(db_path)
            finished_run_id = int(finished_run["id"])
            finished_job = ingest_job(
                db_path,
                run_id=finished_run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/12345",
                canonical_url=None,
                source=None,
                title="Software Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="2 hours ago",
                discovered_at="2026-04-09T12:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )
            record_application(
                db_path,
                job_key=finished_job.job_key,
                status="submitted",
                confirmation_text="Submitted",
                confirmation_url=finished_job.canonical_url,
                error_message=None,
                run_id=finished_run_id,
                resume_path_used="resume/default.pdf",
                resume_label_used="default.pdf",
            )
            finish_run(db_path, finished_run_id)

            active_run = start_run(db_path)
            active_run_id = int(active_run["id"])
            ingest_job(
                db_path,
                run_id=active_run_id,
                raw_url="https://boards.greenhouse.io/acme/jobs/99999",
                canonical_url=None,
                source=None,
                title="Backend Engineer",
                company="Acme",
                location="Remote, United States",
                posted_at="1 hour ago",
                discovered_at="2026-04-09T13:00:00Z",
                role_keywords=[],
                allowed_locations=[],
            )

            client = self._client(root, db_path)
            runs = client.get("/api/runs")

            self.assertEqual(runs.status_code, 200, runs.text)
            payload = runs.json()
            finished_summary = next(
                item for item in payload["items"] if item["id"] == finished_run_id
            )
            self.assertEqual(finished_summary["ui_status"], "completed")
            self.assertEqual(finished_summary["live_counts"]["ready_jobs"], 0)
            self.assertEqual(finished_summary["live_counts"]["applying_jobs"], 0)


if __name__ == "__main__":
    unittest.main()
