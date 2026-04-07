from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest


class CliTests(unittest.TestCase):
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

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "job_apply_bot",
                    "validate-profile",
                    "--repo-root",
                    str(root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                payload["profile"]["enabled_search_sites"],
                ["jobright", "greenhouse", "ashby"],
            )


if __name__ == "__main__":
    unittest.main()
