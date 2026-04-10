import type { RunDetail, RunsResponse, RunUiStatus } from "../types";

export function getRunsRefetchInterval(
  data: RunsResponse | undefined
): number | false {
  if (!data) {
    return 5000;
  }
  return data.items.some((item) => item.ui_status === "running" || item.ui_status === "needs_resume")
    ? 5000
    : false;
}

export function getRunDetailRefetchInterval(
  data: RunDetail | undefined
): number | false {
  if (!data) {
    return 5000;
  }
  return data.summary.ui_status === "running" || data.summary.ui_status === "needs_resume"
    ? 5000
    : false;
}

export function isTerminalRunStatus(status: RunUiStatus): boolean {
  return status === "completed" || status === "completed_with_errors";
}
