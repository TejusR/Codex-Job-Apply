from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from job_apply_bot.profile import validate_profile
from job_apply_bot.search import SUPPORTED_SEARCH_SITES


class ValidateProfileTests(unittest.TestCase):
    def test_discovery_max_pages_defaults_to_five(self) -> None:
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

            payload = validate_profile(root).to_dict()

            self.assertEqual(payload["profile"]["discovery_max_pages"], 5)

    def test_valid_discovery_max_pages_is_surfaced_in_profile(self) -> None:
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
                    APPLICANT_DISCOVERY_MAX_PAGES=7
                    """
                ).strip(),
                encoding="utf-8",
            )
            (root / "applicant.md").write_text(
                "# Applicant Details\n\n## Work Authorization Notes\nProvided\n\n## Reusable Highlights\nProvided\n",
                encoding="utf-8",
            )

            payload = validate_profile(root).to_dict()

            self.assertEqual(payload["profile"]["discovery_max_pages"], 7)

    def test_invalid_discovery_max_pages_warns_and_falls_back_to_default(self) -> None:
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
                    APPLICANT_DISCOVERY_MAX_PAGES=0
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
                "APPLICANT_DISCOVERY_MAX_PAGES must be a positive integer. Using default 5.",
                result.warnings,
            )
            self.assertEqual(result.to_dict()["profile"]["discovery_max_pages"], 5)

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
                list(SUPPORTED_SEARCH_SITES),
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
                    APPLICANT_ENABLED_SEARCH_SITES=ashby, jobs.lever.co, app.dover.com, monster
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
                "APPLICANT_ENABLED_SEARCH_SITES includes unsupported values: monster. Supported values: jobright, greenhouse, ashby, workable, jobvite, jazz, adp, lever, bamboohr, paylocity, smartrecruiters, gem, dover.",
                result.warnings,
            )
            self.assertEqual(
                result.to_dict()["profile"]["enabled_search_sites"],
                ["ashby", "lever", "dover"],
            )

    def test_google_search_queries_follow_enabled_sites_and_role_keywords(self) -> None:
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
                    APPLICANT_TARGET_ROLE_KEYWORDS=software engineer, backend engineer, full stack engineer, software developer
                    APPLICANT_ENABLED_SEARCH_SITES=jobs.lever.co, app.dover.com
                    """
                ).strip(),
                encoding="utf-8",
            )
            (root / "applicant.md").write_text(
                "# Applicant Details\n\n## Work Authorization Notes\nProvided\n\n## Reusable Highlights\nProvided\n",
                encoding="utf-8",
            )

            payload = validate_profile(root).to_dict()

            self.assertEqual(
                payload["google_search_queries"],
                [
                    {
                        "source_key": "lever",
                        "domain": "jobs.lever.co",
                        "query": 'site:jobs.lever.co ("software engineer" OR "backend engineer" OR "full stack engineer" OR "software developer") ("united states" OR "remote")',
                    },
                    {
                        "source_key": "dover",
                        "domain": "app.dover.com",
                        "query": 'site:app.dover.com ("software engineer" OR "backend engineer" OR "full stack engineer" OR "software developer") ("united states" OR "remote")',
                    },
                ],
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
