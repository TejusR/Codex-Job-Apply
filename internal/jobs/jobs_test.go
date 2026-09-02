package jobs

import (
	"testing"
	"time"
)

func TestCanonicalizeURLAndJobKey(t *testing.T) {
	got, err := CanonicalizeURL("HTTPS://Boards.Greenhouse.io/acme/jobs/12345?utm_source=google&gh_src=abc&id=7", "")
	if err != nil {
		t.Fatal(err)
	}
	want := "https://boards.greenhouse.io/acme/jobs/12345?id=7"
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
	if BuildJobKey(want) != BuildJobKey(want) {
		t.Fatal("job key is not deterministic")
	}
}
func TestEvaluatePostedAt(t *testing.T) {
	now := time.Date(2026, 4, 7, 12, 0, 0, 0, time.UTC)
	hours := "2 hours ago"
	recent := EvaluatePostedAt(&hours, now)
	if !recent.IsRecent || !recent.IsVerifiable {
		t.Fatalf("unexpected freshness: %#v", recent)
	}
	yesterday := "yesterday"
	ambiguous := EvaluatePostedAt(&yesterday, now)
	if ambiguous.IsVerifiable || ambiguous.Reason == nil || *ambiguous.Reason != "date_is_only_yesterday" {
		t.Fatalf("unexpected yesterday result: %#v", ambiguous)
	}
}
func TestInferSources(t *testing.T) {
	cases := map[string]string{"https://jobright.ai/jobs/info/123": "jobright", "https://boards.greenhouse.io/acme/jobs/123": "greenhouse", "https://jobs.ashbyhq.com/acme/123/application": "ashby", "https://apply.workable.com/acme/j/123": "workable", "https://jobs.jobvite.com/acme/job/o123": "jobvite", "https://app.jazz.co/apply/abc123": "jazz", "https://recruiting.adp.com/srccar/public/RTI.home?c=123": "adp", "https://jobs.lever.co/acme/123": "lever", "https://acme.bamboohr.com/careers/123": "bamboohr", "https://recruiting.paylocity.com/Recruiting/Jobs/Details/123": "paylocity", "https://jobs.smartrecruiters.com/acme/123": "smartrecruiters", "https://jobs.gem.com/acme/roles/123": "gem", "https://app.dover.com/jobs/acme/123": "dover"}
	for raw, want := range cases {
		t.Run(want, func(t *testing.T) {
			if got := InferSource(raw); got != want {
				t.Fatalf("got %q want %q", got, want)
			}
		})
	}
}
