import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  fetchRunDetail,
  finishRun,
  requeueRunnerFailures,
  resumeRun
} from "../api";
import { EmptyState } from "../components/EmptyState";
import { MetricCard } from "../components/MetricCard";
import { StatusBadge } from "../components/StatusBadge";
import { compactNumber, formatDateTime, titleCase } from "../lib/format";
import { getRunDetailRefetchInterval } from "../lib/polling";

export function RunDetailPage() {
  const navigate = useNavigate();
  const { runId } = useParams();
  const parsedRunId = Number(runId);
  const queryClient = useQueryClient();

  const runQuery = useQuery({
    queryKey: ["run", parsedRunId],
    queryFn: () => fetchRunDetail(parsedRunId),
    enabled: Number.isFinite(parsedRunId),
    refetchInterval: (query) => getRunDetailRefetchInterval(query.state.data),
    refetchOnWindowFocus: false
  });

  const resumeMutation = useMutation({
    mutationFn: () => resumeRun(parsedRunId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      await queryClient.invalidateQueries({ queryKey: ["run", parsedRunId] });
    }
  });

  const requeueMutation = useMutation({
    mutationFn: () => requeueRunnerFailures(parsedRunId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      await queryClient.invalidateQueries({ queryKey: ["run", parsedRunId] });
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    }
  });

  const finishMutation = useMutation({
    mutationFn: (force: boolean) => finishRun(parsedRunId, force),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      await queryClient.invalidateQueries({ queryKey: ["run", parsedRunId] });
    }
  });

  if (!Number.isFinite(parsedRunId)) {
    return (
      <EmptyState
        body="The selected run id is not valid."
        title="Run not found"
      />
    );
  }

  const errorMessage = [
    runQuery.error,
    resumeMutation.error,
    requeueMutation.error,
    finishMutation.error
  ]
    .find(Boolean)
    ?.toString()
    .replace(/^Error:\s*/, "");

  const handleFinish = () => {
    if (!runQuery.data) {
      return;
    }
    const force = runQuery.data.summary.allowed_actions.force_finish;
    if (
      force &&
      !window.confirm(
        "This run still has queued work. Force-finish it anyway?"
      )
    ) {
      return;
    }
    finishMutation.mutate(force);
  };

  return (
    <section className="page-section">
      <div className="section-header fade-up">
        <div>
          <button
            className="text-link"
            onClick={() => navigate("/runs")}
            type="button"
          >
            Back to runs
          </button>
          <h2>Run {parsedRunId}</h2>
          <p className="section-copy">
            Inspect queue pressure, worker activity, recent results, and the jobs this
            run touched.
          </p>
        </div>
        <div className="button-row">
          <button
            className="ghost-button"
            onClick={() => void runQuery.refetch()}
            type="button"
          >
            Refresh
          </button>
          <button
            className="ghost-button"
            disabled={!runQuery.data?.summary.allowed_actions.resume}
            onClick={() => resumeMutation.mutate()}
            type="button"
          >
            Resume
          </button>
          <button
            className="ghost-button"
            disabled={!runQuery.data?.summary.allowed_actions.requeue_runner_failures}
            onClick={() => requeueMutation.mutate()}
            type="button"
          >
            Requeue Failures
          </button>
          <button
            className="primary-button"
            disabled={!runQuery.data?.summary.allowed_actions.finish}
            onClick={handleFinish}
            type="button"
          >
            {runQuery.data?.summary.allowed_actions.force_finish
              ? "Force Finish"
              : "Finish"}
          </button>
        </div>
      </div>

      {errorMessage ? <p className="error-banner fade-up">{errorMessage}</p> : null}

      {runQuery.isLoading ? <div className="loading-panel">Loading run detail...</div> : null}

      {runQuery.data ? (
        <>
          <div className="run-hero fade-up">
            <div>
              <p className="eyebrow">Run Status</p>
              <div className="inline-stack">
                <h3>Started {formatDateTime(runQuery.data.summary.started_at)}</h3>
                <StatusBadge status={runQuery.data.summary.ui_status} />
              </div>
              <p className="section-copy">
                Finished {formatDateTime(runQuery.data.summary.finished_at)}. Findings
                recorded: {runQuery.data.summary.findings_total}.
              </p>
            </div>
            <div className="metric-grid">
              <MetricCard
                label="Jobs Found"
                value={compactNumber(runQuery.data.summary.jobs_found)}
              />
              <MetricCard
                label="Applied"
                value={compactNumber(runQuery.data.summary.jobs_applied)}
              />
              <MetricCard
                label="Ready Jobs"
                value={runQuery.data.summary.live_counts.ready_jobs}
              />
              <MetricCard
                label="Search Results"
                value={compactNumber(runQuery.data.summary.search_summary.total_search_results)}
              />
            </div>
          </div>

          <div className="content-grid">
            <section className="panel fade-up">
              <div className="panel__header">
                <h3>Search Queries</h3>
                <p>{runQuery.data.queries.length} tracked sources</p>
              </div>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th>Status</th>
                      <th>Results</th>
                      <th>Jobs</th>
                      <th>Last Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runQuery.data.queries.map((query) => (
                      <tr key={query.id}>
                        <td>
                          <strong>{titleCase(query.source_key)}</strong>
                          <span className="table-meta">{query.domain}</span>
                        </td>
                        <td>
                          <StatusBadge status={query.status} />
                        </td>
                        <td>{query.results_seen}</td>
                        <td>{query.jobs_ingested}</td>
                        <td>{query.last_error ?? "None"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel fade-up">
              <div className="panel__header">
                <h3>Worker Sessions</h3>
                <p>{runQuery.data.worker_sessions.length} slots</p>
              </div>
              {runQuery.data.worker_sessions.length === 0 ? (
                <EmptyState
                  body="No worker sessions have been created for this run yet."
                  title="No workers"
                />
              ) : (
                <div className="stack-list">
                  {runQuery.data.worker_sessions.map((worker) => (
                    <article className="stack-list__item" key={worker.id}>
                      <div className="inline-stack">
                        <strong>
                          {titleCase(worker.worker_type)} / {worker.slot_key}
                        </strong>
                        <StatusBadge status={worker.status} />
                      </div>
                      <p className="table-meta">
                        Last active {formatDateTime(worker.last_used_at)}
                      </p>
                      <p>{worker.last_error ?? "No recorded worker errors."}</p>
                    </article>
                  ))}
                </div>
              )}
            </section>

            <section className="panel fade-up">
              <div className="panel__header">
                <h3>Recent Result Queue</h3>
                <p>{runQuery.data.recent_search_results.length} recent items</p>
              </div>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Result</th>
                      <th>Status</th>
                      <th>Source</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runQuery.data.recent_search_results.map((result) => (
                      <tr key={result.id}>
                        <td>
                          <strong>{result.title ?? result.url}</strong>
                          <span className="table-meta">{result.url}</span>
                        </td>
                        <td>
                          <StatusBadge status={result.status} />
                        </td>
                        <td>{titleCase(result.source_key)}</td>
                        <td>{result.reason ?? "Pending"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel fade-up">
              <div className="panel__header">
                <h3>Findings Summary</h3>
                <p>{runQuery.data.findings_summary.total_findings} findings</p>
              </div>
              {runQuery.data.findings_summary.by_category.length === 0 ? (
                <EmptyState
                  body="This run has not recorded blocked, incomplete, or failed findings."
                  title="No findings"
                />
              ) : (
                <div className="stack-list">
                  {runQuery.data.findings_summary.by_category.map((item) => (
                    <article className="stack-list__item" key={item.category}>
                      <div className="inline-stack">
                        <strong>{titleCase(item.category)}</strong>
                        <span>{item.count}</span>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>

          <section className="panel fade-up">
            <div className="panel__header">
              <div>
                <h3>Jobs Touched By This Run</h3>
                <p>{runQuery.data.jobs_preview_total} tracked jobs</p>
              </div>
              <Link className="ghost-button" to={`/jobs?runId=${parsedRunId}`}>
                Open Job List
              </Link>
            </div>
            {runQuery.data.jobs_preview.length === 0 ? (
              <EmptyState
                body="Jobs will appear here once discovery resolves visible search results into actual job records."
                title="No jobs for this run yet"
              />
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Role</th>
                      <th>Status</th>
                      <th>Application</th>
                      <th>Resume</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runQuery.data.jobs_preview.map((job) => (
                      <tr key={job.job_key}>
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
                        <td>{job.resume_info.label ?? "Default resume"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      ) : null}
    </section>
  );
}
