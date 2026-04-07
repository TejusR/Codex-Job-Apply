from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest


class CliTests(unittest.TestCase):
    def _run_cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "job_apply_bot", *args],
            cwd=cwd or Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
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
                ["jobright", "greenhouse", "ashby"],
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


if __name__ == "__main__":
    unittest.main()
