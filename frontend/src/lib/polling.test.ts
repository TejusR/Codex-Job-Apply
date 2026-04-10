import { describe, expect, it } from "vitest";

import { getRunDetailRefetchInterval, getRunsRefetchInterval } from "./polling";

describe("polling helpers", () => {
  it("keeps the runs page polling while active runs exist", () => {
    expect(
      getRunsRefetchInterval({
        blocked_by_run_id: 1,
        can_start_run: false,
        items: [
          {
            allowed_actions: {
              finish: false,
              force_finish: false,
              requeue_runner_failures: true,
              resume: true
            },
            finished_at: null,
            findings_total: 0,
            has_outstanding_work: true,
            id: 1,
            jobs_applied: 0,
            jobs_failed: 0,
            jobs_filtered_in: 0,
            jobs_found: 0,
            jobs_skipped_duplicate: 0,
            jobs_skipped_old: 0,
            live_counts: {
              apply_workers_running: 0,
              applying_jobs: 0,
              discovery_workers_running: 0,
              queries_in_progress: 0,
              queries_pending: 1,
              ready_jobs: 0,
              search_results_pending: 0,
              search_results_processing: 0
            },
            search_summary: {
              completed_queries: 0,
              failed_queries: 0,
              in_progress_queries: 0,
              pending_queries: 1,
              pending_search_results: 0,
              processing_search_results: 0,
              requeued_jobs_count: 0,
              total_queries: 1,
              total_search_results: 0
            },
            started_at: "2026-04-10T00:00:00Z",
            ui_status: "needs_resume"
          }
        ]
      })
    ).toBe(5000);
  });

  it("stops run detail polling for finished runs", () => {
    expect(
      getRunDetailRefetchInterval({
        findings_summary: {
          by_category: [],
          latest_for_unsuccessful_jobs: [],
          total_findings: 0
        },
        jobs_preview: [],
        jobs_preview_total: 0,
        queries: [],
        recent_search_results: [],
        summary: {
          allowed_actions: {
            finish: false,
            force_finish: false,
            requeue_runner_failures: false,
            resume: false
          },
          finished_at: "2026-04-10T00:10:00Z",
          findings_total: 0,
          has_outstanding_work: false,
          id: 7,
          jobs_applied: 2,
          jobs_failed: 0,
          jobs_filtered_in: 0,
          jobs_found: 2,
          jobs_skipped_duplicate: 0,
          jobs_skipped_old: 0,
          live_counts: {
            apply_workers_running: 0,
            applying_jobs: 0,
            discovery_workers_running: 0,
            queries_in_progress: 0,
            queries_pending: 0,
            ready_jobs: 0,
            search_results_pending: 0,
            search_results_processing: 0
          },
          search_summary: {
            completed_queries: 2,
            failed_queries: 0,
            in_progress_queries: 0,
            pending_queries: 0,
            pending_search_results: 0,
            processing_search_results: 0,
            requeued_jobs_count: 0,
            total_queries: 2,
            total_search_results: 8
          },
          started_at: "2026-04-10T00:00:00Z",
          ui_status: "completed"
        },
        worker_sessions: []
      })
    ).toBe(false);
  });
});
