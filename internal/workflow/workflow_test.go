package workflow

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/ramesh/codex-job-apply/internal/store"
)

func TestCodexHelperProcess(t *testing.T) {
	if os.Getenv("GO_WANT_CODEX_HELPER") != "1" {
		return
	}
	input, _ := io.ReadAll(os.Stdin)
	prompt := string(input)
	scenario := os.Getenv("CODEX_HELPER_SCENARIO")
	args := os.Args
	resultPath := ""
	for i, arg := range args {
		if arg == "-o" && i+1 < len(args) {
			resultPath = args[i+1]
		}
	}
	var payload any
	switch {
	case strings.Contains(prompt, "current_run_seen_urls") || strings.Contains(prompt, "query_text"):
		if scenario == "query-failure" && strings.Contains(prompt, `"source_key": "ashby"`) {
			payload = map[string]any{"outcome": "query_failed", "results": []any{}, "next_page": nil, "query_error": "simulated query failure"}
			break
		}
		url := "https://boards.greenhouse.io/acme/jobs/123"
		page := 1
		var next any
		if scenario == "listing" {
			url = "https://boards.greenhouse.io/acme"
		}
		if scenario == "multi-page" {
			if strings.Contains(prompt, `"page_number": 2`) {
				url = "https://boards.greenhouse.io/acme/jobs/456"
				page = 2
			} else {
				next = map[string]any{"page_number": 2}
			}
		}
		payload = map[string]any{"outcome": "results_page", "results": []any{map[string]any{"url": url, "title": "Software Engineer", "snippet": "Backend role", "visible_date": "today", "page_number": page, "rank": 1}}, "next_page": next, "query_error": nil}
	case strings.Contains(prompt, "search_result"):
		if scenario == "listing" && strings.Contains(prompt, `"url": "https://boards.greenhouse.io/acme"`) {
			child := map[string]any{"url": "https://boards.greenhouse.io/acme/jobs/123", "title": "Software Engineer", "snippet": "Child", "visible_date": "today", "page_number": 1, "rank": 1}
			payload = map[string]any{"outcome": "expanded", "job": nil, "child_results": []any{child, child}, "skip_reason": nil, "error_message": nil}
		} else {
			url := "https://boards.greenhouse.io/acme/jobs/123"
			if strings.Contains(prompt, "/456") {
				url = "https://boards.greenhouse.io/acme/jobs/456"
			}
			payload = map[string]any{"outcome": "resolved_job", "job": map[string]any{"raw_url": url, "canonical_url": nil, "source": "greenhouse", "title": "Software Engineer", "company": "Acme", "location": "Remote, United States", "posted_at": "today", "description_text": "Build backend systems", "page_url": url}, "child_results": []any{}, "skip_reason": nil, "error_message": nil}
		}
	case strings.Contains(prompt, "allowed_application_statuses"):
		payload = map[string]any{"application_status": "submitted", "confirmation_text": "Application submitted", "confirmation_url": "https://boards.greenhouse.io/acme/jobs/123/confirmation", "error_message": nil, "findings": []any{}}
	default:
		payload = map[string]any{"outcome": "query_failed", "results": []any{}, "next_page": nil, "query_error": "unknown helper prompt"}
	}
	data, _ := json.Marshal(payload)
	os.MkdirAll(filepath.Dir(resultPath), 0o755)
	os.WriteFile(resultPath, data, 0o600)
	fmt.Println(`{"type":"thread.started","thread_id":"helper-thread"}`)
	os.Exit(0)
}

func fakeCodex(t *testing.T) string {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	var path, content string
	if runtime.GOOS == "windows" {
		path = filepath.Join(root, "fake-codex.cmd")
		content = fmt.Sprintf("@echo off\r\n\"%s\" -test.run=TestCodexHelperProcess -- %%*\r\n", exe)
	} else {
		path = filepath.Join(root, "fake-codex")
		content = fmt.Sprintf("#!/bin/sh\n\"%s\" -test.run=TestCodexHelperProcess -- \"$@\"\n", exe)
	}
	if err = os.WriteFile(path, []byte(content), 0o700); err != nil {
		t.Fatal(err)
	}
	return path
}
func workflowRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	os.Mkdir(filepath.Join(root, "PROMPTS"), 0o755)
	for _, name := range []string{"CODEX_QUERY_WORKER_PROMPT.md", "CODEX_RESOLVE_WORKER_PROMPT.md", "CODEX_APPLY_WORKER_PROMPT.md"} {
		os.WriteFile(filepath.Join(root, "PROMPTS", name), []byte("worker prompt"), 0o600)
	}
	for _, name := range []string{"CODEX_QUERY_WORKER_SCHEMA.json", "CODEX_RESOLVE_WORKER_SCHEMA.json", "CODEX_APPLY_WORKER_SCHEMA.json"} {
		os.WriteFile(filepath.Join(root, "PROMPTS", name), []byte(`{"type":"object"}`), 0o600)
	}
	os.WriteFile(filepath.Join(root, "resume.pdf"), []byte("pdf"), 0o600)
	os.WriteFile(filepath.Join(root, ".env"), []byte(`APPLICANT_FULL_NAME=Test Person
APPLICANT_EMAIL=test@example.com
APPLICANT_PHONE=555
APPLICANT_LOCATION=Tempe, AZ
APPLICANT_RESUME_PATH=resume.pdf
APPLICANT_US_WORK_AUTHORIZED=true
APPLICANT_REQUIRES_VISA_SPONSORSHIP=false
APPLICANT_TARGET_ROLE_KEYWORDS=software engineer
APPLICANT_ALLOWED_LOCATIONS=United States, Remote
APPLICANT_ENABLED_SEARCH_SITES=greenhouse
APPLICANT_DISCOVERY_MAX_PAGES=1
`), 0o600)
	os.WriteFile(filepath.Join(root, "applicant.md"), []byte("## Work Authorization Notes\nYes\n\n## Reusable Highlights\nBackend systems\n"), 0o600)
	return root
}
func TestRunWorkflowPipelinesDiscoveryResolutionAndApply(t *testing.T) {
	t.Setenv("GO_WANT_CODEX_HELPER", "1")
	root := workflowRoot(t)
	database, err := store.Open(filepath.Join(root, "jobs.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	service := New(Config{Store: database, RepoRoot: root, CodexBin: fakeCodex(t), MaxWorkerRetries: 1, ApplyWorkers: 2})
	result, err := service.Run(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	if int(result["jobs_applied"].(int64)) != 1 {
		queries, _ := database.QueryRows(context.Background(), `SELECT * FROM run_search_queries`)
		attempts, _ := database.QueryRows(context.Background(), `SELECT * FROM codex_worker_attempts`)
		logText := ""
		if len(attempts) > 0 {
			data, _ := os.ReadFile(fmt.Sprint(attempts[0]["log_path"]))
			logText = string(data)
		}
		t.Fatalf("result: %#v queries=%#v attempts=%#v log=%s", result, queries, attempts, logText)
	}
	applications, err := database.QueryRows(context.Background(), `SELECT * FROM applications`)
	if err != nil || len(applications) != 1 || applications[0]["status"] != "submitted" {
		t.Fatalf("applications=%#v err=%v", applications, err)
	}
	queries, _ := database.QueryRows(context.Background(), `SELECT * FROM run_search_queries`)
	if len(queries) != 1 || queries[0]["status"] != "completed" {
		t.Fatalf("queries: %#v", queries)
	}
	results, _ := database.QueryRows(context.Background(), `SELECT * FROM run_search_results`)
	if len(results) != 1 || results[0]["status"] != "applied" {
		t.Fatalf("results: %#v", results)
	}
}
func TestWorkerPayloadValidators(t *testing.T) {
	validQuery := map[string]any{"outcome": "exhausted", "results": []any{}, "next_page": nil, "query_error": nil}
	if err := validateQuery(validQuery); err != nil {
		t.Fatal(err)
	}
	invalidApply := map[string]any{"application_status": "failed", "confirmation_text": nil, "confirmation_url": nil, "error_message": "failed", "findings": []any{}}
	if err := validateApply(invalidApply); err == nil {
		t.Fatal("expected unsuccessful application without findings to fail")
	}
}

func TestMultiPageDiscoveryResumesSession(t *testing.T) {
	t.Setenv("GO_WANT_CODEX_HELPER", "1")
	t.Setenv("CODEX_HELPER_SCENARIO", "multi-page")
	root := workflowRoot(t)
	envPath := filepath.Join(root, ".env")
	data, _ := os.ReadFile(envPath)
	data = []byte(strings.Replace(string(data), "APPLICANT_DISCOVERY_MAX_PAGES=1", "APPLICANT_DISCOVERY_MAX_PAGES=2", 1))
	os.WriteFile(envPath, data, 0o600)
	database, _ := store.Open(filepath.Join(root, "jobs.sqlite3"))
	defer database.Close()
	service := New(Config{Store: database, RepoRoot: root, CodexBin: fakeCodex(t), MaxWorkerRetries: 1, ApplyWorkers: 2})
	result, err := service.Run(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	if int(result["jobs_applied"].(int64)) != 2 {
		t.Fatalf("result: %#v", result)
	}
	query, _ := database.QueryRow(context.Background(), `SELECT * FROM run_search_queries LIMIT 1`)
	if query["results_seen"].(int64) != 2 {
		t.Fatalf("query: %#v", query)
	}
	session, _ := database.QueryRow(context.Background(), `SELECT * FROM codex_worker_sessions WHERE worker_type='discovery'`)
	if session["thread_id"] != "helper-thread" {
		t.Fatalf("session: %#v", session)
	}
}
func TestListingExpansionDeduplicatesChildren(t *testing.T) {
	t.Setenv("GO_WANT_CODEX_HELPER", "1")
	t.Setenv("CODEX_HELPER_SCENARIO", "listing")
	root := workflowRoot(t)
	database, _ := store.Open(filepath.Join(root, "jobs.sqlite3"))
	defer database.Close()
	service := New(Config{Store: database, RepoRoot: root, CodexBin: fakeCodex(t), MaxWorkerRetries: 1, ApplyWorkers: 2})
	result, err := service.Run(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	if int(result["jobs_applied"].(int64)) != 1 {
		t.Fatalf("result: %#v", result)
	}
	results, _ := database.QueryRows(context.Background(), `SELECT * FROM run_search_results ORDER BY id`)
	if len(results) != 2 || results[0]["status"] != "expanded" || results[1]["status"] != "applied" {
		t.Fatalf("results: %#v", results)
	}
}

func TestQueryFailureIsIsolated(t *testing.T) {
	t.Setenv("GO_WANT_CODEX_HELPER", "1")
	t.Setenv("CODEX_HELPER_SCENARIO", "query-failure")
	root := workflowRoot(t)
	envPath := filepath.Join(root, ".env")
	data, _ := os.ReadFile(envPath)
	data = []byte(strings.Replace(string(data), "APPLICANT_ENABLED_SEARCH_SITES=greenhouse", "APPLICANT_ENABLED_SEARCH_SITES=greenhouse, ashby", 1))
	if err := os.WriteFile(envPath, data, 0o600); err != nil {
		t.Fatal(err)
	}
	database, err := store.Open(filepath.Join(root, "jobs.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()
	service := New(Config{Store: database, RepoRoot: root, CodexBin: fakeCodex(t), MaxWorkerRetries: 1, ApplyWorkers: 2})
	result, err := service.Run(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	summary := result["search_summary"].(store.Row)
	if summary["failed_queries"] != 1 || summary["completed_queries"] != 1 {
		t.Fatalf("search summary: %#v", summary)
	}
	if result["jobs_applied"].(int64) != 1 {
		t.Fatalf("result: %#v", result)
	}
}
