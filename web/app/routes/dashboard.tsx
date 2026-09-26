import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Terminal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useLoaderData } from "react-router";
import { useNewRun } from "~/components/new-run";
import { Button } from "~/components/ui/button";
import { Card } from "~/components/ui/card";
import { Table } from "~/components/ui/table";
import { api, type RunSummary } from "~/lib/api";
import { dateTime, fileName, percent } from "~/lib/utils";

export async function clientLoader() {
  const [project, sources, runs, settings] = await Promise.all([
    api.project(),
    api.sources(),
    api.runs(),
    api.settings(),
  ]);
  const details = await Promise.all(
    sources.map((source) => api.source(source.path).catch(() => null)),
  );
  return { project, sources, runs, settings, details };
}

export default function Dashboard() {
  const initial = useLoaderData<typeof clientLoader>();
  const { open } = useNewRun();
  const project = useQuery({
    queryKey: ["project"],
    queryFn: api.project,
    initialData: initial.project,
    refetchInterval: 5_000,
  }).data;
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: api.sources,
    initialData: initial.sources,
  }).data;
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    initialData: initial.runs,
    refetchInterval: 8_000,
  }).data;
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: api.settings,
    initialData: initial.settings,
  }).data;
  const [source, setSource] = useState(sources[0]?.path ?? "");
  const [filter, setFilter] = useState<"all" | "never" | "low">("all");
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        event.key.toLowerCase() === "n" &&
        !(event.target instanceof HTMLInputElement) &&
        !(event.target instanceof HTMLTextAreaElement)
      ) {
        event.preventDefault();
        open();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  const latestByFile = useMemo(
    () =>
      new Map(
        sources.map((item) => [
          item.path,
          runs.find((run) => run.source_file === item.path),
        ]),
      ),
    [sources, runs],
  );
  const latestCompletedByFile = useMemo(
    () =>
      new Map(
        sources.map((item) => [
          item.path,
          runs.find(
            (run) =>
              run.source_file === item.path &&
              run.status === "completed" &&
              run.kept_score != null,
          ),
        ]),
      ),
    [sources, runs],
  );
  const completed = sources
    .map((item) => latestCompletedByFile.get(item.path))
    .filter(
      (run): run is RunSummary =>
        !!run && run.status === "completed" && run.kept_score != null,
    );
  const average = completed.length
    ? completed.reduce((sum, run) => sum + (run.kept_score ?? 0), 0) /
      completed.length
    : null;
  const added = completed.reduce(
    (sum, run) =>
      sum +
      Math.max(0, (run.tests_after ?? run.tests_before) - run.tests_before),
    0,
  );
  const atTarget = completed.filter(
    (run) => (run.kept_score ?? 0) >= 80,
  ).length;
  const latest = runs.find(
    (run) => run.status === "completed" && run.kept_score != null,
  );
  const latestDetail = useQuery({
    queryKey: ["run", latest?.id],
    queryFn: () => api.run(latest?.id ?? ""),
    enabled: !!latest?.id,
  }).data;
  const chosen = sources.find((item) => item.path === source);
  const chosenDetail =
    initial.details[sources.findIndex((item) => item.path === source)];
  const rows = sources.filter((item) => {
    const run = latestByFile.get(item.path);
    if (filter === "never") return !run;
    if (filter === "low") return !run || (run.kept_score ?? 0) < 60;
    return true;
  });
  const scoreTone = (value: number | null | undefined) =>
    value == null
      ? "muted"
      : value >= 80
        ? "score-high"
        : value >= 60
          ? "score-mid"
          : "score-low";
  const mutation = latestDetail?.report.stages.find(
    (stage) => stage.label === latestDetail.report.kept_stage,
  )?.mutation;
  const operators = mutation
    ? Object.entries(
        mutation.mutants.reduce<
          Record<string, { killed: number; total: number }>
        >((acc, mutant) => {
          const item = acc[mutant.mutator] ?? { killed: 0, total: 0 };
          item.total++;
          if (mutant.status === "Killed" || mutant.status === "Timeout")
            item.killed++;
          acc[mutant.mutator] = item;
          return acc;
        }, {}),
      )
        .sort((a, b) => b[1].total - a[1].total)
        .slice(0, 6)
    : [];
  return (
    <div className="page dashboard">
      <div className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p>
            {sources.length} source files ·{" "}
            {initial.details.filter((item) => item?.test_file_exists).length}{" "}
            with tests ·{" "}
            {project.toolchain
              ? `Vitest ${project.toolchain.vitest} · Stryker ${project.toolchain.stryker} · ${project.toolchain.runner}`
              : "Toolchain needs setup"}
          </p>
        </div>
        <span className="small muted">
          {runs[0] ? `Last run ${dateTime(runs[0].created_at)}` : "No runs yet"}
        </span>
      </div>
      {!project.ready && (
        <div className="setup-notice">
          <span>{project.problems[0] ?? "Project setup is incomplete"}</span>
          <Link to="/setup">
            Set up project <ArrowRight size={14} />
          </Link>
        </div>
      )}
      <div className="quick-run">
        <Terminal size={17} />
        <select
          aria-label="Source file to run"
          value={source}
          onChange={(event) => setSource(event.target.value)}
        >
          {sources.map((item) => (
            <option key={item.path} value={item.path}>
              {item.path}
            </option>
          ))}
        </select>
        <span className="quick-run-hint">
          {chosenDetail
            ? chosenDetail.test_file_exists
              ? `${chosenDetail.test_file} will be extended`
              : `${chosenDetail.test_file} will be created`
            : chosen
              ? `${chosen.size} bytes`
              : "Choose a source file"}
        </span>
        <span className="model-pill mono">{settings.effective.model}</span>
        <Button
          onClick={() => open(source)}
          disabled={!source || !project.ready || !!project.active_run}
        >
          Run regress
        </Button>
      </div>
      <div className="kpi-grid">
        <Card className="kpi">
          <span className="muted">Project mutation score</span>
          <div className="kpi-value">{percent(average)}</div>
          <div className="meter">
            <span style={{ width: `${average ?? 0}%` }} />
            <i style={{ left: "80%" }} />
          </div>
          <div className="row between tiny muted">
            <span>
              {completed.length} of {sources.length} files measured
            </span>
            <span>Target 80%</span>
          </div>
        </Card>
        <Card className="kpi">
          <span className="muted">Files at 80% or more</span>
          <div className="kpi-value">
            {atTarget}
            <small> of {sources.length}</small>
          </div>
          <div className="square-strip">
            {sources.map((item) => (
              <i
                key={item.path}
                className={
                  (latestCompletedByFile.get(item.path)?.kept_score ?? 0) >= 80
                    ? "filled"
                    : ""
                }
              />
            ))}
          </div>
          <span className="tiny muted">Based on latest completed runs</span>
        </Card>
        <Card className="kpi">
          <span className="muted">Tests added by Regress</span>
          <div className="kpi-value">{added}</div>
          <div className="mini-bars">
            {Array.from({ length: 28 }, (_, index) => (
              <i
                key={index}
                className={index < Math.min(added, 28) ? "filled" : ""}
              />
            ))}
          </div>
          <span className="tiny muted">From latest completed runs</span>
        </Card>
        <Card className="kpi">
          <span className="muted">Recent runs</span>
          <div className="kpi-value">{runs.length}</div>
          <div className="meter">
            <span style={{ width: `${Math.min(100, runs.length * 12)}%` }} />
          </div>
          <span className="tiny muted">
            {runs.filter((run) => run.active).length} active ·{" "}
            {runs.filter((run) => run.status === "completed").length} completed
          </span>
        </Card>
      </div>
      <div className="dashboard-two first">
        <Card className="chart-card">
          <div className="section-heading">
            <div>
              <h3>Mutation score by file</h3>
              <p>Latest kept tests for each source</p>
            </div>
            <span className="tiny muted">Target 80%</span>
          </div>
          <div className="file-chart">
            {sources.map((item) => {
              const run = latestByFile.get(item.path);
              return (
                <div className="file-chart-row" key={item.path}>
                  <span className="mono truncate">{fileName(item.path)}</span>
                  <div className="file-chart-track">
                    <i className="target-line" />
                    <span
                      style={{ width: `${run?.kept_score ?? 0}%` }}
                      className={run?.active ? "pending-line" : ""}
                    />
                  </div>
                  <b className={scoreTone(run?.kept_score)}>
                    {run?.active ? "Running" : percent(run?.kept_score)}
                  </b>
                </div>
              );
            })}
          </div>
          <div className="axis">
            <span>0</span>
            <span>20</span>
            <span>40</span>
            <span>60</span>
            <span>80</span>
            <span>100%</span>
          </div>
        </Card>
        <Card className="chart-card latest-card">
          <div className="row between">
            <div>
              <h3>
                Latest completed run
                {latest ? ` · ${fileName(latest.source_file)}` : ""}
              </h3>
              <p className="small muted">
                Every mutant is a tiny test of coverage
              </p>
            </div>
            {latest && (
              <Link
                className="text-link"
                to={`/runs/${encodeURIComponent(latest.id)}`}
              >
                Report
              </Link>
            )}
          </div>
          {latest ? (
            <>
              <div className="huge-score">
                {percent(latest.kept_score)}{" "}
                <span className={scoreTone(latest.improvement)}>
                  {latest.improvement == null
                    ? ""
                    : `${latest.improvement >= 0 ? "+" : ""}${Math.round(latest.improvement)} pts`}
                </span>
              </div>
              <div
                className="mutant-grid"
                role="img"
                aria-label="Mutation outcomes"
              >
                {mutation?.mutants.slice(0, 110).map((mutant) => (
                  <i
                    key={mutant.id}
                    className={
                      mutant.status === "Killed" || mutant.status === "Timeout"
                        ? "killed"
                        : "survived"
                    }
                  />
                ))}
              </div>
              <p className="small muted">
                {mutation
                  ? `${mutation.killed} killed · ${mutation.survived} survived · ${mutation.no_coverage} uncovered`
                  : `${latest.tests_before} → ${latest.tests_after ?? latest.tests_before} tests`}
              </p>
              <div className="stage-mini">
                {latestDetail?.report.stages
                  .filter((stage) => stage.mutation)
                  .map((stage) => (
                    <div key={stage.label} className="row gap-sm">
                      <span className="grow">{stage.label}</span>
                      <div className="meter">
                        <span
                          style={{ width: `${stage.mutation?.score ?? 0}%` }}
                        />
                      </div>
                      <strong>{percent(stage.mutation?.score)}</strong>
                    </div>
                  ))}
              </div>
            </>
          ) : (
            <div className="empty-state">
              <h3>Run your first source file</h3>
              <p>Your latest report will appear here.</p>
              <Button onClick={() => open()}>New run</Button>
            </div>
          )}
        </Card>
      </div>
      <div className="dashboard-two second">
        <Card className="chart-card">
          <div className="section-heading">
            <div>
              <h3>Mutants by operator</h3>
              <p>
                {latest
                  ? `${fileName(latest.source_file)} · kept tests · ${mutation?.mutants.length ?? 0} mutants`
                  : "Run a source file to see operator coverage"}
              </p>
            </div>
            <span className="tiny muted">Killed / total</span>
          </div>
          {operators.length ? (
            <div className="operator-list">
              {operators.map(([name, data]) => (
                <div key={name} className="operator-row">
                  <span className="mono truncate">{name}</span>
                  <div className="meter">
                    <span
                      style={{ width: `${(100 * data.killed) / data.total}%` }}
                    />
                  </div>
                  <b>
                    {data.killed} / {data.total}
                  </b>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state">No mutation data yet.</div>
          )}
        </Card>
        <Card className="chart-card">
          <div className="section-heading">
            <div>
              <h3>Hidden-bug evaluation</h3>
              <p>Check whether better tests catch real regressions</p>
            </div>
          </div>
          <div className="evaluation-promo">
            <div className="score-symbol">◎</div>
            <p>
              Mutation scores are one signal. Compare them with seeded hidden
              bugs in Evaluation.
            </p>
            <Link to="/evaluation" className="text-link">
              Open evaluation <ArrowRight size={14} />
            </Link>
          </div>
        </Card>
      </div>
      <div className="source-section">
        <div className="section-heading">
          <div>
            <h2>Source files</h2>
            <p>Select a file to inspect its latest run or start a new one.</p>
          </div>
          <div className="segmented">
            <button
              type="button"
              className={filter === "all" ? "selected" : ""}
              onClick={() => setFilter("all")}
            >
              All {sources.length}
            </button>
            <button
              type="button"
              className={filter === "never" ? "selected" : ""}
              onClick={() => setFilter("never")}
            >
              Never run
            </button>
            <button
              type="button"
              className={filter === "low" ? "selected" : ""}
              onClick={() => setFilter("low")}
            >
              Below 60%
            </button>
          </div>
        </div>
        <Table>
          <thead>
            <tr>
              <th>File</th>
              <th>Lines</th>
              <th>Test file</th>
              <th className="right">Tests</th>
              <th className="right">Regress</th>
              <th>Last run</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => {
              const detail =
                initial.details[
                  sources.findIndex((source) => source.path === item.path)
                ];
              const run = latestByFile.get(item.path);
              return (
                <tr key={item.path}>
                  <td className="mono">{item.path}</td>
                  <td className="muted">{detail?.lines ?? "—"}</td>
                  <td className="mono muted">{detail?.test_file ?? "—"}</td>
                  <td className="right">
                    {run?.tests_after ?? run?.tests_before ?? "—"}
                  </td>
                  <td className={`right ${scoreTone(run?.kept_score)}`}>
                    {run?.active ? "Running" : percent(run?.kept_score)}
                  </td>
                  <td className="muted">
                    {run ? dateTime(run.created_at) : "Never"}
                  </td>
                  <td className="right">
                    {run ? (
                      <Link
                        className="text-link"
                        to={`/runs/${encodeURIComponent(run.id)}`}
                      >
                        Open
                      </Link>
                    ) : (
                      <button
                        type="button"
                        className="text-link"
                        onClick={() => open(item.path)}
                      >
                        Run
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
        {!rows.length && (
          <div className="empty-state">No source files match this filter.</div>
        )}
      </div>
    </div>
  );
}
