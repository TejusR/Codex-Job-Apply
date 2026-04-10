import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RunsPage } from "./RunsPage";
import { renderRoute } from "../test/testUtils";

describe("RunsPage", () => {
  it("renders run cards and disables starting a second active run", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          blocked_by_run_id: 12,
          can_start_run: false,
          items: [
            {
              allowed_actions: {
                finish: true,
                force_finish: true,
                requeue_runner_failures: true,
                resume: true
              },
              finished_at: null,
              findings_total: 1,
              has_outstanding_work: true,
              id: 12,
              jobs_applied: 2,
              jobs_failed: 1,
              jobs_filtered_in: 3,
              jobs_found: 4,
              jobs_skipped_duplicate: 1,
              jobs_skipped_old: 0,
              live_counts: {
                apply_workers_running: 0,
                applying_jobs: 1,
                discovery_workers_running: 0,
                queries_in_progress: 0,
                queries_pending: 1,
                ready_jobs: 1,
                search_results_pending: 2,
                search_results_processing: 0
              },
              search_summary: {
                completed_queries: 1,
                failed_queries: 0,
                in_progress_queries: 0,
                pending_queries: 1,
                pending_search_results: 2,
                processing_search_results: 0,
                requeued_jobs_count: 0,
                total_queries: 2,
                total_search_results: 4
              },
              started_at: "2026-04-10T00:00:00Z",
              ui_status: "needs_resume"
            }
          ]
        }),
        text: async () => ""
      })
    );

    renderRoute("/runs", <RunsPage />);

    expect(await screen.findByText("Run 12")).toBeInTheDocument();
    expect(screen.getByText(/Run 12 is still active/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start New Run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Resume" })).toBeEnabled();
  });
});
