import type {
  JobDetail,
  JobListResponse,
  JobsQuery,
  RequeueRunnerFailuresResponse,
  RunActionResponse,
  RunDetail,
  RunsResponse
} from "./types";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ?? detail;
    } catch {
      detail = await response.text();
    }
    throw new Error(detail || "Request failed");
  }

  return (await response.json()) as T;
}

export function fetchRuns(): Promise<RunsResponse> {
  return apiFetch<RunsResponse>("/api/runs");
}

export function startRun(): Promise<RunActionResponse> {
  return apiFetch<RunActionResponse>("/api/runs", {
    method: "POST"
  });
}

export function resumeRun(runId: number): Promise<RunActionResponse> {
  return apiFetch<RunActionResponse>(`/api/runs/${runId}/resume`, {
    method: "POST"
  });
}

export function requeueRunnerFailures(
  runId: number
): Promise<RequeueRunnerFailuresResponse> {
  return apiFetch<RequeueRunnerFailuresResponse>(
    `/api/runs/${runId}/requeue-runner-failures`,
    {
      method: "POST"
    }
  );
}

export function finishRun(
  runId: number,
  force: boolean
): Promise<RunActionResponse> {
  return apiFetch<RunActionResponse>(`/api/runs/${runId}/finish`, {
    method: "POST",
    body: JSON.stringify({ force })
  });
}

export function fetchRunDetail(runId: number): Promise<RunDetail> {
  return apiFetch<RunDetail>(`/api/runs/${runId}`);
}

export function fetchJobs(query: JobsQuery): Promise<JobListResponse> {
  const params = new URLSearchParams();
  if (query.runId) {
    params.set("run_id", String(query.runId));
  }
  if (query.status) {
    params.set("status", query.status);
  }
  if (query.source) {
    params.set("source", query.source);
  }
  if (query.q) {
    params.set("q", query.q);
  }
  params.set("page", String(query.page ?? 1));
  params.set("page_size", String(query.pageSize ?? 20));
  return apiFetch<JobListResponse>(`/api/jobs?${params.toString()}`);
}

export function fetchJobDetail(jobKey: string): Promise<JobDetail> {
  return apiFetch<JobDetail>(`/api/jobs/${encodeURIComponent(jobKey)}`);
}
