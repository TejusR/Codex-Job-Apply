package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/ramesh/codex-job-apply/internal/jobs"
)

type SearchResultInput struct {
	URL, Title, Snippet, VisibleDate string
	PageNumber, Rank                 int
}

func (s *Store) InsertSearchResults(ctx context.Context, runID int, source string, parentID *int, origin string, results []SearchResultInput) (Row, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	inserted, duplicates := 0, 0
	for _, item := range results {
		result, err := tx.ExecContext(ctx, `INSERT OR IGNORE INTO run_search_results(run_id,source_key,parent_result_id,origin_kind,url,title,snippet,visible_date,page_number,rank,status) VALUES(?,?,?,?,?,?,?,?,?,?,'pending')`, runID, source, parentID, origin, item.URL, nullableString(item.Title), nullableString(item.Snippet), nullableString(item.VisibleDate), item.PageNumber, item.Rank)
		if err != nil {
			return nil, err
		}
		n, _ := result.RowsAffected()
		if n == 1 {
			inserted++
		} else {
			duplicates++
		}
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return Row{"run_id": runID, "source_key": source, "inserted": inserted, "duplicates": duplicates}, nil
}

func (s *Store) ClaimSearchResult(ctx context.Context, runID int, claimedBy string) (Row, error) {
	for {
		tx, err := s.db.BeginTx(ctx, nil)
		if err != nil {
			return nil, err
		}
		row, err := queryRow(ctx, tx, `SELECT * FROM run_search_results WHERE run_id=? AND status='pending' ORDER BY id LIMIT 1`, runID)
		if err != nil {
			tx.Rollback()
			return nil, err
		}
		if row == nil {
			tx.Rollback()
			return nil, nil
		}
		now := jobs.FormatTimestamp(jobs.UTCNow())
		result, err := tx.ExecContext(ctx, `UPDATE run_search_results SET status='processing',claimed_by=?,claimed_at=?,finished_at=NULL,reason=NULL WHERE id=? AND status='pending'`, claimedBy, now, row["id"])
		if err != nil {
			tx.Rollback()
			return nil, err
		}
		n, _ := result.RowsAffected()
		if n != 1 {
			tx.Rollback()
			continue
		}
		row, err = queryRow(ctx, tx, `SELECT * FROM run_search_results WHERE id=?`, row["id"])
		if err == nil {
			err = tx.Commit()
		} else {
			tx.Rollback()
		}
		return row, err
	}
}
func (s *Store) UpdateSearchResult(ctx context.Context, id int, status string, reason, jobKey *string) (Row, error) {
	row, err := s.GetSearchResult(ctx, id)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, fmt.Errorf("search result %d does not exist", id)
	}
	terminal := status != "pending" && status != "processing"
	var finished any
	if terminal {
		finished = jobs.FormatTimestamp(jobs.UTCNow())
	}
	claimedBy, claimedAt := row["claimed_by"], row["claimed_at"]
	if status == "pending" {
		claimedBy = nil
		claimedAt = nil
	}
	_, err = s.db.ExecContext(ctx, `UPDATE run_search_results SET status=?,reason=?,job_key=COALESCE(?,job_key),finished_at=?,claimed_by=?,claimed_at=? WHERE id=?`, status, reason, jobKey, finished, claimedBy, claimedAt, id)
	if err != nil {
		return nil, err
	}
	return s.GetSearchResult(ctx, id)
}
func (s *Store) GetSearchResult(ctx context.Context, id int) (Row, error) {
	return queryRow(ctx, s.db, `SELECT * FROM run_search_results WHERE id=?`, id)
}
func (s *Store) ListSearchResults(ctx context.Context, runID int, source, status string, limit int) ([]Row, error) {
	where := []string{"run_id=?"}
	args := []any{runID}
	if source != "" {
		where = append(where, "source_key=?")
		args = append(args, source)
	}
	if status != "" {
		where = append(where, "status=?")
		args = append(args, status)
	}
	statement := `SELECT * FROM run_search_results WHERE ` + strings.Join(where, " AND ") + ` ORDER BY id`
	if limit > 0 {
		statement += ` LIMIT ?`
		args = append(args, limit)
	}
	return queryRows(ctx, s.db, statement, args...)
}
func (s *Store) ListRunSeenURLs(ctx context.Context, runID int) ([]string, error) {
	rows, err := queryRows(ctx, s.db, `SELECT url FROM run_search_results WHERE run_id=? UNION SELECT canonical_url AS url FROM jobs ORDER BY url`, runID)
	out := []string{}
	for _, r := range rows {
		out = append(out, asString(r["url"]))
	}
	return out, err
}
func (s *Store) RecordSkippedResult(ctx context.Context, runID int, source, url, reason string) (Row, error) {
	now := jobs.FormatTimestamp(jobs.UTCNow())
	_, err := s.db.ExecContext(ctx, `INSERT INTO run_query_skipped_results(run_id,source_key,url,reason,created_at) VALUES(?,?,?,?,?) ON CONFLICT(run_id,source_key,url) DO UPDATE SET reason=excluded.reason,created_at=excluded.created_at`, runID, source, url, reason, now)
	if err != nil {
		return nil, err
	}
	return queryRow(ctx, s.db, `SELECT * FROM run_query_skipped_results WHERE run_id=? AND source_key=? AND url=?`, runID, source, url)
}

func (s *Store) EnsureWorkerSession(ctx context.Context, runID int, workerType, slot string) (Row, error) {
	now := jobs.FormatTimestamp(jobs.UTCNow())
	_, err := s.db.ExecContext(ctx, `INSERT OR IGNORE INTO codex_worker_sessions(run_id,worker_type,slot_key,status,started_at,last_used_at) VALUES(?,?,?,'idle',?,?)`, runID, workerType, slot, now, now)
	if err != nil {
		return nil, err
	}
	return s.GetWorkerSession(ctx, runID, workerType, slot)
}
func (s *Store) GetWorkerSession(ctx context.Context, runID int, workerType, slot string) (Row, error) {
	return queryRow(ctx, s.db, `SELECT * FROM codex_worker_sessions WHERE run_id=? AND worker_type=? AND slot_key=?`, runID, workerType, slot)
}
func (s *Store) UpdateWorkerSession(ctx context.Context, runID int, workerType, slot, status string, threadID, lastError *string) (Row, error) {
	if _, err := s.EnsureWorkerSession(ctx, runID, workerType, slot); err != nil {
		return nil, err
	}
	_, err := s.db.ExecContext(ctx, `UPDATE codex_worker_sessions SET status=?,thread_id=COALESCE(?,thread_id),last_used_at=?,last_error=? WHERE run_id=? AND worker_type=? AND slot_key=?`, status, threadID, jobs.FormatTimestamp(jobs.UTCNow()), lastError, runID, workerType, slot)
	if err != nil {
		return nil, err
	}
	return s.GetWorkerSession(ctx, runID, workerType, slot)
}
func (s *Store) ResetWorkerSessions(ctx context.Context, runID int) (int, error) {
	result, err := s.db.ExecContext(ctx, `UPDATE codex_worker_sessions SET status='idle',last_error=CASE WHEN status='running' THEN COALESCE(last_error,'requeued_from_interrupted_run') ELSE last_error END,last_used_at=? WHERE run_id=?`, jobs.FormatTimestamp(jobs.UTCNow()), runID)
	if err != nil {
		return 0, err
	}
	n, _ := result.RowsAffected()
	return int(n), nil
}
func (s *Store) StartWorkerAttempt(ctx context.Context, runID int, workerType, target string, attempt int, resultPath, logPath string) (Row, error) {
	result, err := s.db.ExecContext(ctx, `INSERT INTO codex_worker_attempts(run_id,worker_type,target_key,attempt_number,status,started_at,result_path,log_path) VALUES(?,?,?,?,'running',?,?,?)`, runID, workerType, target, attempt, jobs.FormatTimestamp(jobs.UTCNow()), resultPath, logPath)
	if err != nil {
		return nil, err
	}
	id, _ := result.LastInsertId()
	return queryRow(ctx, s.db, `SELECT * FROM codex_worker_attempts WHERE id=?`, id)
}
func (s *Store) FinishWorkerAttempt(ctx context.Context, id int, status string, exitCode *int, errorMessage *string) (Row, error) {
	result, err := s.db.ExecContext(ctx, `UPDATE codex_worker_attempts SET status=?,exit_code=?,error_message=?,finished_at=? WHERE id=?`, status, exitCode, errorMessage, jobs.FormatTimestamp(jobs.UTCNow()), id)
	if err != nil {
		return nil, err
	}
	n, _ := result.RowsAffected()
	if n == 0 {
		return nil, fmt.Errorf("worker attempt %d does not exist", id)
	}
	return queryRow(ctx, s.db, `SELECT * FROM codex_worker_attempts WHERE id=?`, id)
}

type ApplicationInput struct {
	JobKey, Status, ConfirmationText, ConfirmationURL, ErrorMessage, ResumePathUsed, ResumeLabelUsed string
	RunID, ResumeCustomizationID                                                                     *int
}

func (s *Store) RecordApplication(ctx context.Context, input ApplicationInput) (Row, error) {
	allowed := map[string]bool{"submitted": true, "failed": true, "incomplete": true, "duplicate_skipped": true, "blocked": true}
	if !allowed[input.Status] {
		return nil, fmt.Errorf("unsupported application status: %s", input.Status)
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	job, err := queryRow(ctx, tx, `SELECT * FROM jobs WHERE job_key=?`, input.JobKey)
	if err != nil {
		return nil, err
	}
	if job == nil {
		return nil, fmt.Errorf("job %s does not exist", input.JobKey)
	}
	now := jobs.FormatTimestamp(jobs.UTCNow())
	result, err := tx.ExecContext(ctx, `INSERT INTO applications(job_key,applied_at,run_id,status,confirmation_text,confirmation_url,resume_customization_id,resume_path_used,resume_label_used,error_message) VALUES(?,?,?,?,?,?,?,?,?,?)`, input.JobKey, now, input.RunID, input.Status, nullableString(input.ConfirmationText), nullableString(input.ConfirmationURL), input.ResumeCustomizationID, nullableString(input.ResumePathUsed), nullableString(input.ResumeLabelUsed), nullableString(input.ErrorMessage))
	if err != nil {
		return nil, err
	}
	jobStatus, reason := mapApplicationStatus(input.Status, input.ErrorMessage)
	if _, err = tx.ExecContext(ctx, `UPDATE jobs SET status=?,status_reason=?,last_updated_at=? WHERE job_key=?`, jobStatus, reason, now, input.JobKey); err != nil {
		return nil, err
	}
	if input.RunID != nil {
		column := ""
		switch input.Status {
		case "submitted":
			column = "jobs_applied"
		case "failed", "incomplete", "blocked":
			column = "jobs_failed"
		case "duplicate_skipped":
			column = "jobs_skipped_duplicate"
		}
		if column != "" {
			if _, err = tx.ExecContext(ctx, `UPDATE runs SET `+column+`=`+column+`+1 WHERE id=?`, *input.RunID); err != nil {
				return nil, err
			}
		}
	}
	id, _ := result.LastInsertId()
	row, err := queryRow(ctx, tx, `SELECT * FROM applications WHERE id=?`, id)
	if err == nil {
		err = tx.Commit()
	}
	return row, err
}
func mapApplicationStatus(status, errorMessage string) (string, *string) {
	var jobStatus, defaultReason string
	switch status {
	case "submitted":
		return "applied", nil
	case "duplicate_skipped":
		jobStatus = "duplicate_skipped"
		defaultReason = "duplicate_skip_recorded"
	case "blocked":
		jobStatus = "blocked"
		defaultReason = "application_flow_blocked"
	case "incomplete":
		jobStatus = "incomplete"
		defaultReason = "missing_required_application_data"
	default:
		jobStatus = "failed"
		defaultReason = "application_submission_failed"
	}
	if strings.TrimSpace(errorMessage) != "" {
		defaultReason = errorMessage
	}
	return jobStatus, &defaultReason
}

type FindingInput struct {
	JobKey                                                       string
	RunID                                                        int
	ApplicationStatus, Stage, Category, Summary, Detail, PageURL string
}

func (s *Store) RecordFinding(ctx context.Context, input FindingInput) (Row, error) {
	if input.ApplicationStatus != "failed" && input.ApplicationStatus != "incomplete" && input.ApplicationStatus != "blocked" {
		return nil, fmt.Errorf("unsupported finding application status: %s", input.ApplicationStatus)
	}
	result, err := s.db.ExecContext(ctx, `INSERT INTO application_findings(job_key,run_id,application_status,stage,category,summary,detail,page_url,created_at) SELECT ?,?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM jobs WHERE job_key=?) AND EXISTS(SELECT 1 FROM runs WHERE id=?)`, input.JobKey, input.RunID, input.ApplicationStatus, input.Stage, input.Category, input.Summary, nullableString(input.Detail), nullableString(input.PageURL), jobs.FormatTimestamp(jobs.UTCNow()), input.JobKey, input.RunID)
	if err != nil {
		return nil, err
	}
	n, _ := result.RowsAffected()
	if n == 0 {
		return nil, fmt.Errorf("job %s or run %d does not exist", input.JobKey, input.RunID)
	}
	id, _ := result.LastInsertId()
	return queryRow(ctx, s.db, `SELECT * FROM application_findings WHERE id=?`, id)
}

func (s *Store) RequeueRunnerFailures(ctx context.Context, runID int) (Row, error) {
	rows, err := queryRows(ctx, s.db, `WITH latest_failed_applications AS (SELECT a.job_key,a.status FROM applications a JOIN (SELECT job_key,MAX(id) max_id FROM applications GROUP BY job_key) latest ON latest.max_id=a.id),latest_run_findings AS (SELECT f.job_key,f.category,f.application_status FROM application_findings f JOIN (SELECT job_key,MAX(id) max_id FROM application_findings WHERE run_id=? GROUP BY job_key) latest ON latest.max_id=f.id) SELECT jobs.job_key FROM jobs JOIN latest_failed_applications a ON a.job_key=jobs.job_key JOIN latest_run_findings f ON f.job_key=jobs.job_key WHERE a.status='failed' AND f.application_status='failed' AND f.category='codex_worker_error' ORDER BY jobs.last_updated_at DESC,jobs.job_key`, runID)
	if err != nil {
		return nil, err
	}
	keys := []string{}
	for _, r := range rows {
		keys = append(keys, asString(r["job_key"]))
	}
	if len(keys) > 0 {
		placeholders := strings.TrimRight(strings.Repeat("?,", len(keys)), ",")
		args := []any{jobs.FormatTimestamp(jobs.UTCNow())}
		for _, key := range keys {
			args = append(args, key)
		}
		_, err = s.db.ExecContext(ctx, `UPDATE jobs SET status='ready_to_apply',status_reason='requeued_runner_failure',last_updated_at=? WHERE job_key IN (`+placeholders+`)`, args...)
	}
	return Row{"run_id": runID, "count": len(keys), "job_keys": keys}, err
}

type ResumeCustomizationInput struct {
	JobKey                                                                                                                                string
	RunID                                                                                                                                 *int
	Status, SourceTemplatePath, JobDescriptionHash, RenderedTexPath, RenderedPDFPath, PreviewContent, PayloadJSON, Compiler, ErrorMessage string
}

func (s *Store) CreateResumeCustomization(ctx context.Context, input ResumeCustomizationInput) (Row, error) {
	result, err := s.db.ExecContext(ctx, `INSERT INTO resume_customizations(job_key,run_id,status,created_at,source_template_path,job_description_hash,rendered_tex_path,rendered_pdf_path,preview_content,customization_payload_json,compiler,error_message) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`, input.JobKey, input.RunID, input.Status, jobs.FormatTimestamp(jobs.UTCNow()), nullableString(input.SourceTemplatePath), nullableString(input.JobDescriptionHash), nullableString(input.RenderedTexPath), nullableString(input.RenderedPDFPath), nullableString(input.PreviewContent), nullableString(input.PayloadJSON), nullableString(input.Compiler), nullableString(input.ErrorMessage))
	if err != nil {
		return nil, err
	}
	id, _ := result.LastInsertId()
	return s.GetResumeCustomization(ctx, int(id))
}
func (s *Store) UpdateResumeCustomization(ctx context.Context, id int, fields map[string]any) (Row, error) {
	allowed := map[string]bool{"status": true, "rendered_tex_path": true, "rendered_pdf_path": true, "preview_content": true, "customization_payload_json": true, "compiler": true, "error_message": true}
	sets := []string{}
	args := []any{}
	for key, value := range fields {
		if !allowed[key] {
			continue
		}
		sets = append(sets, key+"=?")
		args = append(args, value)
	}
	if len(sets) == 0 {
		return s.GetResumeCustomization(ctx, id)
	}
	args = append(args, id)
	_, err := s.db.ExecContext(ctx, `UPDATE resume_customizations SET `+strings.Join(sets, ",")+` WHERE id=?`, args...)
	if err != nil {
		return nil, err
	}
	return s.GetResumeCustomization(ctx, id)
}
func (s *Store) GetResumeCustomization(ctx context.Context, id int) (Row, error) {
	return queryRow(ctx, s.db, `SELECT * FROM resume_customizations WHERE id=?`, id)
}
func (s *Store) FindLatestResumeCustomization(ctx context.Context, jobKey, descriptionHash string) (Row, error) {
	return queryRow(ctx, s.db, `SELECT * FROM resume_customizations WHERE job_key=? AND job_description_hash=? ORDER BY id DESC LIMIT 1`, jobKey, descriptionHash)
}

func (s *Store) FindingsSummary(ctx context.Context, runID int) (Row, error) {
	categories, err := queryRows(ctx, s.db, `SELECT category,COUNT(*) count FROM application_findings WHERE run_id=? GROUP BY category ORDER BY count DESC,category`, runID)
	if err != nil {
		return nil, err
	}
	rows, err := queryRows(ctx, s.db, `SELECT job_key,application_status,stage,category,summary,detail,page_url,created_at FROM application_findings WHERE run_id=? AND application_status IN ('blocked','incomplete','failed') ORDER BY created_at DESC,id DESC`, runID)
	if err != nil {
		return nil, err
	}
	latest := []Row{}
	seen := map[string]bool{}
	for _, r := range rows {
		key := asString(r["job_key"])
		if !seen[key] {
			seen[key] = true
			latest = append(latest, r)
		}
	}
	total := 0
	for _, r := range categories {
		total += asInt(r["count"])
	}
	return Row{"total_findings": total, "by_category": categories, "latest_for_unsuccessful_jobs": latest}, nil
}

func (s *Store) QueryRows(ctx context.Context, statement string, args ...any) ([]Row, error) {
	return queryRows(ctx, s.db, statement, args...)
}
func (s *Store) QueryRow(ctx context.Context, statement string, args ...any) (Row, error) {
	return queryRow(ctx, s.db, statement, args...)
}
func (s *Store) Exec(ctx context.Context, statement string, args ...any) (sql.Result, error) {
	return s.db.ExecContext(ctx, statement, args...)
}
func EncodeJSON(value any) string { data, _ := json.Marshal(value); return string(data) }
