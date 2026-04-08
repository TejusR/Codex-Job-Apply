from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from job_apply_bot.search import SUPPORTED_SEARCH_SITES


class CliTests(unittest.TestCase):
    def _run_cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "job_apply_bot", *args],
            cwd=cwd or Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )

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

    def test_validate_profile_command_returns_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
                    )
                ),
                encoding="utf-8",
            )
            (root / "applicant.md").write_text(
                "# Applicant Details\n\n## Work Authorization Notes\nProvided\n\n## Reusable Highlights\nProvided\n",
                encoding="utf-8",
            )

            completed = self._run_cli("validate-profile", "--repo-root", str(root))

            self.assertEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                payload["profile"]["enabled_search_sites"],
                list(SUPPORTED_SEARCH_SITES),
            )

    def test_record_application_accepts_blocked_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite3"

            started = self._run_cli("--db-path", str(db_path), "start-run")
            self.assertEqual(started.returncode, 0, started.stderr)
            run_id = json.loads(started.stdout)["id"]

            ingested = self._run_cli(
                "--db-path",
                str(db_path),
                "ingest-job",
                "--run-id",
                str(run_id),
                "--raw-url",
                "https://boards.greenhouse.io/acme/jobs/12345",
                "--title",
                "Software Engineer",
                "--company",
                "Acme",
                "--location",
                "Remote, United States",
                "--posted-at",
                "2 hours ago",
            )
            self.assertEqual(ingested.returncode, 0, ingested.stderr)
            job_key = json.loads(ingested.stdout)["job_key"]

            completed = self._run_cli(
                "--db-path",
                str(db_path),
                "record-application",
                "--job-key",
                job_key,
                "--status",
                "blocked",
                "--confirmation-url",
                "https://boards.greenhouse.io/acme/jobs/12345",
                "--error-message",
                "Employer requires a separate login flow.",
                "--run-id",
                str(run_id),
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "blocked")

    def test_record_finding_command_returns_inserted_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobs.sqlite3"

            started = self._run_cli("--db-path", str(db_path), "start-run")
            self.assertEqual(started.returncode, 0, started.stderr)
            run_id = json.loads(started.stdout)["id"]

            ingested = self._run_cli(
                "--db-path",
                str(db_path),
                "ingest-job",
                "--run-id",
                str(run_id),
                "--raw-url",
                "https://jobs.ashbyhq.com/acme/1234/application",
                "--title",
                "Software Engineer",
                "--company",
                "Acme",
                "--location",
                "Remote, United States",
                "--posted-at",
                "2 hours ago",
            )
            self.assertEqual(ingested.returncode, 0, ingested.stderr)
            job_key = json.loads(ingested.stdout)["job_key"]

            completed = self._run_cli(
                "--db-path",
                str(db_path),
                "record-finding",
                "--job-key",
                job_key,
                "--run-id",
                str(run_id),
                "--application-status",
                "blocked",
                "--stage",
                "open",
                "--category",
                "login_required",
                "--summary",
                "Employer requires account access",
                "--detail",
                "The application redirects to a credentialed portal.",
                "--page-url",
                "https://jobs.ashbyhq.com/acme/1234/application",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["job_key"], job_key)
            self.assertEqual(payload["run_id"], run_id)
            self.assertEqual(payload["category"], "login_required")

    def test_prepare_run_and_query_commands_return_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "jobs.sqlite3"
            self._write_valid_profile(root, enabled_search_sites="greenhouse, ashby")

            prepared = self._run_cli(
                "--db-path",
                str(db_path),
                "prepare-run",
                "--repo-root",
                str(root),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            prepared_payload = json.loads(prepared.stdout)
            self.assertEqual(prepared_payload["requeued_jobs_count"], 0)
            self.assertEqual(len(prepared_payload["queries"]), 2)

            run_id = prepared_payload["run_id"]
            next_query = self._run_cli(
                "--db-path",
                str(db_path),
                "next-query",
                "--run-id",
                str(run_id),
            )
            self.assertEqual(next_query.returncode, 0, next_query.stderr)
            next_query_payload = json.loads(next_query.stdout)
            self.assertEqual(next_query_payload["status"], "in_progress")

            completed = self._run_cli(
                "--db-path",
                str(db_path),
                "complete-query",
                "--run-id",
                str(run_id),
                "--source-key",
                next_query_payload["source_key"],
                "--results-seen",
                "4",
                "--jobs-ingested",
                "2",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            status = self._run_cli(
                "--db-path",
                str(db_path),
                "workflow-status",
                "--run-id",
                str(run_id),
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            status_payload = json.loads(status.stdout)
            self.assertEqual(status_payload["queries_completed"], 1)
            self.assertEqual(status_payload["queries_pending"], 1)
            self.assertFalse(status_payload["drained"])


if __name__ == "__main__":
    unittest.main()
