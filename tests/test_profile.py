from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from job_apply_bot.profile import validate_profile


class ValidateProfileTests(unittest.TestCase):
    def test_enabled_search_sites_default_to_all_supported_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "resume.pdf").write_text("stub", encoding="utf-8")
            (root / ".env").write_text(
                textwrap.dedent(
                    """
                    APPLICANT_FULL_NAME=Tejus Ramesh
                    APPLICANT_EMAIL=rameshtejus@gmail.com
                    APPLICANT_PHONE=(480)-810-7760
                    APPLICANT_LOCATION=Tempe, AZ
                    APPLICANT_RESUME_PATH=resume.pdf
                    APPLICANT_US_WORK_AUTHORIZED=true
                    APPLICANT_REQUIRES_VISA_SPONSORSHIP=false
                    """
                ).strip(),
                encoding="utf-8",
            )
            (root / "applicant.md").write_text(
                "# Applicant Details\n\n## Work Authorization Notes\nProvided\n\n## Reusable Highlights\nProvided\n",
                encoding="utf-8",
            )

            result = validate_profile(root)

            self.assertEqual(
                result.to_dict()["profile"]["enabled_search_sites"],
                ["jobright", "greenhouse", "ashby"],
            )

    def test_invalid_search_sites_are_warned_and_valid_sites_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "resume.pdf").write_text("stub", encoding="utf-8")
            (root / ".env").write_text(
                textwrap.dedent(
                    """
                    APPLICANT_FULL_NAME=Tejus Ramesh
                    APPLICANT_EMAIL=rameshtejus@gmail.com
                    APPLICANT_PHONE=(480)-810-7760
                    APPLICANT_LOCATION=Tempe, AZ
                    APPLICANT_RESUME_PATH=resume.pdf
                    APPLICANT_US_WORK_AUTHORIZED=true
                    APPLICANT_REQUIRES_VISA_SPONSORSHIP=false
                    APPLICANT_ENABLED_SEARCH_SITES=ashby, jobright.ai, monster
                    """
                ).strip(),
                encoding="utf-8",
            )
            (root / "applicant.md").write_text(
                "# Applicant Details\n\n## Work Authorization Notes\nProvided\n\n## Reusable Highlights\nProvided\n",
                encoding="utf-8",
            )

            result = validate_profile(root)

            self.assertIn(
                "APPLICANT_ENABLED_SEARCH_SITES includes unsupported values: monster. Supported values: ashby, greenhouse, jobright.",
                result.warnings,
            )
            self.assertEqual(
                result.to_dict()["profile"]["enabled_search_sites"],
                ["ashby", "jobright"],
            )

    def test_missing_required_fields_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "resume.pdf").write_text("stub", encoding="utf-8")
            (root / ".env").write_text(
                textwrap.dedent(
                    """
                    APPLICANT_FULL_NAME=Tejus Ramesh
                    APPLICANT_EMAIL=rameshtejus@gmail.com
                    APPLICANT_PHONE=(480)-810-7760
                    APPLICANT_LOCATION=Tempe, AZ
                    APPLICANT_RESUME_PATH=resume.pdf
                    APPLICANT_US_WORK_AUTHORIZED=
                    APPLICANT_REQUIRES_VISA_SPONSORSHIP=
                    """
                ).strip(),
                encoding="utf-8",
            )
            (root / "applicant.md").write_text(
                "# Applicant Details\n\n## Reusable Highlights\nExample\n",
                encoding="utf-8",
            )

            result = validate_profile(root)

            self.assertIn("APPLICANT_US_WORK_AUTHORIZED", result.missing_required_fields)
            self.assertIn(
                "APPLICANT_REQUIRES_VISA_SPONSORSHIP", result.missing_required_fields
            )
            self.assertFalse(result.ok)

    def test_missing_env_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = validate_profile(root)
            self.assertIn(".env", result.missing_required_files)


if __name__ == "__main__":
    unittest.main()
