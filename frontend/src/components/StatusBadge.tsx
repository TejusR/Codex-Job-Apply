import { titleCase } from "../lib/format";

interface StatusBadgeProps {
  status: string;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const tone = getBadgeTone(status);
  return (
    <span className={`status-badge status-badge--${tone}`}>
      {titleCase(status)}
    </span>
  );
}

function getBadgeTone(status: string): string {
  const normalized = status.toLowerCase();
  if (
    normalized.includes("completed") ||
    normalized === "submitted" ||
    normalized === "applied"
  ) {
    return "success";
  }
  if (
    normalized.includes("failed") ||
    normalized.includes("blocked") ||
    normalized.includes("error")
  ) {
    return "danger";
  }
  if (
    normalized.includes("running") ||
    normalized.includes("progress") ||
    normalized.includes("applying") ||
    normalized.includes("processing")
  ) {
    return "active";
  }
  if (normalized.includes("resume") || normalized.includes("pending")) {
    return "warning";
  }
  return "neutral";
}
