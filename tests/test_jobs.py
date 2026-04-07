from __future__ import annotations

from datetime import datetime, timezone
import unittest

from job_apply_bot.jobs import (
    build_job_key,
    canonicalize_url,
    evaluate_posted_at,
    infer_source,
)


class CanonicalizeUrlTests(unittest.TestCase):
    def test_strips_tracking_and_lowercases_host(self) -> None:
        canonical = canonicalize_url(
            "HTTPS://Boards.Greenhouse.io/acme/jobs/12345?utm_source=google&gh_src=abc&id=7"
        )
        self.assertEqual(
            canonical,
            "https://boards.greenhouse.io/acme/jobs/12345?id=7",
        )

    def test_build_job_key_is_deterministic(self) -> None:
        url = "https://boards.greenhouse.io/acme/jobs/12345"
        self.assertEqual(build_job_key(url), build_job_key(url))


class EvaluatePostedAtTests(unittest.TestCase):
    def test_relative_hours_are_recent(self) -> None:
        now = datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc)
        freshness = evaluate_posted_at("2 hours ago", now=now)
        self.assertTrue(freshness.is_recent)
        self.assertTrue(freshness.is_verifiable)

    def test_yesterday_without_time_is_not_verifiable(self) -> None:
        now = datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc)
        freshness = evaluate_posted_at("yesterday", now=now)
        self.assertFalse(freshness.is_verifiable)
        self.assertEqual(freshness.reason, "date_is_only_yesterday")


class InferSourceTests(unittest.TestCase):
    def test_recognizes_all_supported_board_domains(self) -> None:
        cases = {
            "https://jobright.ai/jobs/info/123": "jobright",
            "https://boards.greenhouse.io/acme/jobs/123": "greenhouse",
            "https://jobs.ashbyhq.com/acme/123/application": "ashby",
            "https://apply.workable.com/acme/j/123": "workable",
            "https://jobs.jobvite.com/acme/job/o123": "jobvite",
            "https://app.jazz.co/apply/abc123": "jazz",
            "https://recruiting.adp.com/srccar/public/RTI.home?c=123": "adp",
            "https://jobs.lever.co/acme/123": "lever",
            "https://acme.bamboohr.com/careers/123": "bamboohr",
            "https://recruiting.paylocity.com/Recruiting/Jobs/Details/123": "paylocity",
            "https://jobs.smartrecruiters.com/acme/123": "smartrecruiters",
            "https://jobs.gem.com/acme/roles/123": "gem",
            "https://app.dover.com/jobs/acme/123": "dover",
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(infer_source(url), expected)


if __name__ == "__main__":
    unittest.main()
