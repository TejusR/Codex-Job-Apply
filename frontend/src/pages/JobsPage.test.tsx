import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { JobsPage } from "./JobsPage";
import { renderRoute } from "../test/testUtils";

describe("JobsPage", () => {
  it("opens a detail drawer with the reserved resume panel", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/runs")) {
        return {
          ok: true,
          json: async () => ({
            blocked_by_run_id: null,
            can_start_run: true,
            items: []
          }),
          text: async (): Promise<string> => ""
        };
      }
      if (url.startsWith("/api/jobs?")) {
        return {
          ok: true,
          json: async () => ({
            items: [
              {
                canonical_url: "https://boards.greenhouse.io/acme/jobs/12345",
                company: "Acme",
                discovered_at: "2026-04-10T00:00:00Z",
                job_key: "job-1",
                last_updated_at: "2026-04-10T00:05:00Z",
                latest_application_run_id: 7,
                latest_application_status: "submitted",
                latest_applied_at: "2026-04-10T00:05:00Z",
                location: "Remote, United States",
                posted_at: "2 hours ago",
                raw_url: null,
                resume_info: {
                  label: "tailored-acme.pdf",
                  path: "resume/tailored-acme.pdf",
                  source: "application_snapshot"
                },
                source: "greenhouse",
                status: "applied",
                status_reason: null,
                title: "Software Engineer"
              }
            ],
            page: 1,
            page_size: 12,
            total: 1,
            total_pages: 1
          }),
          text: async (): Promise<string> => ""
        };
      }
      return {
        ok: true,
        json: async () => ({
          application_history: [],
          canonical_url: "https://boards.greenhouse.io/acme/jobs/12345",
          company: "Acme",
          discovered_at: "2026-04-10T00:00:00Z",
          findings: [],
          job_key: "job-1",
          last_updated_at: "2026-04-10T00:05:00Z",
          latest_application_run_id: 7,
          latest_application_status: "submitted",
          latest_applied_at: "2026-04-10T00:05:00Z",
          location: "Remote, United States",
          posted_at: "2 hours ago",
          raw_url: null,
          resume_info: {
            label: "tailored-acme.pdf",
            path: "resume/tailored-acme.pdf",
            source: "application_snapshot"
          },
          source: "greenhouse",
          status: "applied",
          status_reason: null,
          title: "Software Engineer"
        }),
        text: async (): Promise<string> => ""
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRoute("/jobs", <JobsPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByText("Software Engineer"));

    expect(await screen.findByText("Resume Customization Panel")).toBeInTheDocument();
    expect(screen.getAllByText("tailored-acme.pdf")).toHaveLength(2);
    expect(screen.getAllByText("Application Snapshot").length).toBeGreaterThan(0);
  });
});
