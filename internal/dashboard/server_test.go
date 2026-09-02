package dashboard

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/ramesh/codex-job-apply/internal/store"
)

func dashboardFixture(t *testing.T) (*store.Store, *Server, string) {
	t.Helper()
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "resume.pdf"), []byte("pdf"), 0o600)
	os.WriteFile(filepath.Join(root, ".env"), []byte(`APPLICANT_FULL_NAME=Test Person
APPLICANT_EMAIL=test@example.com
APPLICANT_PHONE=555
APPLICANT_LOCATION=Tempe, AZ
APPLICANT_RESUME_PATH=resume.pdf
APPLICANT_US_WORK_AUTHORIZED=true
APPLICANT_REQUIRES_VISA_SPONSORSHIP=false
`), 0o600)
	os.WriteFile(filepath.Join(root, "applicant.md"), []byte("## Work Authorization Notes\nYes\n\n## Reusable Highlights\nYes\n"), 0o600)
	s, err := store.Open(filepath.Join(root, "jobs.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s, &Server{Service: &Service{Store: s, RepoRoot: root}}, root
}
func requestJSON(t *testing.T, handler http.Handler, method, path string) (int, map[string]any) {
	t.Helper()
	request := httptest.NewRequest(method, path, nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	payload := map[string]any{}
	json.Unmarshal(response.Body.Bytes(), &payload)
	return response.Code, payload
}
func TestHealthAndEmptyRuns(t *testing.T) {
	_, server, _ := dashboardFixture(t)
	handler := server.Handler()
	status, payload := requestJSON(t, handler, "GET", "/api/health")
	if status != 200 || payload["status"] != "ok" {
		t.Fatalf("status=%d payload=%#v", status, payload)
	}
	status, payload = requestJSON(t, handler, "GET", "/api/runs")
	if status != 200 || payload["can_start_run"] != true || payload["blocked_by_run_id"] != nil {
		t.Fatalf("payload=%#v", payload)
	}
}
func TestRunAndJobEndpoints(t *testing.T) {
	s, server, _ := dashboardFixture(t)
	ctx := context.Background()
	run, _ := s.StartRun(ctx)
	id := int(run["id"].(int64))
	ingested, err := s.IngestJob(ctx, store.IngestInput{RunID: id, RawURL: "https://jobs.lever.co/acme/123", Title: "Software Engineer", Company: "Acme", Location: "Remote, United States", PostedAt: "today", RoleKeywords: []string{"software engineer"}, AllowedLocations: []string{"United States"}, AllowUnverifiableFreshness: true})
	if err != nil {
		t.Fatal(err)
	}
	handler := server.Handler()
	status, payload := requestJSON(t, handler, "GET", "/api/runs")
	if status != 200 || payload["can_start_run"] != false {
		t.Fatalf("payload=%#v", payload)
	}
	status, payload = requestJSON(t, handler, "GET", "/api/runs/"+jsonNumber(id))
	if status != 200 {
		t.Fatalf("status=%d payload=%#v", status, payload)
	}
	status, payload = requestJSON(t, handler, "GET", "/api/jobs?run_id="+jsonNumber(id))
	if status != 200 || payload["total"].(float64) != 1 {
		t.Fatalf("payload=%#v", payload)
	}
	status, payload = requestJSON(t, handler, "GET", "/api/jobs/"+ingested.JobKey)
	if status != 200 {
		t.Fatalf("payload=%#v", payload)
	}
	resume := payload["resume_info"].(map[string]any)
	if resume["source"] != "default_profile" || resume["path"] == nil {
		t.Fatalf("resume=%#v", resume)
	}
}
func TestResumeCustomizationAndErrors(t *testing.T) {
	s, server, root := dashboardFixture(t)
	ctx := context.Background()
	run, _ := s.StartRun(ctx)
	id := int(run["id"].(int64))
	ingested, _ := s.IngestJob(ctx, store.IngestInput{RunID: id, RawURL: "https://jobs.lever.co/acme/resume", Title: "Software Engineer", Location: "Remote, United States", PostedAt: "today", RoleKeywords: []string{"software engineer"}, AllowedLocations: []string{"United States"}, AllowUnverifiableFreshness: true})
	pdf := filepath.Join(root, "tailored.pdf")
	os.WriteFile(pdf, []byte("pdf"), 0o600)
	custom, err := s.CreateResumeCustomization(ctx, store.ResumeCustomizationInput{JobKey: ingested.JobKey, RunID: &id, Status: "succeeded", RenderedPDFPath: pdf, PreviewContent: "preview"})
	if err != nil {
		t.Fatal(err)
	}
	customID := int(custom["id"].(int64))
	handler := server.Handler()
	status, payload := requestJSON(t, handler, "GET", "/api/resume-customizations/"+jsonNumber(customID))
	if status != 200 || payload["download_url"] == nil {
		t.Fatalf("payload=%#v", payload)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "/api/resume-customizations/"+jsonNumber(customID)+"/file", nil))
	if response.Code != 200 || response.Body.String() != "pdf" {
		t.Fatalf("code=%d body=%q", response.Code, response.Body.String())
	}
	status, payload = requestJSON(t, handler, "GET", "/api/jobs/missing")
	if status != 404 || payload["detail"] == nil {
		t.Fatalf("status=%d payload=%#v", status, payload)
	}
}
func jsonNumber(v int) string { return fmt.Sprint(v) }
