import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { startTransition } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  fetchRuns,
  finishRun,
  requeueRunnerFailures,
  resumeRun,
  startRun
} from "../api";
import { EmptyState } from "../components/EmptyState";
import { MetricCard } from "../components/MetricCard";
import { StatusBadge } from "../components/StatusBadge";
import { compactNumber, formatDateTime } from "../lib/format";
import { getRunsRefetchInterval } from "../lib/polling";
import type { RunSummary } from "../types";

export function RunsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const runsQuery = useQuery({
    queryKey: ["runs"],
    queryFn: fetchRuns,
    refetchInterval: (query) => getRunsRefetchInterval(query.state.data),
    refetchOnWindowFocus: false
  });

  const startRunMutation = useMutation({
    mutationFn: startRun,
    onSuccess: async (payload) => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      startTransition(() => navigate(`/runs/${payload.run.id}`));
    }
  });

  const resumeMutation = useMutation({
    mutationFn: (runId: number) => resumeRun(runId),
    onSuccess: async (payload) => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      await queryClient.invalidateQueries({ queryKey: ["run", payload.run.id] });
    }
  });

  const requeueMutation = useMutation({
    mutationFn: (runId: number) => requeueRunnerFailures(runId),
    onSuccess: async (payload) => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      await queryClient.invalidateQueries({ queryKey: ["run", payload.run.id] });
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    }
  });

  const finishMutation = useMutation({
    mutationFn: ({ runId, force }: { runId: number; force: boolean }) =>
      finishRun(runId, force),
    onSuccess: async (payload) => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      await queryClient.invalidateQueries({ queryKey: ["run", payload.run.id] });
    }
  });

  const errorMessage = [
    runsQuery.error,
    startRunMutation.error,
    resumeMutation.error,
    requeueMutation.error,
    finishMutation.error
  ]
    .find(Boolean)
    ?.toString()
    .replace(/^Error:\s*/, "");

  const handleFinish = (run: RunSummary) => {
    const force = run.allowed_actions.force_finish;
    if (
      force &&
      !window.confirm(
        "This run still has unresolved work. Force-finish it anyway?"
      )
    ) {
      return;
    }
    finishMutation.mutate({ runId: run.id, force });
  };

  return (
    <section className="page-section">
      <div className="section-header fade-up">
        <div>
          <p className="eyebrow">Run Control</p>
          <h2>Workflow Runs</h2>
          <p className="section-copy">
            Launch a new workflow, resume interrupted work, and inspect the health of
            discovery plus application queues at a glance.
          </p>
        </div>
        <div className="button-row">
          <button
            className="ghost-button"
            onClick={() => void runsQuery.refetch()}
            type="button"
          >
            Refresh
          </button>
          <button
            className="primary-button"
            disabled={!runsQuery.data?.can_start_run || startRunMutation.isPending}
            onClick={() => startRunMutation.mutate()}
            type="button"
          >
            {startRunMutation.isPending ? "Starting..." : "Start New Run"}
          </button>
        </div>
      </div>

      {errorMessage ? <p className="error-banner fade-up">{errorMessage}</p> : null}

      {runsQuery.data && !runsQuery.data.can_start_run ? (
        <div className="notice-card fade-up">
          <p className="eyebrow">Run Lock</p>
          <strong>Run {runsQuery.data.blocked_by_run_id} is still active.</strong>
          <p>
            Finish or resume that run before starting another one so queue counts stay
            unambiguous.
          </p>
        </div>
      ) : null}

      {runsQuery.isLoading ? (
        <div className="loading-panel fade-up">Loading runs...</div>
      ) : null}

      {!runsQuery.isLoading && runsQuery.data?.items.length === 0 ? (
        <EmptyState
          body="There are no recorded runs yet. Start the first workflow run from this page."
          title="No runs have been created"
        />
      ) : null}

      <div className="run-grid">
        {runsQuery.data?.items.map((run, index) => (
          <article
            className="run-card fade-up"
            key={run.id}
            style={{ animationDelay: `${index * 80}ms` }}
          >
            <div className="run-card__header">
              <div>
                <p className="run-card__title">Run {run.id}</p>
                <p className="run-card__subtitle">
                  Started {formatDateTime(run.started_at)}
                </p>
              </div>
              <StatusBadge status={run.ui_status} />
            </div>

            <div className="metric-grid">
              <MetricCard label="Jobs Found" value={compactNumber(run.jobs_found)} />
              <MetricCard label="Applied" value={compactNumber(run.jobs_applied)} />
              <MetricCard label="Failed" value={compactNumber(run.jobs_failed)} />
              <MetricCard
                detail={`${run.search_summary.pending_queries} pending / ${run.search_summary.failed_queries} failed`}
                label="Queries"
                value={compactNumber(run.search_summary.total_queries)}
              />
            </div>

            <div className="run-card__stats">
              <div>
                <span>Ready jobs</span>
                <strong>{run.live_counts.ready_jobs}</strong>
              </div>
              <div>
                <span>Applying</span>
                <strong>{run.live_counts.applying_jobs}</strong>
              </div>
              <div>
                <span>Pending results</span>
                <strong>{run.live_counts.search_results_pending}</strong>
              </div>
            </div>

            <div className="button-row button-row--wrap">
              <Link className="ghost-button" to={`/runs/${run.id}`}>
                Open
              </Link>
              <button
                className="ghost-button"
                disabled={!run.allowed_actions.resume || resumeMutation.isPending}
                onClick={() => resumeMutation.mutate(run.id)}
                type="button"
              >
                Resume
              </button>
              <button
                className="ghost-button"
                disabled={
                  !run.allowed_actions.requeue_runner_failures ||
                  requeueMutation.isPending
                }
                onClick={() => requeueMutation.mutate(run.id)}
                type="button"
              >
                Requeue Failures
              </button>
              <button
                className="ghost-button"
                disabled={!run.allowed_actions.finish || finishMutation.isPending}
                onClick={() => handleFinish(run)}
                type="button"
              >
                {run.allowed_actions.force_finish ? "Force Finish" : "Finish"}
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
