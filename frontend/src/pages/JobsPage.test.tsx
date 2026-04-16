import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobsPage } from "./JobsPage";
import { renderRoute } from "../test/testUtils";

afterEach(() => {
  cleanup();
});

describe("JobsPage", () => {
  it("opens a detail drawer with the tailored resume preview panel", async () => {
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
            available_sources: ["greenhouse"],
            page: 1,
            page_size: 12,
            total: 1,
            total_pages: 1
          }),
          text: async (): Promise<string> => ""
        };
      }
      if (url.startsWith("/api/resume-customizations/")) {
        return {
          ok: true,
          json: async () => ({
            id: 22,
            job_key: "job-1",
            run_id: 7,
            status: "succeeded",
            created_at: "2026-04-10T00:03:00Z",
            source_template_path: "/resume/master_resume.tex",
            rendered_tex_path: "/data/resume_customizations/job-1/v1/resume.tex",
            rendered_pdf_path: "/data/resume_customizations/job-1/v1/resume.pdf",
            preview_content: "# Tailored Resume Preview\n\n## Summary\n- Backend-heavy fit",
            customization_payload_json: "{}",
            compiler: "pdflatex",
            error_message: null,
            preview_url: "/api/resume-customizations/22",
            download_url: "/api/resume-customizations/22/file"
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
            customization_id: 22,
            download_url: "/api/resume-customizations/22/file",
            generated_at: "2026-04-10T00:03:00Z",
            label: "tailored-acme.pdf",
            path: "resume/tailored-acme.pdf",
            preview_url: "/api/resume-customizations/22",
            source: "job_tailored"
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

    expect(await screen.findByText("Tailored Resume Snapshot")).toBeInTheDocument();
    expect(await screen.findByText(/Backend-heavy fit/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open PDF" })).toBeInTheDocument();
    expect(screen.getAllByText("tailored-acme.pdf")).toHaveLength(2);
    expect(screen.getAllByText("Job Tailored").length).toBeGreaterThan(0);
  });

  it("renders source as a dropdown backed by available source values", async () => {
    const jobsFetchUrls: string[] = [];
    const greenhouseJob = {
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
        customization_id: null,
        download_url: null,
        generated_at: null,
        label: "tailored-acme.pdf",
        path: "resume/tailored-acme.pdf",
        preview_url: null,
        source: "application_snapshot"
      },
      source: "greenhouse",
      status: "applied",
      status_reason: null,
      title: "Software Engineer"
    };
    const ashbyJob = {
      ...greenhouseJob,
      canonical_url: "https://jobs.ashbyhq.com/acme/54321",
      job_key: "job-2",
      source: "ashby",
      title: "Platform Engineer"
    };
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
        jobsFetchUrls.push(url);
        const params = new URL(url, "http://localhost").searchParams;
        const source = params.get("source");
        const items =
          source === "greenhouse"
            ? [greenhouseJob]
            : source === "ashby"
              ? [ashbyJob]
              : [greenhouseJob, ashbyJob];
        return {
          ok: true,
          json: async () => ({
            items,
            available_sources: ["ashby", "greenhouse"],
            page: 1,
            page_size: 12,
            total: items.length,
            total_pages: 1
          }),
          text: async (): Promise<string> => ""
        };
      }
      return {
        ok: true,
        json: async () => greenhouseJob,
        text: async (): Promise<string> => ""
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderRoute("/jobs", <JobsPage />);

    const user = userEvent.setup();
    const sourceSelect = await screen.findByLabelText("Source");
    const ashbyOption = await screen.findByRole("option", { name: "Ashby" });
    const greenhouseOption = await screen.findByRole("option", { name: "Greenhouse" });

    expect(sourceSelect.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: "All sources" })).toBeInTheDocument();
    expect(ashbyOption).toBeInTheDocument();
    expect(greenhouseOption).toBeInTheDocument();

    await user.selectOptions(sourceSelect, "greenhouse");

    await waitFor(() =>
      expect(
        jobsFetchUrls.some((url) => url.includes("source=greenhouse"))
      ).toBe(true)
    );
    expect(await screen.findByText("Software Engineer")).toBeInTheDocument();
  });
});
