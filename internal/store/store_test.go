package store

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"

	_ "modernc.org/sqlite"
)

func testStore(t *testing.T) (*Store, string) {
	t.Helper()
	root := t.TempDir()
	s, err := Open(filepath.Join(root, "jobs.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s, root
}
func testProfileRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "resume.pdf"), []byte("stub"), 0o600)
	os.WriteFile(filepath.Join(root, ".env"), []byte(`APPLICANT_FULL_NAME=Test Person
APPLICANT_EMAIL=test@example.com
APPLICANT_PHONE=555-0100
APPLICANT_LOCATION=Tempe, AZ
APPLICANT_RESUME_PATH=resume.pdf
APPLICANT_US_WORK_AUTHORIZED=true
APPLICANT_REQUIRES_VISA_SPONSORSHIP=false
APPLICANT_TARGET_ROLE_KEYWORDS=software engineer
APPLICANT_ALLOWED_LOCATIONS=United States, Remote
APPLICANT_ENABLED_SEARCH_SITES=greenhouse, lever
`), 0o600)
	os.WriteFile(filepath.Join(root, "applicant.md"), []byte("## Work Authorization Notes\nProvided\n\n## Reusable Highlights\nProvided\n"), 0o600)
	return root
}
func TestLegacyMigrationOrdersColumnsBeforeIndexes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "legacy.sqlite3")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`CREATE TABLE applications(id INTEGER PRIMARY KEY AUTOINCREMENT,job_key TEXT NOT NULL,applied_at TEXT NOT NULL,status TEXT NOT NULL)`)
	if err != nil {
		t.Fatal(err)
	}
	db.Close()
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	rows, err := s.QueryRows(context.Background(), `PRAGMA table_info(applications)`)
	if err != nil {
		t.Fatal(err)
	}
	columns := map[string]bool{}
	for _, row := range rows {
		columns[textValue(row["name"])] = true
	}
	for _, column := range []string{"run_id", "resume_customization_id", "resume_path_used", "resume_label_used"} {
		if !columns[column] {
			t.Fatalf("missing migrated column %s", column)
		}
	}
}
func TestPrepareRunSeedsQueriesAndRequeues(t *testing.T) {
	ctx := context.Background()
	s, _ := testStore(t)
	run, _ := s.StartRun(ctx)
	_, err := s.Exec(ctx, `INSERT INTO jobs(job_key,canonical_url,status,discovered_at,last_updated_at) VALUES('old','https://example.com/old','applying','x','x')`)
	if err != nil {
		t.Fatal(err)
	}
	prepared, err := s.PrepareRun(ctx, testProfileRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	if toIntTest(prepared["requeued_jobs_count"]) != 1 {
		t.Fatalf("prepared: %#v", prepared)
	}
	queries := prepared["queries"].([]Row)
	if len(queries) != 2 {
		t.Fatalf("queries: %#v", queries)
	}
	_ = run
	job, _ := s.GetJob(ctx, "old")
	if job["status"] != "ready_to_apply" {
		t.Fatalf("job: %#v", job)
	}
}
func TestIngestFilteringDedupAndApplication(t *testing.T) {
	ctx := context.Background()
	s, _ := testStore(t)
	run, _ := s.StartRun(ctx)
	runID := toIntTest(run["id"])
	input := IngestInput{RunID: runID, RawURL: "https://boards.greenhouse.io/acme/jobs/123?utm_source=x", Title: "Software Engineer", Company: "Acme", Location: "Remote, United States", PostedAt: "New", RoleKeywords: []string{"software engineer"}, AllowedLocations: []string{"United States"}, AllowUnverifiableFreshness: true}
	first, err := s.IngestJob(ctx, input)
	if err != nil {
		t.Fatal(err)
	}
	if first.Status != "ready_to_apply" || first.Action != "ready_to_apply_unverifiable_date" {
		t.Fatalf("first: %#v", first)
	}
	second, err := s.IngestJob(ctx, input)
	if err != nil {
		t.Fatal(err)
	}
	if second.Action != "duplicate_same_run" {
		t.Fatalf("second: %#v", second)
	}
	job, err := s.NextJob(ctx, true)
	if err != nil || job == nil || job["status"] != "applying" {
		t.Fatalf("job=%#v err=%v", job, err)
	}
	application, err := s.RecordApplication(ctx, ApplicationInput{JobKey: first.JobKey, Status: "submitted", RunID: &runID, ResumePathUsed: "resume.pdf", ResumeLabelUsed: "resume.pdf"})
	if err != nil {
		t.Fatal(err)
	}
	if application["status"] != "submitted" {
		t.Fatalf("application: %#v", application)
	}
	stored, _ := s.GetJob(ctx, first.JobKey)
	if stored["status"] != "applied" {
		t.Fatalf("stored: %#v", stored)
	}
}
func TestFailedJobCanBeRediscoveredFutureRun(t *testing.T) {
	ctx := context.Background()
	s, _ := testStore(t)
	one, _ := s.StartRun(ctx)
	id1 := toIntTest(one["id"])
	input := IngestInput{RunID: id1, RawURL: "https://jobs.lever.co/acme/1", Title: "Software Engineer", Location: "Remote, United States", PostedAt: "today", RoleKeywords: []string{"software engineer"}, AllowedLocations: []string{"United States"}, AllowUnverifiableFreshness: true}
	ingested, _ := s.IngestJob(ctx, input)
	s.RecordApplication(ctx, ApplicationInput{JobKey: ingested.JobKey, Status: "failed", RunID: &id1})
	two, _ := s.StartRun(ctx)
	input.RunID = toIntTest(two["id"])
	again, err := s.IngestJob(ctx, input)
	if err != nil {
		t.Fatal(err)
	}
	if again.Status != "ready_to_apply" {
		t.Fatalf("again: %#v", again)
	}
}
func TestTerminalApplicationStaysDuplicate(t *testing.T) {
	for _, status := range []string{"submitted", "incomplete", "blocked", "duplicate_skipped"} {
		t.Run(status, func(t *testing.T) {
			ctx := context.Background()
			s, _ := testStore(t)
			one, _ := s.StartRun(ctx)
			id1 := toIntTest(one["id"])
			input := IngestInput{RunID: id1, RawURL: "https://jobs.lever.co/acme/" + status, Title: "Software Engineer", Location: "Remote, United States", PostedAt: "today", RoleKeywords: []string{"software engineer"}, AllowedLocations: []string{"United States"}, AllowUnverifiableFreshness: true}
			ingested, _ := s.IngestJob(ctx, input)
			s.RecordApplication(ctx, ApplicationInput{JobKey: ingested.JobKey, Status: status, RunID: &id1})
			two, _ := s.StartRun(ctx)
			input.RunID = toIntTest(two["id"])
			again, err := s.IngestJob(ctx, input)
			if err != nil {
				t.Fatal(err)
			}
			if again.Status != "duplicate_skipped" {
				t.Fatalf("again: %#v", again)
			}
		})
	}
}
func TestQueryAndSearchResultLifecycle(t *testing.T) {
	ctx := context.Background()
	s, _ := testStore(t)
	prepared, err := s.PrepareRun(ctx, testProfileRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	id := toIntTest(prepared["run_id"])
	query, err := s.NextQuery(ctx, id)
	if err != nil || query["status"] != "in_progress" {
		t.Fatalf("query=%#v err=%v", query, err)
	}
	seen, ingested := 4, 1
	cursor := `{"page_number":2}`
	query, err = s.CheckpointQuery(ctx, id, "greenhouse", &seen, &ingested, &cursor)
	if err != nil || query["cursor_json"] != cursor {
		t.Fatalf("query=%#v err=%v", query, err)
	}
	summary, err := s.InsertSearchResults(ctx, id, "greenhouse", nil, "google_result", []SearchResultInput{{URL: "https://example.com/1", PageNumber: 1, Rank: 1}, {URL: "https://example.com/1", PageNumber: 1, Rank: 1}})
	if err != nil || toIntTest(summary["inserted"]) != 1 {
		t.Fatalf("summary=%#v err=%v", summary, err)
	}
	result, err := s.ClaimSearchResult(ctx, id, "apply-1")
	if err != nil || result["status"] != "processing" {
		t.Fatalf("result=%#v err=%v", result, err)
	}
	reason := "done"
	s.UpdateSearchResult(ctx, toIntTest(result["id"]), "filtered_out", &reason, nil)
	for _, source := range []string{"greenhouse", "lever"} {
		q, _ := s.GetQuery(ctx, id, source)
		v1, v2 := toIntTest(q["results_seen"]), toIntTest(q["jobs_ingested"])
		s.UpdateQuery(ctx, id, source, "completed", &v1, &v2, nil, nil)
	}
	statusRow, err := s.WorkflowStatus(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	if statusRow["drained"] != true {
		t.Fatalf("status: %#v", statusRow)
	}
	if _, err = s.FinishRun(ctx, id, false); err != nil {
		t.Fatal(err)
	}
}
func TestFinishRefusesOutstandingWork(t *testing.T) {
	ctx := context.Background()
	s, _ := testStore(t)
	prepared, _ := s.PrepareRun(ctx, testProfileRoot(t))
	id := toIntTest(prepared["run_id"])
	if _, err := s.FinishRun(ctx, id, false); err == nil || !strings.Contains(err.Error(), "cannot finish run while work remains") {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, err := s.FinishRun(ctx, id, true); err != nil {
		t.Fatal(err)
	}
}
func TestRequeueRunnerFailures(t *testing.T) {
	ctx := context.Background()
	s, _ := testStore(t)
	run, _ := s.StartRun(ctx)
	id := toIntTest(run["id"])
	ingested, _ := s.IngestJob(ctx, IngestInput{RunID: id, RawURL: "https://jobs.lever.co/acme/retry", Title: "Software Engineer", Location: "Remote, United States", PostedAt: "today", RoleKeywords: []string{"software engineer"}, AllowedLocations: []string{"United States"}, AllowUnverifiableFreshness: true})
	s.RecordApplication(ctx, ApplicationInput{JobKey: ingested.JobKey, Status: "failed", RunID: &id})
	s.RecordFinding(ctx, FindingInput{JobKey: ingested.JobKey, RunID: id, ApplicationStatus: "failed", Stage: "worker", Category: "codex_worker_error", Summary: "worker failed"})
	result, err := s.RequeueRunnerFailures(ctx, id)
	if err != nil || toIntTest(result["count"]) != 1 {
		t.Fatalf("result=%#v err=%v", result, err)
	}
	job, _ := s.GetJob(ctx, ingested.JobKey)
	if job["status"] != "ready_to_apply" {
		t.Fatalf("job: %#v", job)
	}
}
func textValue(v any) string {
	if v == nil {
		return ""
	}
	return v.(string)
}
func toIntTest(v any) int {
	switch n := v.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	}
	return 0
}
