export type RunUiStatus =
  | "running"
  | "needs_resume"
  | "completed"
  | "completed_with_errors";

export type ResumeSource = "application_snapshot" | "default_profile";

export interface ResumeInfo {
  path: string | null;
  label: string | null;
  source: ResumeSource;
}

export interface SearchSummary {
  total_queries: number;
  completed_queries: number;
  failed_queries: number;
  pending_queries: number;
  in_progress_queries: number;
  pending_search_results: number;
  processing_search_results: number;
  total_search_results: number;
  requeued_jobs_count: number;
}

export interface LiveCounts {
  ready_jobs: number;
  applying_jobs: number;
  queries_pending: number;
  queries_in_progress: number;
  search_results_pending: number;
  search_results_processing: number;
  discovery_workers_running: number;
  apply_workers_running: number;
}

export interface RunAllowedActions {
  resume: boolean;
  requeue_runner_failures: boolean;
  finish: boolean;
  force_finish: boolean;
}

export interface RunSummary {
  id: number;
  started_at: string;
  finished_at: string | null;
  ui_status: RunUiStatus;
  jobs_found: number;
  jobs_filtered_in: number;
  jobs_skipped_old: number;
  jobs_skipped_duplicate: number;
  jobs_applied: number;
  jobs_failed: number;
  has_outstanding_work: boolean;
  findings_total: number;
  search_summary: SearchSummary;
  live_counts: LiveCounts;
  allowed_actions: RunAllowedActions;
}

export interface QueryRow {
  id: number;
  run_id: number;
  source_key: string;
  domain: string;
  query_text: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  results_seen: number;
  jobs_ingested: number;
  cursor_json: string | null;
  last_error: string | null;
}

export interface WorkerSessionRow {
  id: number;
  run_id: number;
  worker_type: string;
  slot_key: string;
  thread_id: string | null;
  status: string;
  started_at: string;
  last_used_at: string;
  last_error: string | null;
}

export interface SearchResultRow {
  id: number;
  run_id: number;
  source_key: string;
  parent_result_id: number | null;
  origin_kind: string;
  url: string;
  title: string | null;
  snippet: string | null;
  visible_date: string | null;
  page_number: number | null;
  rank: number | null;
  status: string;
  claimed_by: string | null;
  claimed_at: string | null;
  finished_at: string | null;
  reason: string | null;
  job_key: string | null;
}

export interface ApplicationRow {
  id: number;
  job_key: string;
  run_id: number | null;
  applied_at: string;
  status: string;
  confirmation_text: string | null;
  confirmation_url: string | null;
  resume_path_used: string | null;
  resume_label_used: string | null;
  error_message: string | null;
}

export interface FindingRow {
  id: number;
  job_key: string;
  run_id: number;
  application_status: string;
  stage: string;
  category: string;
  summary: string;
  detail: string | null;
  page_url: string | null;
  created_at: string;
}

export interface FindingCategoryCount {
  category: string;
  count: number;
}

export interface LatestFindingRow {
  job_key: string;
  application_status: string;
  stage: string;
  category: string;
  summary: string;
  detail: string | null;
  page_url: string | null;
  created_at: string;
}

export interface FindingsSummary {
  total_findings: number;
  by_category: FindingCategoryCount[];
  latest_for_unsuccessful_jobs: LatestFindingRow[];
}

export interface JobListItem {
  job_key: string;
  canonical_url: string;
  raw_url: string | null;
  source: string | null;
  title: string | null;
  company: string | null;
  location: string | null;
  posted_at: string | null;
  discovered_at: string;
  status: string;
  status_reason: string | null;
  last_updated_at: string;
  latest_application_status: string | null;
  latest_applied_at: string | null;
  latest_application_run_id: number | null;
  resume_info: ResumeInfo;
}

export interface JobDetail extends JobListItem {
  application_history: ApplicationRow[];
  findings: FindingRow[];
}

export interface RunDetail {
  summary: RunSummary;
  queries: QueryRow[];
  worker_sessions: WorkerSessionRow[];
  findings_summary: FindingsSummary;
  recent_search_results: SearchResultRow[];
  jobs_preview: JobListItem[];
  jobs_preview_total: number;
}

export interface RunsResponse {
  items: RunSummary[];
  can_start_run: boolean;
  blocked_by_run_id: number | null;
}

export interface JobListResponse {
  items: JobListItem[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface RunActionResponse {
  run: RunSummary;
  launched: boolean;
}

export interface RequeueRunnerFailuresResponse {
  run: RunSummary;
  count: number;
  job_keys: string[];
}

export interface JobsQuery {
  runId?: number;
  status?: string;
  source?: string;
  q?: string;
  page?: number;
  pageSize?: number;
}
