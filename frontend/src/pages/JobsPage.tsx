import { useQuery } from "@tanstack/react-query";
import {
  startTransition,
  useDeferredValue,
  useEffect,
  useMemo,
  useState
} from "react";
import { useLocation } from "react-router-dom";

import { fetchJobDetail, fetchJobs, fetchRuns } from "../api";
import { Drawer } from "../components/Drawer";
import { EmptyState } from "../components/EmptyState";
import { StatusBadge } from "../components/StatusBadge";
import { formatDateTime, titleCase } from "../lib/format";

export function JobsPage() {
  const location = useLocation();
  const initialRunId = useMemo(() => {
    const value = new URLSearchParams(location.search).get("runId");
    return value ? Number(value) : undefined;
  }, [location.search]);
  const [runId, setRunId] = useState<number | undefined>(initialRunId);
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selectedJobKey, setSelectedJobKey] = useState<string | null>(null);
  const deferredSearch = useDeferredValue(search);

  useEffect(() => {
    setPage(1);
  }, [runId, status, source, deferredSearch]);

  const runsQuery = useQuery({
    queryKey: ["runs", "jobs-filter"],
    queryFn: fetchRuns,
    refetchOnWindowFocus: false
  });

  const jobsQuery = useQuery({
    queryKey: ["jobs", { runId, status, source, deferredSearch, page }],
    queryFn: () =>
      fetchJobs({
        runId,
        status: status || undefined,
        source: source || undefined,
        q: deferredSearch || undefined,
        page,
        pageSize: 12
      }),
    refetchOnWindowFocus: false
  });

  const detailQuery = useQuery({
    queryKey: ["job", selectedJobKey],
    queryFn: () => fetchJobDetail(selectedJobKey!),
    enabled: selectedJobKey !== null,
    refetchOnWindowFocus: false
  });

  const errorMessage = [jobsQuery.error, detailQuery.error, runsQuery.error]
    .find(Boolean)
    ?.toString()
    .replace(/^Error:\s*/, "");

  return (
    <section className="page-section">
      <div className="section-header fade-up">
        <div>
          <p className="eyebrow">Application Ledger</p>
          <h2>Jobs</h2>
          <p className="section-copy">
            Filter jobs by run, source, and status, then open a dedicated resume panel
            for the exact file used on a given application.
          </p>
        </div>
        <button
          className="ghost-button"
          onClick={() => {
            void jobsQuery.refetch();
            void detailQuery.refetch();
          }}
          type="button"
        >
          Refresh
        </button>
      </div>

      {errorMessage ? <p className="error-banner fade-up">{errorMessage}</p> : null}

      <section className="panel fade-up">
        <div className="filters-grid">
          <label className="field">
            <span>Run</span>
            <select
              onChange={(event) =>
                setRunId(event.target.value ? Number(event.target.value) : undefined)
              }
              value={runId ?? ""}
            >
              <option value="">All runs</option>
              {runsQuery.data?.items.map((run) => (
                <option key={run.id} value={run.id}>
                  Run {run.id} • {titleCase(run.ui_status)}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Status</span>
            <select onChange={(event) => setStatus(event.target.value)} value={status}>
              <option value="">All statuses</option>
              <option value="ready_to_apply">Ready To Apply</option>
              <option value="applying">Applying</option>
              <option value="applied">Applied</option>
              <option value="failed">Failed</option>
              <option value="blocked">Blocked</option>
              <option value="incomplete">Incomplete</option>
            </select>
          </label>

          <label className="field">
            <span>Source</span>
            <input
              onChange={(event) => setSource(event.target.value)}
              placeholder="greenhouse, ashby, lever..."
              value={source}
            />
          </label>

          <label className="field field--search">
            <span>Search</span>
            <input
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Title, company, location, or URL"
              value={search}
            />
          </label>
        </div>
      </section>

      <section className="panel fade-up">
        <div className="panel__header">
          <h3>Tracked Jobs</h3>
          <p>{jobsQuery.data?.total ?? 0} total matches</p>
        </div>

        {jobsQuery.isLoading ? (
          <div className="loading-panel">Loading jobs...</div>
        ) : null}

        {!jobsQuery.isLoading && jobsQuery.data?.items.length === 0 ? (
          <EmptyState
            body="Try widening the filters or start a run to generate jobs."
            title="No jobs match the current filters"
          />
        ) : null}

        {jobsQuery.data?.items.length ? (
          <>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Application</th>
                    <th>Resume</th>
                    <th>Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {jobsQuery.data.items.map((job) => (
                    <tr
                      className="data-table__interactive"
                      key={job.job_key}
                      onClick={() =>
                        startTransition(() => setSelectedJobKey(job.job_key))
                      }
                    >
                      <td>
                        <strong>{job.title ?? job.job_key}</strong>
                        <span className="table-meta">
                          {job.company ?? "Unknown company"} • {job.location ?? "Unknown location"}
                        </span>
                      </td>
                      <td>
                        <StatusBadge status={job.status} />
                      </td>
                      <td>{job.latest_application_status ?? "Not attempted"}</td>
                      <td>
                        <strong>{job.resume_info.label ?? "Default resume"}</strong>
                        <span className="table-meta">{titleCase(job.resume_info.source)}</span>
                      </td>
                      <td>{formatDateTime(job.last_updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="pagination-row">
              <button
                className="ghost-button"
                disabled={page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                type="button"
              >
                Previous
              </button>
              <span>
                Page {jobsQuery.data.page} of {jobsQuery.data.total_pages}
              </span>
              <button
                className="ghost-button"
                disabled={page >= jobsQuery.data.total_pages}
                onClick={() =>
                  setPage((current) =>
                    Math.min(jobsQuery.data!.total_pages, current + 1)
                  )
                }
                type="button"
              >
                Next
              </button>
            </div>
          </>
        ) : null}
      </section>

      <Drawer
        isOpen={selectedJobKey !== null}
        onClose={() => setSelectedJobKey(null)}
        title={detailQuery.data?.title ?? selectedJobKey ?? "Job detail"}
      >
        {detailQuery.isLoading ? (
          <div className="loading-panel">Loading job detail...</div>
        ) : null}

        {detailQuery.data ? (
          <div className="drawer-stack">
            <section className="panel panel--inset">
              <div className="panel__header">
                <h3>Job Snapshot</h3>
                <StatusBadge status={detailQuery.data.status} />
              </div>
              <p className="detail-title">
                {detailQuery.data.company ?? "Unknown company"} •{" "}
                {detailQuery.data.location ?? "Unknown location"}
              </p>
              <p className="table-meta">{detailQuery.data.canonical_url}</p>
            </section>

            <section className="panel panel--inset">
              <div className="panel__header">
                <h3>Resume Used</h3>
                <span className="eyebrow">{titleCase(detailQuery.data.resume_info.source)}</span>
              </div>
              <div className="resume-card">
                <strong>{detailQuery.data.resume_info.label ?? "Default resume"}</strong>
                <p>{detailQuery.data.resume_info.path ?? "No resume path was recorded."}</p>
              </div>
              <div className="placeholder-card">
                <p className="eyebrow">Reserved Space</p>
                <h4>Resume Customization Panel</h4>
                <p>
                  This area is provisioned for a future iteration where job-specific
                  resume tailoring and previewing will live.
                </p>
              </div>
            </section>

            <section className="panel panel--inset">
              <div className="panel__header">
                <h3>Application History</h3>
                <p>{detailQuery.data.application_history.length} attempts</p>
              </div>
              {detailQuery.data.application_history.length === 0 ? (
                <EmptyState
                  body="No application attempt has been recorded for this job."
                  title="No history"
                />
              ) : (
                <div className="stack-list">
                  {detailQuery.data.application_history.map((application) => (
                    <article className="stack-list__item" key={application.id}>
                      <div className="inline-stack">
                        <strong>{titleCase(application.status)}</strong>
                        <span className="table-meta">
                          {formatDateTime(application.applied_at)}
                        </span>
                      </div>
                      <p>{application.confirmation_text ?? application.error_message ?? "No extra details recorded."}</p>
                    </article>
                  ))}
                </div>
              )}
            </section>

            <section className="panel panel--inset">
              <div className="panel__header">
                <h3>Findings</h3>
                <p>{detailQuery.data.findings.length} findings</p>
              </div>
              {detailQuery.data.findings.length === 0 ? (
                <EmptyState
                  body="No blocked, incomplete, or failure findings were stored for this job."
                  title="No findings"
                />
              ) : (
                <div className="stack-list">
                  {detailQuery.data.findings.map((finding) => (
                    <article className="stack-list__item" key={finding.id}>
                      <div className="inline-stack">
                        <strong>{titleCase(finding.category)}</strong>
                        <StatusBadge status={finding.application_status} />
                      </div>
                      <p>{finding.summary}</p>
                      {finding.detail ? (
                        <p className="table-meta">{finding.detail}</p>
                      ) : null}
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>
        ) : null}
      </Drawer>
    </section>
  );
}
