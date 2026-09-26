import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useLoaderData } from "react-router";
import { useNewRun } from "~/components/new-run";
import { Button } from "~/components/ui/button";
import { Card } from "~/components/ui/card";
import { Table } from "~/components/ui/table";
import { api } from "~/lib/api";
import { dateTime, duration, fileName, percent } from "~/lib/utils";

export async function clientLoader() {
  return Promise.all([api.runs(), api.sources()]).then(([runs, sources]) => ({
    runs,
    sources,
  }));
}

export default function Runs() {
  const initial = useLoaderData<typeof clientLoader>();
  const { open } = useNewRun();
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    initialData: initial.runs,
    refetchInterval: 5_000,
  }).data;
  const [status, setStatus] = useState("all");
  const [source, setSource] = useState("all");
  const filtered = useMemo(
    () =>
      runs.filter(
        (run) =>
          (status === "all" || run.status === status) &&
          (source === "all" || run.source_file === source),
      ),
    [runs, status, source],
  );
  const today = runs.filter(
    (run) =>
      new Date(run.created_at).toDateString() === new Date().toDateString(),
  );
  return (
    <div className="page runs-page">
      <div className="page-header">
        <div>
          <h1>Runs</h1>
          <p>
            {today.length} runs today ·{" "}
            {runs.filter((run) => run.active).length} running · stored in
            .regress/runs
          </p>
        </div>
        <div className="row gap-sm">
          <div className="segmented">
            {["all", "running", "completed", "failed", "cancelled"].map(
              (item) => (
                <button
                  type="button"
                  key={item}
                  className={status === item ? "selected" : ""}
                  onClick={() => setStatus(item)}
                >
                  {item[0].toUpperCase() + item.slice(1)}{" "}
                  {item === "all"
                    ? runs.length
                    : runs.filter((run) => run.status === item).length}
                </button>
              ),
            )}
          </div>
          <select
            aria-label="Filter by source file"
            value={source}
            onChange={(event) => setSource(event.target.value)}
            className="source-filter"
          >
            <option value="all">All files</option>
            {initial.sources.map((item) => (
              <option key={item.path} value={item.path}>
                {item.path}
              </option>
            ))}
          </select>
        </div>
      </div>
      <Card className="runs-timeline">
        <div className="row between">
          <h3>Today</h3>
          <span className="small muted">
            Each mark is a run · {new Date().toLocaleDateString()}
          </span>
        </div>
        {today.length ? (
          <div className="timeline-list">
            {today.slice(0, 8).map((run) => (
              <Link
                key={run.id}
                to={`/runs/${encodeURIComponent(run.id)}`}
                className="timeline-row"
              >
                <span className="mono">{fileName(run.source_file)}</span>
                <div className="timeline-track">
                  <i
                    className={run.status}
                    style={{
                      marginLeft: `${Math.min(80, Math.max(0, ((new Date(run.created_at).getHours() - 8) / 14) * 80))}%`,
                      width: `${Math.max(2, Math.min(16, run.duration_seconds / 60))}%`,
                    }}
                  />
                </div>
                <span className="small">
                  {run.status === "completed"
                    ? percent(run.kept_score)
                    : run.status}{" "}
                  · {duration(run.duration_seconds)}
                </span>
              </Link>
            ))}
          </div>
        ) : (
          <p className="empty-inline">No runs today.</p>
        )}
      </Card>
      <Table>
        <thead>
          <tr>
            <th>Run</th>
            <th>Source</th>
            <th>Status</th>
            <th className="right">Tests</th>
            <th className="right">First</th>
            <th className="right">Kept</th>
            <th className="right">Δ</th>
            <th>Duration</th>
            <th>Started</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((run) => (
            <tr key={run.id}>
              <td>
                <Link
                  to={`/runs/${encodeURIComponent(run.id)}`}
                  className="mono text-link"
                >
                  {run.id}
                </Link>
              </td>
              <td>
                <div className="mono">{run.source_file}</div>
                <small className="muted">{run.model}</small>
              </td>
              <td>
                <span className={`status-dot ${run.status}`} />
                {run.status === "running"
                  ? "Running"
                  : run.status[0].toUpperCase() + run.status.slice(1)}
              </td>
              <td className="right">
                {run.tests_before} → {run.tests_after ?? "—"}
              </td>
              <td className="right">{percent(run.reference_score)}</td>
              <td className="right">{percent(run.kept_score)}</td>
              <td className="right">
                {run.improvement == null
                  ? "—"
                  : `${run.improvement >= 0 ? "+" : ""}${Math.round(run.improvement)}%`}
              </td>
              <td className="muted">{duration(run.duration_seconds)}</td>
              <td className="muted">{dateTime(run.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {!filtered.length && (
        <div className="empty-state">
          <h3>No runs match these filters</h3>
          <p>Change the filters or start a run.</p>
          <Button onClick={() => open()}>New run</Button>
        </div>
      )}
    </div>
  );
}
