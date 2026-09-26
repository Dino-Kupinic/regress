import { StatusDot } from "~/components/status-dot";
import { Badge } from "~/components/ui/badge";
import type { RunSummary } from "~/lib/api";

const labels: Record<RunSummary["status"], string> = {
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function RunStatusBadge({
  status,
  active = false,
}: {
  status: RunSummary["status"];
  active?: boolean;
}) {
  if (active || status === "running") {
    return (
      <Badge variant="outline">
        <StatusDot tone="success" pulse />
        <span className="shimmer">Running</span>
      </Badge>
    );
  }
  if (status === "failed") {
    return (
      <Badge variant="destructive">
        <StatusDot tone="destructive" />
        {labels.failed}
      </Badge>
    );
  }
  return (
    <Badge variant={status === "completed" ? "outline" : "secondary"}>
      <StatusDot tone={status === "completed" ? "default" : "muted"} />
      {labels[status]}
    </Badge>
  );
}
