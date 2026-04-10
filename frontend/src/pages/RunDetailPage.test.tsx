import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RunDetailPage } from "./RunDetailPage";
import { renderRoute } from "../test/testUtils";

describe("RunDetailPage", () => {
  it("renders query, worker, and result sections", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          findings_summary: {
            by_category: [{ category: "confirmation_missing", count: 1 }],
            latest_for_unsuccessful_jobs: [],
            total_findings: 1
          },
          jobs_preview: [],
          jobs_preview_total: 0,
          queries: [
            {
              cursor_json: null,
              domain: "boards.greenhouse.io",
              finished_at: null,
              id: 1,
              jobs_ingested: 2,
              last_error: null,
              query_text: "site:boards.greenhouse.io backend engineer",
              results_seen: 8,
              run_id: 7,
              source_key: "greenhouse",
              started_at: "2026-04-10T00:00:00Z",
              status: "in_progress"
            }
          ],
          recent_search_results: [
            {
              claimed_at: null,
              claimed_by: null,
              finished_at: null,
              id: 1,
              job_key: null,
              origin_kind: "google_result",
              page_number: 1,
              parent_result_id: null,
              rank: 1,
              reason: null,
              run_id: 7,
              snippet: "Acme backend role",
              source_key: "greenhouse",
              status: "pending",
              title: "Software Engineer",
              url: "https://boards.greenhouse.io/acme/jobs/12345",
              visible_date: "2 hours ago"
            }
          ],
          summary: {
            allowed_actions: {
              finish: false,
              force_finish: false,
              requeue_runner_failures: false,
              resume: false
            },
            finished_at: null,
            findings_total: 1,
            has_outstanding_work: true,
            id: 7,
            jobs_applied: 1,
            jobs_failed: 1,
            jobs_filtered_in: 0,
            jobs_found: 2,
            jobs_skipped_duplicate: 0,
            jobs_skipped_old: 0,
            live_counts: {
              apply_workers_running: 1,
              applying_jobs: 1,
              discovery_workers_running: 1,
              queries_in_progress: 1,
              queries_pending: 0,
              ready_jobs: 0,
              search_results_pending: 1,
              search_results_processing: 0
            },
            search_summary: {
              completed_queries: 0,
              failed_queries: 0,
              in_progress_queries: 1,
              pending_queries: 0,
              pending_search_results: 1,
              processing_search_results: 0,
              requeued_jobs_count: 0,
              total_queries: 1,
              total_search_results: 1
            },
            started_at: "2026-04-10T00:00:00Z",
            ui_status: "running"
          },
          worker_sessions: [
            {
              id: 1,
              last_error: null,
              last_used_at: "2026-04-10T00:05:00Z",
              run_id: 7,
              slot_key: "greenhouse",
              started_at: "2026-04-10T00:00:00Z",
              status: "running",
              thread_id: "thread-1",
              worker_type: "discovery"
            }
          ]
        }),
        text: async () => ""
      })
    );

    renderRoute("/runs/7", <RunDetailPage />, "/runs/:runId");

    expect(await screen.findByText("Run 7")).toBeInTheDocument();
    expect(await screen.findByText("Search Queries")).toBeInTheDocument();
    expect(await screen.findByText("Worker Sessions")).toBeInTheDocument();
    expect(await screen.findByText("Recent Result Queue")).toBeInTheDocument();
    expect(await screen.findByText("Software Engineer")).toBeInTheDocument();
  });
});
