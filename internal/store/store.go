package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ramesh/codex-job-apply/internal/jobs"
	"github.com/ramesh/codex-job-apply/internal/profile"
	_ "modernc.org/sqlite"
)

type Store struct {
	db   *sql.DB
	path string
}
type Row map[string]any

var schemaStatements = []string{
	`CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL UNIQUE, canonical_url TEXT NOT NULL,
 raw_url TEXT, source TEXT, title TEXT, company TEXT, location TEXT, posted_at TEXT, description_text TEXT,
 discovered_at TEXT NOT NULL DEFAULT (datetime('now')), status TEXT NOT NULL DEFAULT 'discovered',
 status_reason TEXT, last_updated_at TEXT NOT NULL DEFAULT (datetime('now')));`,
	`CREATE TABLE IF NOT EXISTS applications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT (datetime('now')),
 run_id INTEGER, status TEXT NOT NULL, confirmation_text TEXT, confirmation_url TEXT, resume_customization_id INTEGER,
 resume_path_used TEXT, resume_label_used TEXT, error_message TEXT,
 FOREIGN KEY (job_key) REFERENCES jobs(job_key), FOREIGN KEY (resume_customization_id) REFERENCES resume_customizations(id),
 FOREIGN KEY (run_id) REFERENCES runs(id));`,
	`CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL DEFAULT (datetime('now')), finished_at TEXT,
 jobs_found INTEGER NOT NULL DEFAULT 0, jobs_filtered_in INTEGER NOT NULL DEFAULT 0,
 jobs_skipped_old INTEGER NOT NULL DEFAULT 0, jobs_skipped_duplicate INTEGER NOT NULL DEFAULT 0,
 jobs_applied INTEGER NOT NULL DEFAULT 0, jobs_failed INTEGER NOT NULL DEFAULT 0, notes TEXT);`,
	`CREATE TABLE IF NOT EXISTS application_findings (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL, run_id INTEGER NOT NULL,
 application_status TEXT NOT NULL, stage TEXT NOT NULL, category TEXT NOT NULL, summary TEXT NOT NULL,
 detail TEXT, page_url TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
 FOREIGN KEY (job_key) REFERENCES jobs(job_key), FOREIGN KEY (run_id) REFERENCES runs(id));`,
	`CREATE TABLE IF NOT EXISTS run_search_queries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, source_key TEXT NOT NULL, domain TEXT NOT NULL,
 query_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', started_at TEXT, finished_at TEXT,
 results_seen INTEGER NOT NULL DEFAULT 0, jobs_ingested INTEGER NOT NULL DEFAULT 0, cursor_json TEXT, last_error TEXT,
 FOREIGN KEY (run_id) REFERENCES runs(id), UNIQUE(run_id, source_key));`,
	`CREATE TABLE IF NOT EXISTS run_search_results (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, source_key TEXT NOT NULL, parent_result_id INTEGER,
 origin_kind TEXT NOT NULL, url TEXT NOT NULL, title TEXT, snippet TEXT, visible_date TEXT, page_number INTEGER,
 rank INTEGER, status TEXT NOT NULL DEFAULT 'pending', claimed_by TEXT, claimed_at TEXT, finished_at TEXT,
 reason TEXT, job_key TEXT, FOREIGN KEY (run_id) REFERENCES runs(id),
 FOREIGN KEY (parent_result_id) REFERENCES run_search_results(id), UNIQUE(run_id, source_key, url));`,
	`CREATE TABLE IF NOT EXISTS codex_worker_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, worker_type TEXT NOT NULL, target_key TEXT NOT NULL,
 attempt_number INTEGER NOT NULL, status TEXT NOT NULL, exit_code INTEGER, error_message TEXT, started_at TEXT NOT NULL,
 finished_at TEXT, result_path TEXT, log_path TEXT, FOREIGN KEY (run_id) REFERENCES runs(id));`,
	`CREATE TABLE IF NOT EXISTS run_query_skipped_results (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, source_key TEXT NOT NULL, url TEXT NOT NULL,
 reason TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY (run_id) REFERENCES runs(id), UNIQUE(run_id, source_key, url));`,
	`CREATE TABLE IF NOT EXISTS codex_worker_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, worker_type TEXT NOT NULL, slot_key TEXT NOT NULL,
 thread_id TEXT, status TEXT NOT NULL DEFAULT 'idle', started_at TEXT NOT NULL, last_used_at TEXT NOT NULL,
 last_error TEXT, FOREIGN KEY (run_id) REFERENCES runs(id), UNIQUE(run_id, worker_type, slot_key));`,
	`CREATE TABLE IF NOT EXISTS resume_customizations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL, run_id INTEGER, status TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (datetime('now')), source_template_path TEXT, job_description_hash TEXT,
 rendered_tex_path TEXT, rendered_pdf_path TEXT, preview_content TEXT, customization_payload_json TEXT,
 compiler TEXT, error_message TEXT, FOREIGN KEY (job_key) REFERENCES jobs(job_key), FOREIGN KEY (run_id) REFERENCES runs(id));`,
}

var indexStatements = []string{
	`CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);`, `CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at);`,
	`CREATE INDEX IF NOT EXISTS idx_applications_job_key ON applications(job_key);`, `CREATE INDEX IF NOT EXISTS idx_applications_resume_customization_id ON applications(resume_customization_id);`,
	`CREATE INDEX IF NOT EXISTS idx_application_findings_run_id ON application_findings(run_id);`, `CREATE INDEX IF NOT EXISTS idx_application_findings_job_key ON application_findings(job_key);`,
	`CREATE INDEX IF NOT EXISTS idx_run_search_queries_run_status ON run_search_queries(run_id, status);`, `CREATE INDEX IF NOT EXISTS idx_run_search_results_run_status ON run_search_results(run_id, status);`,
	`CREATE INDEX IF NOT EXISTS idx_run_search_results_run_source ON run_search_results(run_id, source_key);`, `CREATE INDEX IF NOT EXISTS idx_codex_worker_attempts_run_target ON codex_worker_attempts(run_id, worker_type, target_key);`,
	`CREATE INDEX IF NOT EXISTS idx_run_query_skipped_results_run_source ON run_query_skipped_results(run_id, source_key);`, `CREATE INDEX IF NOT EXISTS idx_codex_worker_sessions_run_type_status ON codex_worker_sessions(run_id, worker_type, status);`,
	`CREATE INDEX IF NOT EXISTS idx_resume_customizations_job_created ON resume_customizations(job_key, created_at DESC);`,
}

func Open(path string) (*Store, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", "file:"+filepath.ToSlash(abs)+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	s := &Store{db: db, path: abs}
	if err := s.initialize(context.Background()); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}
func (s *Store) Close() error { return s.db.Close() }
func (s *Store) DB() *sql.DB  { return s.db }
func (s *Store) Path() string { return s.path }

func (s *Store) initialize(ctx context.Context) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, statement := range schemaStatements {
		if _, err := tx.ExecContext(ctx, statement); err != nil {
			return err
		}
	}
	for _, item := range []struct{ table, column, declaration string }{
		{"applications", "run_id", "INTEGER"}, {"applications", "resume_customization_id", "INTEGER"},
		{"applications", "resume_path_used", "TEXT"}, {"applications", "resume_label_used", "TEXT"},
		{"jobs", "description_text", "TEXT"}, {"run_search_queries", "cursor_json", "TEXT"},
	} {
		if err := ensureColumn(ctx, tx, item.table, item.column, item.declaration); err != nil {
			return err
		}
	}
	for _, statement := range indexStatements {
		if _, err := tx.ExecContext(ctx, statement); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func ensureColumn(ctx context.Context, tx *sql.Tx, table, column, declaration string) error {
	rows, err := tx.QueryContext(ctx, "PRAGMA table_info("+table+")")
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var cid int
		var name, typ string
		var notnull, pk int
		var defaultValue any
		if err := rows.Scan(&cid, &name, &typ, &notnull, &defaultValue, &pk); err != nil {
			return err
		}
		if name == column {
			return nil
		}
	}
	_, err = tx.ExecContext(ctx, fmt.Sprintf("ALTER TABLE %s ADD COLUMN %s %s", table, column, declaration))
	return err
}

func queryRow(ctx context.Context, q interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}, statement string, args ...any) (Row, error) {
	rows, err := q.QueryContext(ctx, statement, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	if !rows.Next() {
		if err := rows.Err(); err != nil {
			return nil, err
		}
		return nil, nil
	}
	return scanRow(rows)
}
func queryRows(ctx context.Context, q interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}, statement string, args ...any) ([]Row, error) {
	rows, err := q.QueryContext(ctx, statement, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Row{}
	for rows.Next() {
		row, err := scanRow(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, row)
	}
	return out, rows.Err()
}
func scanRow(rows *sql.Rows) (Row, error) {
	columns, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	values := make([]any, len(columns))
	pointers := make([]any, len(columns))
	for i := range values {
		pointers[i] = &values[i]
	}
	if err := rows.Scan(pointers...); err != nil {
		return nil, err
	}
	out := Row{}
	for i, column := range columns {
		if b, ok := values[i].([]byte); ok {
			out[column] = string(b)
		} else {
			out[column] = values[i]
		}
	}
	return out, nil
}
func asInt(v any) int {
	switch n := v.(type) {
	case int64:
		return int(n)
	case int:
		return n
	case float64:
		return int(n)
	case json.Number:
		i, _ := n.Int64()
		return int(i)
	default:
		return 0
	}
}
func asString(v any) string {
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
func nullableString(v string) any {
	if strings.TrimSpace(v) == "" {
		return nil
	}
	return strings.TrimSpace(v)
}

func (s *Store) StartRun(ctx context.Context) (Row, error) {
	now := jobs.FormatTimestamp(jobs.UTCNow())
	notes := `{"seen_job_keys":[],"requeued_jobs_count":0}`
	result, err := s.db.ExecContext(ctx, `INSERT INTO runs(started_at,notes) VALUES(?,?)`, now, notes)
	if err != nil {
		return nil, err
	}
	id, _ := result.LastInsertId()
	return s.GetRun(ctx, int(id))
}

func (s *Store) PrepareRun(ctx context.Context, root string) (Row, error) {
	validation := profile.Validate(root)
	if !validation.OK() {
		missing := append(append([]string{}, validation.MissingRequiredFields...), validation.MissingRequiredFiles...)
		return nil, fmt.Errorf("profile validation failed. Missing required items: %s", strings.Join(missing, ", "))
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	now := jobs.FormatTimestamp(jobs.UTCNow())
	notes := map[string]any{"seen_job_keys": []string{}, "requeued_jobs_count": 0}
	encoded, _ := json.Marshal(notes)
	result, err := tx.ExecContext(ctx, `INSERT INTO runs(started_at,notes) VALUES(?,?)`, now, string(encoded))
	if err != nil {
		return nil, err
	}
	runID64, _ := result.LastInsertId()
	runID := int(runID64)
	requeue, err := tx.ExecContext(ctx, `UPDATE jobs SET status='ready_to_apply',status_reason='requeued_from_interrupted_run',last_updated_at=? WHERE status='applying'`, now)
	if err != nil {
		return nil, err
	}
	count, _ := requeue.RowsAffected()
	notes["requeued_jobs_count"] = int(count)
	encoded, _ = json.Marshal(notes)
	if _, err = tx.ExecContext(ctx, `UPDATE runs SET notes=? WHERE id=?`, string(encoded), runID); err != nil {
		return nil, err
	}
	for _, query := range validation.Payload().GoogleSearchQueries {
		if _, err = tx.ExecContext(ctx, `INSERT INTO run_search_queries(run_id,source_key,domain,query_text,status,cursor_json) VALUES(?,?,?,?, 'pending',NULL)`, runID, query.SourceKey, query.Domain, query.Query); err != nil {
			return nil, err
		}
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	queries, err := s.ListRunQueries(ctx, runID)
	if err != nil {
		return nil, err
	}
	return Row{"run_id": runID, "queries": queries, "requeued_jobs_count": int(count), "warnings": validation.Warnings}, nil
}

func (s *Store) GetRun(ctx context.Context, id int) (Row, error) {
	row, err := queryRow(ctx, s.db, `SELECT * FROM runs WHERE id=?`, id)
	if err == nil && row == nil {
		return nil, fmt.Errorf("run %d does not exist", id)
	}
	return row, err
}
func (s *Store) GetJob(ctx context.Context, key string) (Row, error) {
	return queryRow(ctx, s.db, `SELECT * FROM jobs WHERE job_key=?`, key)
}
func (s *Store) GetQuery(ctx context.Context, runID int, source string) (Row, error) {
	return queryRow(ctx, s.db, `SELECT * FROM run_search_queries WHERE run_id=? AND source_key=?`, runID, source)
}

type IngestInput struct {
	RunID                                                                                           int
	RawURL, CanonicalURL, Source, Title, Company, Location, PostedAt, DiscoveredAt, DescriptionText string
	RoleKeywords, AllowedLocations                                                                  []string
	AllowUnverifiableFreshness                                                                      bool
}
type IngestResult struct {
	Action       string              `json:"action"`
	JobKey       string              `json:"job_key"`
	CanonicalURL string              `json:"canonical_url"`
	Status       string              `json:"status"`
	StatusReason *string             `json:"status_reason"`
	Source       string              `json:"source"`
	Freshness    jobs.FreshnessCheck `json:"freshness"`
}

func (s *Store) IngestJob(ctx context.Context, input IngestInput) (IngestResult, error) {
	canonical, err := jobs.CanonicalizeURL(input.RawURL, input.CanonicalURL)
	if err != nil {
		return IngestResult{}, err
	}
	key := jobs.BuildJobKey(canonical)
	source := input.Source
	if source == "" {
		source = jobs.InferSource(canonical)
	}
	var posted *string
	if strings.TrimSpace(input.PostedAt) != "" {
		posted = &input.PostedAt
	}
	freshness := jobs.EvaluatePostedAt(posted, time.Time{})
	discovered := input.DiscoveredAt
	if discovered == "" {
		discovered = jobs.FormatTimestamp(jobs.UTCNow())
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return IngestResult{}, err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `UPDATE runs SET jobs_found=jobs_found+1 WHERE id=?`, input.RunID); err != nil {
		return IngestResult{}, err
	}
	notes, err := getNotes(ctx, tx, input.RunID)
	if err != nil {
		return IngestResult{}, err
	}
	seen := stringSlice(notes["seen_job_keys"])
	for _, item := range seen {
		if item == key {
			tx.ExecContext(ctx, `UPDATE runs SET jobs_skipped_duplicate=jobs_skipped_duplicate+1 WHERE id=?`, input.RunID)
			tx.Commit()
			reason := "job_already_seen_in_current_run"
			return IngestResult{"duplicate_same_run", key, canonical, "duplicate_skipped", &reason, source, freshness}, nil
		}
	}
	seen = append(seen, key)
	notes["seen_job_keys"] = seen
	if err = setNotes(ctx, tx, input.RunID, notes); err != nil {
		return IngestResult{}, err
	}
	if reason, err := findAttemptedDuplicate(ctx, tx, key, canonical); err != nil {
		return IngestResult{}, err
	} else if reason != "" {
		tx.ExecContext(ctx, `UPDATE runs SET jobs_skipped_duplicate=jobs_skipped_duplicate+1 WHERE id=?`, input.RunID)
		tx.Commit()
		return IngestResult{"duplicate_existing_attempt", key, canonical, "duplicate_skipped", &reason, source, freshness}, nil
	}
	status, action, reason := "ready_to_apply", "ready_to_apply", (*string)(nil)
	if !freshness.IsVerifiable && !input.AllowUnverifiableFreshness {
		status = "skipped_unverifiable_date"
		action = status
		reason = freshness.Reason
	} else if freshness.IsVerifiable && !freshness.IsRecent {
		status = "filtered_out_old"
		action = status
		reason = freshness.Reason
		tx.ExecContext(ctx, `UPDATE runs SET jobs_skipped_old=jobs_skipped_old+1 WHERE id=?`, input.RunID)
	} else if !jobs.TitleMatchesRole(input.Title, input.RoleKeywords) {
		status = "discovered"
		action = "filtered_out_role"
		value := "filtered_out_role"
		reason = &value
	} else if !jobs.LocationMatchesUS(input.Location, input.AllowedLocations) {
		status = "discovered"
		action = "filtered_out_location"
		value := "filtered_out_location"
		reason = &value
	} else {
		if !freshness.IsVerifiable {
			value := "unverified_freshness_allowed"
			reason = &value
			action = "ready_to_apply_unverifiable_date"
		}
		tx.ExecContext(ctx, `UPDATE runs SET jobs_filtered_in=jobs_filtered_in+1 WHERE id=?`, input.RunID)
	}
	if err = upsertJob(ctx, tx, key, canonical, input, source, freshness.NormalizedPostedAt, discovered, status, reason); err != nil {
		return IngestResult{}, err
	}
	if err = tx.Commit(); err != nil {
		return IngestResult{}, err
	}
	return IngestResult{action, key, canonical, status, reason, source, freshness}, nil
}

func upsertJob(ctx context.Context, tx *sql.Tx, key, canonical string, input IngestInput, source string, posted *string, discovered, status string, reason *string) error {
	_, err := tx.ExecContext(ctx, `INSERT INTO jobs(job_key,canonical_url,raw_url,source,title,company,location,posted_at,description_text,discovered_at,status,status_reason,last_updated_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_key) DO UPDATE SET canonical_url=excluded.canonical_url,raw_url=excluded.raw_url,source=excluded.source,title=excluded.title,company=excluded.company,location=excluded.location,posted_at=excluded.posted_at,description_text=COALESCE(excluded.description_text,jobs.description_text),discovered_at=excluded.discovered_at,status=excluded.status,status_reason=excluded.status_reason,last_updated_at=excluded.last_updated_at`, key, canonical, input.RawURL, source, nullableString(input.Title), nullableString(input.Company), nullableString(input.Location), posted, nullableString(input.DescriptionText), discovered, status, reason, jobs.FormatTimestamp(jobs.UTCNow()))
	return err
}
func findAttemptedDuplicate(ctx context.Context, tx *sql.Tx, key, canonical string) (string, error) {
	row, err := queryRow(ctx, tx, `SELECT applications.status FROM applications JOIN jobs ON jobs.job_key=applications.job_key WHERE jobs.job_key=? OR jobs.canonical_url=? ORDER BY applications.id DESC LIMIT 1`, key, canonical)
	if err != nil {
		return "", err
	}
	if row != nil {
		status := asString(row["status"])
		if status == "submitted" || status == "duplicate_skipped" || status == "incomplete" || status == "blocked" {
			return "existing_application_" + status, nil
		}
	}
	row, err = queryRow(ctx, tx, `SELECT status FROM jobs WHERE job_key=? OR canonical_url=? LIMIT 1`, key, canonical)
	if err != nil {
		return "", err
	}
	if row != nil {
		status := asString(row["status"])
		if status == "applied" || status == "duplicate_skipped" || status == "incomplete" || status == "blocked" || status == "applying" {
			return "existing_job_" + status, nil
		}
	}
	return "", nil
}

func (s *Store) NextJob(ctx context.Context, mark bool) (Row, error) {
	for {
		tx, err := s.db.BeginTx(ctx, nil)
		if err != nil {
			return nil, err
		}
		row, err := queryRow(ctx, tx, `SELECT * FROM jobs WHERE status='ready_to_apply' ORDER BY posted_at DESC,discovered_at DESC LIMIT 1`)
		if err != nil {
			tx.Rollback()
			return nil, err
		}
		if row == nil {
			tx.Rollback()
			return nil, nil
		}
		if !mark {
			tx.Rollback()
			return row, nil
		}
		result, err := tx.ExecContext(ctx, `UPDATE jobs SET status='applying',status_reason=NULL,last_updated_at=? WHERE job_key=? AND status='ready_to_apply'`, jobs.FormatTimestamp(jobs.UTCNow()), row["job_key"])
		if err != nil {
			tx.Rollback()
			return nil, err
		}
		affected, _ := result.RowsAffected()
		if affected != 1 {
			tx.Rollback()
			continue
		}
		updated, err := queryRow(ctx, tx, `SELECT * FROM jobs WHERE job_key=?`, row["job_key"])
		if err == nil {
			err = tx.Commit()
		} else {
			tx.Rollback()
		}
		return updated, err
	}
}
func (s *Store) MarkJobApplying(ctx context.Context, key string) (Row, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	row, err := queryRow(ctx, tx, `SELECT * FROM jobs WHERE job_key=?`, key)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, fmt.Errorf("job %s does not exist", key)
	}
	if asString(row["status"]) != "ready_to_apply" {
		return row, nil
	}
	if _, err = tx.ExecContext(ctx, `UPDATE jobs SET status='applying',status_reason=NULL,last_updated_at=? WHERE job_key=? AND status='ready_to_apply'`, jobs.FormatTimestamp(jobs.UTCNow()), key); err != nil {
		return nil, err
	}
	row, err = queryRow(ctx, tx, `SELECT * FROM jobs WHERE job_key=?`, key)
	if err == nil {
		err = tx.Commit()
	}
	return row, err
}

func (s *Store) RequeueStaleApplyingJobs(ctx context.Context, runID *int) (int, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, `UPDATE jobs SET status='ready_to_apply',status_reason='requeued_from_interrupted_run',last_updated_at=? WHERE status='applying'`, jobs.FormatTimestamp(jobs.UTCNow()))
	if err != nil {
		return 0, err
	}
	count64, _ := result.RowsAffected()
	count := int(count64)
	if runID != nil {
		notes, err := getNotes(ctx, tx, *runID)
		if err == nil {
			notes["requeued_jobs_count"] = asInt(notes["requeued_jobs_count"]) + count
			err = setNotes(ctx, tx, *runID, notes)
		}
		if err != nil {
			return 0, err
		}
	}
	if err := tx.Commit(); err != nil {
		return 0, err
	}
	return count, nil
}
func (s *Store) RequeueProcessingSearchResults(ctx context.Context, runID int) (int, error) {
	result, err := s.db.ExecContext(ctx, `UPDATE run_search_results SET status='pending',claimed_by=NULL,claimed_at=NULL,finished_at=NULL,reason=COALESCE(reason,'requeued_from_interrupted_run') WHERE run_id=? AND status='processing'`, runID)
	if err != nil {
		return 0, err
	}
	n, _ := result.RowsAffected()
	return int(n), nil
}

func (s *Store) NextQuery(ctx context.Context, runID int) (Row, error) {
	return s.claimQuery(ctx, runID, false)
}
func (s *Store) ClaimQuery(ctx context.Context, runID int) (Row, error) {
	return s.claimQuery(ctx, runID, true)
}
func (s *Store) claimQuery(ctx context.Context, runID int, resume bool) (Row, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	exists, _ := queryRow(ctx, tx, `SELECT id FROM runs WHERE id=?`, runID)
	if exists == nil {
		return nil, fmt.Errorf("run %d does not exist", runID)
	}
	var row Row
	if resume {
		row, err = queryRow(ctx, tx, `SELECT * FROM run_search_queries WHERE run_id=? AND status='in_progress' ORDER BY started_at,id LIMIT 1`, runID)
	}
	if err != nil {
		return nil, err
	}
	if row == nil {
		row, err = queryRow(ctx, tx, `SELECT * FROM run_search_queries WHERE run_id=? AND status='pending' ORDER BY id LIMIT 1`, runID)
	}
	if err != nil || row == nil {
		return row, err
	}
	if asString(row["status"]) == "pending" {
		now := jobs.FormatTimestamp(jobs.UTCNow())
		if _, err = tx.ExecContext(ctx, `UPDATE run_search_queries SET status='in_progress',started_at=COALESCE(started_at,?),finished_at=NULL,last_error=NULL WHERE id=?`, now, row["id"]); err != nil {
			return nil, err
		}
		row, err = queryRow(ctx, tx, `SELECT * FROM run_search_queries WHERE id=?`, row["id"])
	}
	if err == nil {
		err = tx.Commit()
	}
	return row, err
}
func (s *Store) ListRunQueries(ctx context.Context, runID int) ([]Row, error) {
	exists, err := queryRow(ctx, s.db, `SELECT id FROM runs WHERE id=?`, runID)
	if err != nil {
		return nil, err
	}
	if exists == nil {
		return nil, fmt.Errorf("run %d does not exist", runID)
	}
	return queryRows(ctx, s.db, `SELECT * FROM run_search_queries WHERE run_id=? ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'pending' THEN 1 WHEN 'failed' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END,id`, runID)
}
func (s *Store) UpdateQuery(ctx context.Context, runID int, source, status string, resultsSeen, jobsIngested *int, cursor, lastError *string) (Row, error) {
	now := jobs.FormatTimestamp(jobs.UTCNow())
	row, err := s.GetQuery(ctx, runID, source)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, fmt.Errorf("run %d does not have a search query for source '%s'", runID, source)
	}
	_, err = s.db.ExecContext(ctx, `UPDATE run_search_queries SET status=?,started_at=COALESCE(started_at,?),finished_at=?,results_seen=COALESCE(?,results_seen),jobs_ingested=COALESCE(?,jobs_ingested),cursor_json=COALESCE(?,cursor_json),last_error=? WHERE id=?`, status, now, now, resultsSeen, jobsIngested, cursor, lastError, row["id"])
	if err != nil {
		return nil, err
	}
	return s.GetQuery(ctx, runID, source)
}
func (s *Store) CheckpointQuery(ctx context.Context, runID int, source string, resultsSeen, jobsIngested *int, cursor *string) (Row, error) {
	now := jobs.FormatTimestamp(jobs.UTCNow())
	row, err := s.GetQuery(ctx, runID, source)
	if err != nil || row == nil {
		if err == nil {
			err = fmt.Errorf("run %d does not have a search query for source '%s'", runID, source)
		}
		return nil, err
	}
	_, err = s.db.ExecContext(ctx, `UPDATE run_search_queries SET status='in_progress',started_at=COALESCE(started_at,?),finished_at=NULL,results_seen=COALESCE(?,results_seen),jobs_ingested=COALESCE(?,jobs_ingested),cursor_json=COALESCE(?,cursor_json),last_error=NULL WHERE id=?`, now, resultsSeen, jobsIngested, cursor, row["id"])
	if err != nil {
		return nil, err
	}
	return s.GetQuery(ctx, runID, source)
}
func (s *Store) IncrementQueryJobsIngested(ctx context.Context, runID int, source string, amount int) (Row, error) {
	_, err := s.db.ExecContext(ctx, `UPDATE run_search_queries SET jobs_ingested=jobs_ingested+? WHERE run_id=? AND source_key=?`, amount, runID, source)
	if err != nil {
		return nil, err
	}
	return s.GetQuery(ctx, runID, source)
}

func (s *Store) WorkflowStatus(ctx context.Context, runID int) (Row, error) {
	run, err := s.GetRun(ctx, runID)
	if err != nil {
		return nil, err
	}
	open, err := s.countOpenJobs(ctx)
	if err != nil {
		return nil, err
	}
	queries, err := s.countStatuses(ctx, "run_search_queries", runID)
	if err != nil {
		return nil, err
	}
	results, err := s.countStatuses(ctx, "run_search_results", runID)
	if err != nil {
		return nil, err
	}
	workers, err := s.workerCounts(ctx, runID)
	if err != nil {
		return nil, err
	}
	drained := open["ready_to_apply"] == 0 && open["applying"] == 0 && queries["pending"] == 0 && queries["in_progress"] == 0 && results["pending"] == 0 && results["processing"] == 0
	return Row{"run": run, "ready_jobs": open["ready_to_apply"], "applying_jobs": open["applying"], "queries_pending": queries["pending"], "queries_in_progress": queries["in_progress"], "queries_completed": queries["completed"], "queries_failed": queries["failed"], "search_results_pending": results["pending"], "search_results_processing": results["processing"], "search_results_total": results["total"], "discovery_workers_running": workers["discovery"], "apply_workers_running": workers["apply"], "drained": drained}, nil
}
func (s *Store) countOpenJobs(ctx context.Context) (map[string]int, error) {
	rows, err := queryRows(ctx, s.db, `SELECT status,COUNT(*) count FROM jobs WHERE status IN ('ready_to_apply','applying') GROUP BY status`)
	out := map[string]int{"ready_to_apply": 0, "applying": 0}
	for _, r := range rows {
		out[asString(r["status"])] = asInt(r["count"])
	}
	return out, err
}
func (s *Store) countStatuses(ctx context.Context, table string, runID int) (map[string]int, error) {
	if table != "run_search_queries" && table != "run_search_results" {
		return nil, errors.New("unsupported status table")
	}
	rows, err := queryRows(ctx, s.db, `SELECT status,COUNT(*) count FROM `+table+` WHERE run_id=? GROUP BY status`, runID)
	out := map[string]int{"pending": 0, "in_progress": 0, "completed": 0, "failed": 0, "processing": 0, "total": 0}
	for _, r := range rows {
		n := asInt(r["count"])
		out[asString(r["status"])] = n
		out["total"] += n
	}
	return out, err
}
func (s *Store) workerCounts(ctx context.Context, runID int) (map[string]int, error) {
	rows, err := queryRows(ctx, s.db, `SELECT worker_type,COUNT(*) count FROM codex_worker_sessions WHERE run_id=? AND status='running' GROUP BY worker_type`, runID)
	out := map[string]int{"discovery": 0, "apply": 0}
	for _, r := range rows {
		out[asString(r["worker_type"])] = asInt(r["count"])
	}
	return out, err
}

func (s *Store) FinishRun(ctx context.Context, runID int, force bool) (Row, error) {
	if !force {
		status, err := s.WorkflowStatus(ctx, runID)
		if err != nil {
			return nil, err
		}
		if drained, ok := status["drained"].(bool); !ok || !drained {
			return nil, fmt.Errorf("cannot finish run while work remains: ready_jobs=%d, applying_jobs=%d, queries_pending=%d, queries_in_progress=%d, search_results_pending=%d, search_results_processing=%d", asInt(status["ready_jobs"]), asInt(status["applying_jobs"]), asInt(status["queries_pending"]), asInt(status["queries_in_progress"]), asInt(status["search_results_pending"]), asInt(status["search_results_processing"]))
		}
	}
	_, err := s.db.ExecContext(ctx, `UPDATE runs SET finished_at=COALESCE(finished_at,?) WHERE id=?`, jobs.FormatTimestamp(jobs.UTCNow()), runID)
	if err != nil {
		return nil, err
	}
	run, err := s.GetRun(ctx, runID)
	if err != nil {
		return nil, err
	}
	notes := loadNotes(run["notes"])
	run["notes"] = notes
	summary, _ := s.SearchSummary(ctx, runID)
	run["search_summary"] = summary
	findings, _ := s.FindingsSummary(ctx, runID)
	run["findings_summary"] = findings
	return run, nil
}
func (s *Store) SearchSummary(ctx context.Context, runID int) (Row, error) {
	q, err := s.countStatuses(ctx, "run_search_queries", runID)
	if err != nil {
		return nil, err
	}
	r, err := s.countStatuses(ctx, "run_search_results", runID)
	if err != nil {
		return nil, err
	}
	run, err := s.GetRun(ctx, runID)
	if err != nil {
		return nil, err
	}
	notes := loadNotes(run["notes"])
	return Row{"total_queries": q["total"], "completed_queries": q["completed"], "failed_queries": q["failed"], "pending_queries": q["pending"], "in_progress_queries": q["in_progress"], "pending_search_results": r["pending"], "processing_search_results": r["processing"], "total_search_results": r["total"], "requeued_jobs_count": asInt(notes["requeued_jobs_count"])}, nil
}

func getNotes(ctx context.Context, tx *sql.Tx, runID int) (map[string]any, error) {
	row, err := queryRow(ctx, tx, `SELECT notes FROM runs WHERE id=?`, runID)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, fmt.Errorf("run %d does not exist", runID)
	}
	return loadNotes(row["notes"]), nil
}
func setNotes(ctx context.Context, tx *sql.Tx, runID int, notes map[string]any) error {
	data, _ := json.Marshal(notes)
	_, err := tx.ExecContext(ctx, `UPDATE runs SET notes=? WHERE id=?`, string(data), runID)
	return err
}
func loadNotes(raw any) map[string]any {
	defaults := map[string]any{"seen_job_keys": []any{}, "requeued_jobs_count": 0}
	text := asString(raw)
	if text == "" {
		return defaults
	}
	var out map[string]any
	if json.Unmarshal([]byte(text), &out) != nil || out == nil {
		return defaults
	}
	if _, ok := out["seen_job_keys"].([]any); !ok {
		if _, ok := out["seen_job_keys"].([]string); !ok {
			out["seen_job_keys"] = []any{}
		}
	}
	if _, ok := out["requeued_jobs_count"]; !ok {
		out["requeued_jobs_count"] = 0
	}
	return out
}
func stringSlice(v any) []string {
	out := []string{}
	switch values := v.(type) {
	case []any:
		for _, item := range values {
			out = append(out, asString(item))
		}
	case []string:
		return append(out, values...)
	}
	return out
}
