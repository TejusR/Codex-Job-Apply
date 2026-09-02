package dashboard

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/ramesh/codex-job-apply/internal/profile"
	"github.com/ramesh/codex-job-apply/internal/store"
)

type HTTPError struct {
	Status  int
	Message string
}

func (e *HTTPError) Error() string { return e.Message }

type Service struct {
	Store    *store.Store
	RepoRoot string
}

func (s *Service) Runs(ctx context.Context) (store.Row, error) {
	rows, err := s.Store.QueryRows(ctx, `SELECT * FROM runs ORDER BY id DESC`)
	if err != nil {
		return nil, err
	}
	items := []store.Row{}
	var blocked any
	for _, row := range rows {
		summary, err := s.runSummary(ctx, row)
		if err != nil {
			return nil, err
		}
		items = append(items, summary)
		status := text(summary["ui_status"])
		if blocked == nil && (status == "running" || status == "needs_resume") {
			blocked = summary["id"]
		}
	}
	return store.Row{"items": items, "can_start_run": blocked == nil, "blocked_by_run_id": blocked}, nil
}
func (s *Service) RunDetail(ctx context.Context, id int) (store.Row, error) {
	run, err := s.Store.QueryRow(ctx, `SELECT * FROM runs WHERE id=?`, id)
	if err != nil {
		return nil, err
	}
	if run == nil {
		return nil, &HTTPError{404, fmt.Sprintf("Run %d does not exist.", id)}
	}
	summary, err := s.runSummary(ctx, run)
	if err != nil {
		return nil, err
	}
	queries, _ := s.Store.QueryRows(ctx, `SELECT * FROM run_search_queries WHERE run_id=? ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'pending' THEN 1 WHEN 'failed' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END,id`, id)
	sessions, _ := s.Store.QueryRows(ctx, `SELECT * FROM codex_worker_sessions WHERE run_id=? ORDER BY id`, id)
	results, _ := s.Store.QueryRows(ctx, `SELECT * FROM run_search_results WHERE run_id=? ORDER BY id DESC LIMIT 20`, id)
	findings, _ := s.Store.FindingsSummary(ctx, id)
	jobs, _ := s.Jobs(ctx, JobsQuery{RunID: &id, Page: 1, PageSize: 8})
	return store.Row{"summary": summary, "queries": queries, "worker_sessions": sessions, "findings_summary": findings, "recent_search_results": results, "jobs_preview": jobs["items"], "jobs_preview_total": jobs["total"]}, nil
}

type JobsQuery struct {
	RunID             *int
	Status, Source, Q string
	Page, PageSize    int
}

const latestApplicationCTE = `WITH latest_application AS (SELECT applications.* FROM applications JOIN (SELECT job_key,MAX(id) max_id FROM applications GROUP BY job_key) latest ON latest.max_id=applications.id) `

func (s *Service) Jobs(ctx context.Context, q JobsQuery) (store.Row, error) {
	if q.Page < 1 {
		q.Page = 1
	}
	if q.PageSize < 1 {
		q.PageSize = 20
	}
	if q.PageSize > 100 {
		q.PageSize = 100
	}
	filters, args, err := s.jobFilters(ctx, q.RunID, q.Status, q.Source, q.Q)
	if err != nil {
		return nil, err
	}
	where := ""
	if len(filters) > 0 {
		where = " WHERE " + strings.Join(filters, " AND ")
	}
	totalRow, err := s.Store.QueryRow(ctx, latestApplicationCTE+`SELECT COUNT(*) count FROM jobs LEFT JOIN latest_application ON latest_application.job_key=jobs.job_key`+where, args...)
	if err != nil {
		return nil, err
	}
	total := toInt(totalRow["count"])
	sources, err := s.jobSources(ctx, q.RunID, q.Status, q.Q)
	if err != nil {
		return nil, err
	}
	statement := latestApplicationCTE + `SELECT jobs.job_key,jobs.canonical_url,jobs.raw_url,jobs.source,jobs.title,jobs.company,jobs.location,jobs.posted_at,jobs.discovered_at,jobs.status,jobs.status_reason,jobs.last_updated_at,latest_application.status latest_application_status,latest_application.applied_at latest_applied_at,latest_application.run_id latest_application_run_id,latest_application.resume_customization_id latest_resume_customization_id,latest_application.resume_path_used latest_resume_path_used,latest_application.resume_label_used latest_resume_label_used,latest_resume_customization.created_at latest_resume_generated_at FROM jobs LEFT JOIN latest_application ON latest_application.job_key=jobs.job_key LEFT JOIN resume_customizations latest_resume_customization ON latest_resume_customization.id=latest_application.resume_customization_id` + where + ` ORDER BY jobs.last_updated_at DESC,jobs.id DESC LIMIT ? OFFSET ?`
	args = append(args, q.PageSize, (q.Page-1)*q.PageSize)
	rows, err := s.Store.QueryRows(ctx, statement, args...)
	if err != nil {
		return nil, err
	}
	defaults := s.resumeDefaults()
	items := []store.Row{}
	for _, row := range rows {
		items = append(items, jobListItem(row, defaults))
	}
	pages := 1
	if total > 0 {
		pages = int(math.Ceil(float64(total) / float64(q.PageSize)))
	}
	return store.Row{"items": items, "available_sources": sources, "page": q.Page, "page_size": q.PageSize, "total": total, "total_pages": pages}, nil
}
func (s *Service) JobDetail(ctx context.Context, key string) (store.Row, error) {
	row, err := s.Store.QueryRow(ctx, latestApplicationCTE+`SELECT jobs.job_key,jobs.canonical_url,jobs.raw_url,jobs.source,jobs.title,jobs.company,jobs.location,jobs.posted_at,jobs.discovered_at,jobs.status,jobs.status_reason,jobs.last_updated_at,latest_application.status latest_application_status,latest_application.applied_at latest_applied_at,latest_application.run_id latest_application_run_id,latest_application.resume_customization_id latest_resume_customization_id,latest_application.resume_path_used latest_resume_path_used,latest_application.resume_label_used latest_resume_label_used,latest_resume_customization.created_at latest_resume_generated_at FROM jobs LEFT JOIN latest_application ON latest_application.job_key=jobs.job_key LEFT JOIN resume_customizations latest_resume_customization ON latest_resume_customization.id=latest_application.resume_customization_id WHERE jobs.job_key=?`, key)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, &HTTPError{404, fmt.Sprintf("Job %s does not exist.", key)}
	}
	item := jobListItem(row, s.resumeDefaults())
	applications, err := s.Store.QueryRows(ctx, `SELECT applications.*,resume_customizations.created_at resume_generated_at,resume_customizations.rendered_pdf_path resume_rendered_pdf_path FROM applications LEFT JOIN resume_customizations ON resume_customizations.id=applications.resume_customization_id WHERE applications.job_key=? ORDER BY applications.id DESC`, key)
	if err != nil {
		return nil, err
	}
	for i := range applications {
		applications[i]["resume_info"] = applicationResumeInfo(applications[i])
	}
	findings, err := s.Store.QueryRows(ctx, `SELECT * FROM application_findings WHERE job_key=? ORDER BY created_at DESC,id DESC`, key)
	if err != nil {
		return nil, err
	}
	item["application_history"] = applications
	item["findings"] = findings
	return item, nil
}
func (s *Service) ResumeCustomization(ctx context.Context, id int) (store.Row, error) {
	row, err := s.Store.GetResumeCustomization(ctx, id)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, &HTTPError{404, fmt.Sprintf("Resume customization %d does not exist.", id)}
	}
	row["preview_url"] = fmt.Sprintf("/api/resume-customizations/%d", id)
	row["download_url"] = fmt.Sprintf("/api/resume-customizations/%d/file", id)
	return row, nil
}

func (s *Service) StartRun(ctx context.Context) (store.Row, error) {
	overview, err := s.Runs(ctx)
	if err != nil {
		return nil, err
	}
	if blocked := overview["blocked_by_run_id"]; blocked != nil {
		return nil, &HTTPError{409, fmt.Sprintf("Run %d must finish or be resumed before starting another run.", toInt(blocked))}
	}
	prepared, err := s.Store.PrepareRun(ctx, s.RepoRoot)
	if err != nil {
		return nil, &HTTPError{400, err.Error()}
	}
	id := toInt(prepared["run_id"])
	if err = s.launch(id); err != nil {
		return nil, err
	}
	run, err := s.Store.GetRun(ctx, id)
	if err != nil {
		return nil, err
	}
	summary, err := s.runSummary(ctx, run)
	return store.Row{"run": summary, "launched": true}, err
}
func (s *Service) ResumeRun(ctx context.Context, id int) (store.Row, error) {
	run, err := s.Store.GetRun(ctx, id)
	if err != nil {
		return nil, &HTTPError{404, fmt.Sprintf("Run %d does not exist.", id)}
	}
	summary, err := s.runSummary(ctx, run)
	if err != nil {
		return nil, err
	}
	if summary["finished_at"] != nil || summary["has_outstanding_work"] == false {
		return nil, &HTTPError{409, fmt.Sprintf("Run %d has no remaining work to resume.", id)}
	}
	if summary["ui_status"] == "running" {
		return nil, &HTTPError{409, fmt.Sprintf("Run %d is already running.", id)}
	}
	overview, _ := s.Runs(ctx)
	if blocked := overview["blocked_by_run_id"]; blocked != nil && toInt(blocked) != id {
		return nil, &HTTPError{409, fmt.Sprintf("Run %d must finish or be resumed before starting another run.", toInt(blocked))}
	}
	if err = s.launch(id); err != nil {
		return nil, err
	}
	return store.Row{"run": summary, "launched": true}, nil
}
func (s *Service) Requeue(ctx context.Context, id int) (store.Row, error) {
	run, err := s.Store.GetRun(ctx, id)
	if err != nil {
		return nil, &HTTPError{404, fmt.Sprintf("Run %d does not exist.", id)}
	}
	if run["finished_at"] != nil {
		return nil, &HTTPError{409, fmt.Sprintf("Run %d is already finished.", id)}
	}
	result, err := s.Store.RequeueRunnerFailures(ctx, id)
	if err != nil {
		return nil, err
	}
	run, _ = s.Store.GetRun(ctx, id)
	summary, _ := s.runSummary(ctx, run)
	return store.Row{"run": summary, "count": result["count"], "job_keys": result["job_keys"]}, nil
}
func (s *Service) Finish(ctx context.Context, id int, force bool) (store.Row, error) {
	run, err := s.Store.GetRun(ctx, id)
	if err != nil {
		return nil, &HTTPError{404, fmt.Sprintf("Run %d does not exist.", id)}
	}
	summary, _ := s.runSummary(ctx, run)
	if summary["ui_status"] == "running" {
		return nil, &HTTPError{409, fmt.Sprintf("Run %d is still running.", id)}
	}
	if _, err = s.Store.FinishRun(ctx, id, force); err != nil {
		return nil, &HTTPError{409, err.Error()}
	}
	run, _ = s.Store.GetRun(ctx, id)
	summary, _ = s.runSummary(ctx, run)
	return store.Row{"run": summary, "launched": false}, nil
}

func (s *Service) runSummary(ctx context.Context, run store.Row) (store.Row, error) {
	id := toInt(run["id"])
	q, err := countStatuses(ctx, s.Store, "run_search_queries", id)
	if err != nil {
		return nil, err
	}
	r, err := countStatuses(ctx, s.Store, "run_search_results", id)
	if err != nil {
		return nil, err
	}
	workers, err := s.Store.QueryRows(ctx, `SELECT worker_type,COUNT(*) count FROM codex_worker_sessions WHERE run_id=? AND status='running' GROUP BY worker_type`, id)
	if err != nil {
		return nil, err
	}
	workerCounts := map[string]int{"discovery": 0, "apply": 0}
	for _, row := range workers {
		workerCounts[text(row["worker_type"])] = toInt(row["count"])
	}
	notes := decodeNotes(run["notes"])
	keys := stringSlice(notes["seen_job_keys"])
	ready, applying := 0, 0
	if len(keys) > 0 {
		placeholders := strings.TrimRight(strings.Repeat("?,", len(keys)), ",")
		args := make([]any, len(keys))
		for i, key := range keys {
			args[i] = key
		}
		rows, _ := s.Store.QueryRows(ctx, `SELECT status,COUNT(*) count FROM jobs WHERE job_key IN (`+placeholders+`) AND status IN ('ready_to_apply','applying') GROUP BY status`, args...)
		for _, row := range rows {
			if text(row["status"]) == "ready_to_apply" {
				ready = toInt(row["count"])
			} else {
				applying = toInt(row["count"])
			}
		}
	}
	outstanding := ready > 0 || applying > 0 || q["pending"] > 0 || q["in_progress"] > 0 || r["pending"] > 0 || r["processing"] > 0
	hasErrors := toInt(run["jobs_failed"]) > 0 || q["failed"] > 0
	finished := run["finished_at"]
	uiStatus := "needs_resume"
	if finished != nil || !outstanding {
		if hasErrors {
			uiStatus = "completed_with_errors"
		} else {
			uiStatus = "completed"
		}
	} else if workerCounts["discovery"] > 0 || workerCounts["apply"] > 0 {
		uiStatus = "running"
	}
	findingRow, _ := s.Store.QueryRow(ctx, `SELECT COUNT(*) count FROM application_findings WHERE run_id=?`, id)
	canResume := finished == nil && outstanding && uiStatus == "needs_resume"
	canFinish := finished == nil && uiStatus != "running"
	return store.Row{"id": id, "started_at": run["started_at"], "finished_at": finished, "ui_status": uiStatus, "jobs_found": toInt(run["jobs_found"]), "jobs_filtered_in": toInt(run["jobs_filtered_in"]), "jobs_skipped_old": toInt(run["jobs_skipped_old"]), "jobs_skipped_duplicate": toInt(run["jobs_skipped_duplicate"]), "jobs_applied": toInt(run["jobs_applied"]), "jobs_failed": toInt(run["jobs_failed"]), "has_outstanding_work": outstanding, "findings_total": toInt(findingRow["count"]), "search_summary": store.Row{"total_queries": q["total"], "completed_queries": q["completed"], "failed_queries": q["failed"], "pending_queries": q["pending"], "in_progress_queries": q["in_progress"], "pending_search_results": r["pending"], "processing_search_results": r["processing"], "total_search_results": r["total"], "requeued_jobs_count": toInt(notes["requeued_jobs_count"])}, "live_counts": store.Row{"ready_jobs": ready, "applying_jobs": applying, "queries_pending": q["pending"], "queries_in_progress": q["in_progress"], "search_results_pending": r["pending"], "search_results_processing": r["processing"], "discovery_workers_running": workerCounts["discovery"], "apply_workers_running": workerCounts["apply"]}, "allowed_actions": store.Row{"resume": canResume, "requeue_runner_failures": canResume, "finish": canFinish, "force_finish": canFinish && outstanding}}, nil
}

func (s *Service) jobFilters(ctx context.Context, runID *int, status, source, q string) ([]string, []any, error) {
	filters := []string{}
	args := []any{}
	if runID != nil {
		run, err := s.Store.GetRun(ctx, *runID)
		if err != nil {
			return nil, nil, &HTTPError{404, fmt.Sprintf("Run %d does not exist.", *runID)}
		}
		keys := stringSlice(decodeNotes(run["notes"])["seen_job_keys"])
		if len(keys) == 0 {
			return []string{"1=0"}, args, nil
		}
		filters = append(filters, "jobs.job_key IN ("+strings.TrimRight(strings.Repeat("?,", len(keys)), ",")+")")
		for _, key := range keys {
			args = append(args, key)
		}
	}
	if status != "" {
		filters = append(filters, "jobs.status=?")
		args = append(args, status)
	}
	if source != "" {
		filters = append(filters, "jobs.source=?")
		args = append(args, source)
	}
	if strings.TrimSpace(q) != "" {
		filters = append(filters, `(LOWER(COALESCE(jobs.title,'')) LIKE ? OR LOWER(COALESCE(jobs.company,'')) LIKE ? OR LOWER(COALESCE(jobs.location,'')) LIKE ? OR LOWER(COALESCE(jobs.source,'')) LIKE ? OR LOWER(COALESCE(jobs.canonical_url,'')) LIKE ? OR LOWER(COALESCE(jobs.job_key,'')) LIKE ?)`)
		term := "%" + strings.ToLower(strings.TrimSpace(q)) + "%"
		for i := 0; i < 6; i++ {
			args = append(args, term)
		}
	}
	return filters, args, nil
}
func (s *Service) jobSources(ctx context.Context, runID *int, status, q string) ([]string, error) {
	filters, args, err := s.jobFilters(ctx, runID, status, "", q)
	if err != nil {
		return nil, err
	}
	filters = append(filters, "jobs.source IS NOT NULL", "TRIM(jobs.source)<>''")
	rows, err := s.Store.QueryRows(ctx, `SELECT DISTINCT jobs.source FROM jobs WHERE `+strings.Join(filters, " AND ")+` ORDER BY LOWER(jobs.source)`, args...)
	out := []string{}
	for _, row := range rows {
		out = append(out, text(row["source"]))
	}
	return out, err
}

type resumeDefaults struct{ Path, Label any }

func (s *Service) resumeDefaults() resumeDefaults {
	validation := profile.Validate(s.RepoRoot)
	path := validation.Payload().Profile.ResumePath
	if path == nil {
		return resumeDefaults{nil, nil}
	}
	return resumeDefaults{*path, filepath.Base(*path)}
}
func jobListItem(row store.Row, defaults resumeDefaults) store.Row {
	out := store.Row{}
	for _, key := range []string{"job_key", "canonical_url", "raw_url", "source", "title", "company", "location", "posted_at", "discovered_at", "status", "status_reason", "last_updated_at", "latest_application_status", "latest_applied_at", "latest_application_run_id"} {
		out[key] = row[key]
	}
	out["resume_info"] = resumeInfo(row, defaults)
	return out
}
func resumeInfo(row store.Row, defaults resumeDefaults) store.Row {
	id := row["latest_resume_customization_id"]
	path, label, generated := row["latest_resume_path_used"], row["latest_resume_label_used"], row["latest_resume_generated_at"]
	if id != nil {
		if label == nil && path != nil {
			label = filepath.Base(text(path))
		}
		n := toInt(id)
		return store.Row{"path": path, "label": label, "source": "job_tailored", "customization_id": n, "generated_at": generated, "preview_url": fmt.Sprintf("/api/resume-customizations/%d", n), "download_url": fmt.Sprintf("/api/resume-customizations/%d/file", n)}
	}
	if path != nil || label != nil {
		if label == nil && path != nil {
			label = filepath.Base(text(path))
		}
		return store.Row{"path": path, "label": label, "source": "application_snapshot", "customization_id": nil, "generated_at": nil, "preview_url": nil, "download_url": nil}
	}
	return store.Row{"path": defaults.Path, "label": defaults.Label, "source": "default_profile", "customization_id": nil, "generated_at": nil, "preview_url": nil, "download_url": nil}
}
func applicationResumeInfo(row store.Row) any {
	id := row["resume_customization_id"]
	path := row["resume_rendered_pdf_path"]
	if path == nil {
		path = row["resume_path_used"]
	}
	label := row["resume_label_used"]
	if id != nil {
		if label == nil && path != nil {
			label = filepath.Base(text(path))
		}
		n := toInt(id)
		return store.Row{"path": path, "label": label, "source": "job_tailored", "customization_id": n, "generated_at": row["resume_generated_at"], "preview_url": fmt.Sprintf("/api/resume-customizations/%d", n), "download_url": fmt.Sprintf("/api/resume-customizations/%d/file", n)}
	}
	if path != nil || label != nil {
		return store.Row{"path": path, "label": label, "source": "application_snapshot", "customization_id": nil, "generated_at": nil, "preview_url": nil, "download_url": nil}
	}
	return nil
}

func (s *Service) launch(runID int) error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	logDir := filepath.Join(filepath.Dir(s.Store.Path()), "dashboard_logs")
	if err = os.MkdirAll(logDir, 0o755); err != nil {
		return err
	}
	logPath := filepath.Join(logDir, fmt.Sprintf("run-%d.log", runID))
	file, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	cmd := exec.Command(exe, "--db-path", s.Store.Path(), "run-workflow", "--repo-root", s.RepoRoot, "--run-id", fmt.Sprint(runID))
	cmd.Dir = s.RepoRoot
	cmd.Stdout = file
	cmd.Stderr = file
	detach(cmd)
	if err = cmd.Start(); err != nil {
		file.Close()
		return err
	}
	file.Close()
	return nil
}

func countStatuses(ctx context.Context, s *store.Store, table string, runID int) (map[string]int, error) {
	rows, err := s.QueryRows(ctx, `SELECT status,COUNT(*) count FROM `+table+` WHERE run_id=? GROUP BY status`, runID)
	out := map[string]int{"pending": 0, "in_progress": 0, "completed": 0, "failed": 0, "processing": 0, "total": 0}
	for _, row := range rows {
		n := toInt(row["count"])
		out[text(row["status"])] = n
		out["total"] += n
	}
	return out, err
}
func decodeNotes(value any) map[string]any {
	out := map[string]any{}
	if raw := text(value); raw != "" {
		json.Unmarshal([]byte(raw), &out)
	}
	return out
}
func stringSlice(value any) []string {
	out := []string{}
	if values, ok := value.([]any); ok {
		for _, item := range values {
			out = append(out, text(item))
		}
	}
	return out
}
func toInt(value any) int {
	switch n := value.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	}
	return 0
}
func text(value any) string {
	if value == nil {
		return ""
	}
	return fmt.Sprint(value)
}
