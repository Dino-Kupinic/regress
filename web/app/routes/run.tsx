import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Copy, Download } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  Link,
  useLoaderData,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";
import { useNewRun } from "~/components/new-run";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card } from "~/components/ui/card";
import { Table } from "~/components/ui/table";
import { api, type Mutant, type RunDetail, type RunEvent } from "~/lib/api";
import { dateTime, duration, fileName, percent } from "~/lib/utils";

export async function clientLoader({ params }: { params: { runId?: string } }) {
  if (!params.runId) throw new Error("Missing run ID");
  return Promise.all([api.run(params.runId), api.events(params.runId)]).then(
    ([run, events]) => ({ run, events }),
  );
}

type Tab = "overview" | "mutants" | "tests" | "model";

export default function RunPage() {
  const initial = useLoaderData<typeof clientLoader>();
  const { runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "overview";
  const { open } = useNewRun();
  const _navigate = useNavigate();
  const queryClient = useQueryClient();
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    initialData: initial.run,
    refetchInterval: (query) =>
      query.state.data?.summary.active ? 1_500 : false,
  }).data;
  const events = useQuery({
    queryKey: ["events", runId],
    queryFn: () => api.events(runId),
    initialData: initial.events,
    refetchInterval: run.summary.active ? 1_500 : false,
  }).data;
  const cancel = useMutation({
    mutationFn: () => api.cancelRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const setTab = (next: Tab) =>
    setParams(next === "overview" ? {} : { tab: next });
  const measured = run.report.stages.filter(
    (stage) => stage.mutation && !stage.rejected,
  );
  const kept = run.report.stages.find(
    (stage) => stage.label === run.report.kept_stage,
  );
  const current = kept?.mutation ?? measured.at(-1)?.mutation;
  const statusText = run.summary.active
    ? "Running"
    : run.summary.status[0].toUpperCase() + run.summary.status.slice(1);
  const isLive = run.summary.active;
  return (
    <div className="page run-page">
      <div className="run-header">
        <div className="breadcrumb">
          <Link to="/runs">Runs</Link>
          <span>/</span>
          <span className="mono">{run.summary.id}</span>
        </div>
        <div className="row between run-title">
          <div className="row gap-md">
            <h1 className="mono">{run.summary.source_file}</h1>
            <Badge className={run.summary.status}>
              {statusText}{" "}
              <span className="muted mono">
                {duration(
                  run.live?.elapsed_seconds ?? run.summary.duration_seconds,
                )}
              </span>
            </Badge>
          </div>
          <div className="row gap-sm">
            {isLive ? (
              <Button
                variant="outline"
                disabled={cancel.isPending || run.live?.cancel_requested}
                onClick={() => cancel.mutate()}
              >
                {run.live?.cancel_requested ? "Cancelling…" : "Cancel run"}
              </Button>
            ) : (
              <>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setTab("overview");
                    requestAnimationFrame(() =>
                      document
                        .getElementById("artifacts")
                        ?.scrollIntoView({ behavior: "smooth" }),
                    );
                  }}
                >
                  Open artifacts
                </Button>
                <Button
                  variant="outline"
                  onClick={() => open(run.summary.source_file)}
                >
                  Run again
                </Button>
              </>
            )}
          </div>
        </div>
        <div className="run-meta">
          <span>
            Tests <span className="mono">{run.summary.test_file}</span>
          </span>
          <span>Model {run.summary.model}</span>
          <span>
            {measured.length} mutation{" "}
            {measured.length === 1 ? "stage" : "stages"}
          </span>
          <span>{dateTime(run.summary.created_at)}</span>
        </div>
      </div>
      {run.summary.error && (
        <div className="run-error" role="alert">
          <strong>{statusText}</strong>
          <p>{run.summary.error}</p>
        </div>
      )}
      {isLive ? (
        <LiveRun run={run} events={events} />
      ) : (
        <>
          <div className="tabs" role="tablist" aria-label="Run report">
            {[
              ["overview", "Overview"],
              ["mutants", `Mutants ${current?.mutants.length ?? ""}`],
              ["tests", "Tests"],
              ["model", `Model log ${run.report.usage.calls || ""}`],
            ].map(([key, label]) => (
              <button
                type="button"
                key={key}
                role="tab"
                aria-selected={tab === key}
                className={tab === key ? "active" : ""}
                onClick={() => setTab(key as Tab)}
              >
                {label}
              </button>
            ))}
          </div>
          {tab === "overview" && <Overview run={run} setTab={setTab} />}
          {tab === "mutants" && <Mutants run={run} />}
          {tab === "tests" && <Tests run={run} />}
          {tab === "model" && <ModelLog run={run} />}
        </>
      )}
      {cancel.isError && (
        <p role="alert" className="error-text">
          {cancel.error.message}
        </p>
      )}
    </div>
  );
}

function LiveRun({ run, events }: { run: RunDetail; events: RunEvent[] }) {
  const phases = ["Validate", "Generate", "Mutate", "Improve", "Compare"];
  const phase = events.some((item) => item.type === "improved")
    ? 4
    : events.some((item) => item.type === "improving")
      ? 3
      : events.some((item) => item.type === "mutation")
        ? 2
        : events.some((item) => item.type === "generated")
          ? 1
          : 0;
  const latestMutation = run.report.stages
    .filter((stage) => stage.mutation)
    .at(-1)?.mutation;
  return (
    <>
      <Card className="stepper">
        {phases.map((item, index) => (
          <div
            className={`step ${index < phase ? "done" : index === phase ? "current" : ""}`}
            key={item}
          >
            <div className="step-line">
              <i />
            </div>
            <strong>{item}</strong>
            <small>
              {index === phase
                ? (run.live?.activity_status ?? "In progress")
                : index < phase
                  ? "Done"
                  : "Waiting"}
            </small>
          </div>
        ))}
      </Card>
      <div className="live-grid">
        <Card className="activity-card">
          <div className="card-bar row between">
            <h3>Activity</h3>
            <span className="muted small">
              {run.live?.activity ?? "Working…"}
            </span>
          </div>
          <div className="activity-list">
            {events.length ? (
              events.map((event) => (
                <div className={`event ${event.type}`} key={event.seq}>
                  <span className="mono muted">
                    {new Date(event.time).toLocaleTimeString(undefined, {
                      hour: "2-digit",
                      minute: "2-digit",
                      second: "2-digit",
                    })}
                  </span>
                  <span className="event-mark">●</span>
                  <span>{event.message}</span>
                </div>
              ))
            ) : (
              <div className="empty-inline">
                Waiting for the first activity…
              </div>
            )}
            {run.live?.activity && (
              <div className="event active">
                <span className="mono muted">now</span>
                <span className="event-mark">◌</span>
                <span>
                  {run.live.activity} · {run.live.activity_status ?? "working"}
                </span>
              </div>
            )}
          </div>
        </Card>
        <div className="live-rail">
          <Card className="result-card">
            <div className="card-bar row between">
              <h3>Results so far</h3>
              <span className="small muted">
                {latestMutation
                  ? `${latestMutation.mutants.length} mutants`
                  : "Awaiting mutation test"}
              </span>
            </div>
            {latestMutation ? (
              <div className="result-body">
                <div className="huge-score">
                  {percent(latestMutation.score)}
                </div>
                <div className="outcome-bar">
                  <span
                    style={{
                      width: `${latestMutation.mutants.length ? (latestMutation.killed / latestMutation.mutants.length) * 100 : 0}%`,
                    }}
                  />
                </div>
                <p className="small muted">
                  {latestMutation.killed} killed · {latestMutation.survived}{" "}
                  survived · {latestMutation.no_coverage} uncovered
                </p>
              </div>
            ) : (
              <div className="empty-inline">
                Mutation results will appear here.
              </div>
            )}
          </Card>
          <Card className="result-card">
            <div className="card-bar">
              <h3>Current activity</h3>
            </div>
            <div className="result-body">
              <p>{run.live?.activity ?? "Starting run…"}</p>
              <p className="muted small">
                Elapsed {duration(run.live?.elapsed_seconds)}
              </p>
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}

function Overview({
  run,
  setTab,
}: {
  run: RunDetail;
  setTab: (tab: Tab) => void;
}) {
  const stages = run.report.stages.filter((stage) => !stage.rejected);
  const measured = stages.filter((stage) => stage.mutation);
  const kept = stages.find((stage) => stage.label === run.report.kept_stage);
  const mutation = kept?.mutation;
  const artifacts = useQuery({
    queryKey: ["artifacts", run.summary.id],
    queryFn: () => api.artifacts(run.summary.id),
  }).data;
  const shown = artifacts
    ?.filter(
      (item) =>
        item.path === "report.json" ||
        ["tests/", "llm/", "stryker/", "vitest/"].some((prefix) =>
          item.path.startsWith(prefix),
        ),
    )
    .slice(0, 7);
  const density = useMemo(() => {
    const map = new Map<number, number>();
    for (const mutant of mutation?.mutants ?? [])
      map.set(mutant.start_line, (map.get(mutant.start_line) ?? 0) + 1);
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  }, [mutation]);
  const maxLine = density.at(-1)?.[0] ?? 1;
  return (
    <>
      <div className="overview-hero">
        <Card className="score-journey">
          <div>
            <p className="small muted">Mutation score · kept</p>
            <div className="display-score">
              {percent(run.summary.kept_score)}
            </div>
            <p className="small">
              <span
                className={
                  run.summary.improvement != null &&
                  run.summary.improvement >= 0
                    ? "success"
                    : "danger"
                }
              >
                {run.summary.improvement == null
                  ? ""
                  : `${run.summary.improvement >= 0 ? "+" : ""}${Math.round(run.summary.improvement)} pts`}
              </span>{" "}
              <span className="muted">vs first measured stage</span>
            </p>
          </div>
          <div className="journey-bars">
            <div className="target-rule">80% target</div>
            {measured.map((stage) => (
              <div className="journey-stage" key={stage.label}>
                <span>{stage.label}</span>
                <div className="journey-track">
                  <i
                    style={{ height: `${stage.mutation?.score ?? 0}%` }}
                    className={
                      stage.label === run.report.kept_stage ? "kept" : ""
                    }
                  />
                </div>
                <strong>{percent(stage.mutation?.score)}</strong>
              </div>
            ))}
          </div>
          <p className="muted small journey-caption">
            Detected ÷ valid mutants. Timeouts count as detected.
          </p>
        </Card>
        <Card className="outcomes">
          <h3>Mutant outcomes</h3>
          {[
            ["Killed", mutation?.killed],
            ["Survived", mutation?.survived],
            ["No coverage", mutation?.no_coverage],
            ["Timeout", mutation?.timeout],
            ["Compile / runtime error", mutation?.errors],
          ].map(([label, count]) => (
            <div className="row between outcome-row" key={label}>
              <span>{label}</span>
              <strong>{count ?? "—"}</strong>
            </div>
          ))}
          <p className="muted small">
            Only the selected source and test file were mutation tested.
          </p>
        </Card>
      </div>
      <Card className="density-card">
        <div className="section-heading">
          <div>
            <h3>Mutants along {fileName(run.summary.source_file)}</h3>
            <p>One bar per source line; height represents mutant count</p>
          </div>
          <button
            type="button"
            className="text-link"
            onClick={() => setTab("mutants")}
          >
            Explore mutants <ChevronRight size={14} />
          </button>
        </div>
        {density.length ? (
          <div className="density-chart">
            {density.map(([line, count]) => (
              <button
                type="button"
                key={line}
                title={`Line ${line}: ${count} mutants`}
                style={{
                  left: `${((line - 1) / maxLine) * 100}%`,
                  height: `${Math.max(8, Math.min(100, count * 14))}%`,
                }}
                onClick={() => setTab("mutants")}
              />
            ))}
          </div>
        ) : (
          <div className="empty-inline">No mutant data in this report.</div>
        )}
        <div className="row between tiny muted">
          <span>1</span>
          <span>{maxLine}</span>
        </div>
      </Card>
      <div className="overview-bottom">
        <section>
          <h2>Stages</h2>
          <Table>
            <thead>
              <tr>
                <th>Stage</th>
                <th className="right">Tests</th>
                <th className="right">Killed</th>
                <th className="right">Survived</th>
                <th className="right">No cov.</th>
                <th className="right">Score</th>
              </tr>
            </thead>
            <tbody>
              {stages.map((stage) => (
                <tr key={stage.label}>
                  <td>
                    {stage.label}{" "}
                    {stage.label === run.report.kept_stage && (
                      <span className="small kept-label">kept</span>
                    )}
                  </td>
                  <td className="right">{stage.test_count}</td>
                  <td className="right">{stage.mutation?.killed ?? "—"}</td>
                  <td className="right">{stage.mutation?.survived ?? "—"}</td>
                  <td className="right">
                    {stage.mutation?.no_coverage ?? "—"}
                  </td>
                  <td className="right success">
                    {percent(stage.mutation?.score)}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
          <p className="muted small stage-note">
            {run.report.model} · {run.report.usage.calls} calls ·{" "}
            {run.report.usage.input_tokens.toLocaleString()} input /{" "}
            {run.report.usage.output_tokens.toLocaleString()} output tokens
          </p>
        </section>
        <section id="artifacts">
          <h2>Artifacts</h2>
          <Card className="artifact-list">
            {shown?.map((item) => (
              <a
                key={item.path}
                href={`/api/runs/${encodeURIComponent(run.summary.id)}/artifacts/${item.path.split("/").map(encodeURIComponent).join("/")}`}
                target="_blank"
                rel="noreferrer"
              >
                <span className="mono">{item.path}</span>
                <span className="muted">
                  {(item.size / 1024).toFixed(1)} KB <Download size={13} />
                </span>
              </a>
            ))}
            {!shown?.length && (
              <p className="empty-inline">No artifacts found.</p>
            )}
          </Card>
        </section>
      </div>
    </>
  );
}

function Mutants({ run }: { run: RunDetail }) {
  const measured = run.report.stages
    .map((stage, index) => ({ stage, index }))
    .filter(({ stage }) => stage.mutation);
  const [stageIndex, setStageIndex] = useState(
    measured.find(({ stage }) => stage.label === run.report.kept_stage)
      ?.index ??
      measured.at(-1)?.index ??
      0,
  );
  const [filter, setFilter] = useState("all");
  const [operator, setOperator] = useState("all");
  const [line, setLine] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const result = useQuery({
    queryKey: ["mutants", run.summary.id, stageIndex],
    queryFn: () => api.mutants(run.summary.id, stageIndex, "all"),
    enabled: measured.length > 0,
  }).data;
  const source = useQuery({
    queryKey: ["source-file", run.summary.source_file],
    queryFn: () => api.file(run.summary.source_file),
  }).data;
  const mutants = result?.mutants ?? [];
  const operators = [...new Set(mutants.map((item) => item.mutator))].sort();
  const visible = mutants.filter(
    (item) =>
      (filter === "all" ||
        (filter === "killed"
          ? item.detected
          : filter === "survived"
            ? item.status === "Survived"
            : item.status === "NoCoverage")) &&
      (operator === "all" || item.mutator === operator) &&
      (!line || String(item.start_line).includes(line)),
  );
  const active = visible.find((item) => item.id === selected) ?? visible[0];
  const byLine = new Map<number, Mutant[]>();
  for (const item of visible)
    byLine.set(item.start_line, [...(byLine.get(item.start_line) ?? []), item]);
  return (
    <>
      <div className="mutant-filters">
        <div className="segmented">
          {[
            ["all", "All"],
            ["killed", "Killed"],
            ["survived", "Survived"],
            ["uncovered", "No coverage"],
          ].map(([key, label]) => (
            <button
              type="button"
              key={key}
              className={filter === key ? "selected" : ""}
              onClick={() => setFilter(key)}
            >
              {label}{" "}
              {key === "all"
                ? mutants.length
                : mutants.filter((item) =>
                    key === "killed"
                      ? item.detected
                      : key === "survived"
                        ? item.status === "Survived"
                        : item.status === "NoCoverage",
                  ).length}
            </button>
          ))}
        </div>
        <div className="row gap-sm">
          <select
            aria-label="Mutation stage"
            value={stageIndex}
            onChange={(event) => setStageIndex(Number(event.target.value))}
          >
            {measured.map(({ stage, index }) => (
              <option value={index} key={index}>
                {stage.label}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by operator"
            value={operator}
            onChange={(event) => setOperator(event.target.value)}
          >
            <option value="all">All operators</option>
            {operators.map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
          <input
            aria-label="Go to line"
            placeholder="Go to line"
            className="input line-search"
            inputMode="numeric"
            value={line}
            onChange={(event) => setLine(event.target.value.replace(/\D/g, ""))}
          />
        </div>
      </div>
      <div className="mutant-explorer">
        <Card className="source-code">
          <div className="card-bar row between">
            <span className="mono">{run.summary.source_file}</span>
            <span className="small muted">Markers: one square per mutant</span>
          </div>
          <div className="source-lines">
            {(source?.content ?? "").split("\n").map((text, index) => {
              const lineNo = index + 1;
              const row = byLine.get(lineNo) ?? [];
              return (
                <div
                  className={`source-line ${row.some((item) => item.id === active?.id) ? "selected" : ""}`}
                  key={lineNo}
                >
                  <span className="line-number">{lineNo}</span>
                  <span className="markers">
                    {row.slice(0, 5).map((item) => (
                      <button
                        type="button"
                        key={item.id}
                        title={`${item.mutator} · ${item.status}`}
                        className={item.detected ? "detected" : ""}
                        onClick={() => setSelected(item.id)}
                      />
                    ))}
                    {row.length > 5 && <small>+{row.length - 5}</small>}
                  </span>
                  <code>{text || " "}</code>
                </div>
              );
            })}
          </div>
        </Card>
        <Card className="mutant-detail">
          {active ? (
            <>
              <div className="detail-block row between">
                <h2>Mutant {active.id}</h2>
                <span className="badge">{active.status}</span>
              </div>
              <div className="detail-block">
                <p className="mono muted small">
                  {active.mutator} · line {active.start_line}, cols{" "}
                  {active.start_column}–{active.end_column}
                </p>
                <pre className="diff-code">
                  − {active.original_line ?? active.original}
                </pre>
                <pre className="diff-code">
                  + {active.mutated_line ?? active.replacement}
                </pre>
                <p>
                  {active.status === "Survived"
                    ? "Tests ran this code, but no assertion failed."
                    : active.status === "NoCoverage"
                      ? "No test covered this code."
                      : "A test detected this mutation."}
                </p>
              </div>
              <div className="detail-block">
                <h3>Covered by</h3>
                <p className="muted small">
                  {active.covered_by.length} tests · {active.killed_by.length}{" "}
                  failed
                </p>
                {active.covered_by.slice(0, 6).map((item) => (
                  <p key={item} className="small">
                    {item}
                  </p>
                ))}
              </div>
              <div className="detail-block">
                <h3>Mutation</h3>
                <p className="small">{active.summary}</p>
                {active.equivalent && (
                  <p className="muted small">
                    Flagged as likely equivalent by the model.
                  </p>
                )}
              </div>
            </>
          ) : (
            <div className="empty-state">No mutants match these filters.</div>
          )}
        </Card>
      </div>
    </>
  );
}

function Tests({ run }: { run: RunDetail }) {
  const stages = run.report.stages
    .map((stage, index) => ({ stage, index }))
    .filter(({ stage }) => stage.test_file_snapshot && !stage.rejected);
  const [index, setIndex] = useState(
    stages.find(({ stage }) => stage.label === run.report.kept_stage)?.index ??
      stages.at(-1)?.index ??
      0,
  );
  const [mode, setMode] = useState<"unified" | "split">("unified");
  const diff = useQuery({
    queryKey: ["stage-diff", run.summary.id, index],
    queryFn: () => api.stageDiff(run.summary.id, index),
    enabled: !!stages.length,
  }).data;
  const copy = () => {
    if (diff) navigator.clipboard.writeText(diff.after);
  };
  return (
    <>
      <div className="compare-bar">
        <span className="muted">Compare</span>
        <span className="mono">{diff?.before_label ?? "Original"}</span>
        <span>→</span>
        <select
          aria-label="Comparison stage"
          value={index}
          onChange={(event) => setIndex(Number(event.target.value))}
        >
          {stages.map(({ stage, index: option }) => (
            <option value={option} key={option}>
              {stage.label} · {stage.test_count} tests
            </option>
          ))}
        </select>
        <span className="push muted small">
          {diff?.added_tests.length ?? 0} tests added
        </span>
        <button type="button" className="text-link" onClick={copy}>
          <Copy size={14} /> Copy file
        </button>
      </div>
      <div className="tests-grid">
        <Card className="test-list">
          <div className="card-bar row between">
            <h3>Added tests</h3>
            <span className="muted small">{diff?.added_tests.length ?? 0}</span>
          </div>
          {diff?.added_tests.map((name) => (
            <div className="test-row" key={name}>
              <span>+</span>
              {name}
            </div>
          ))}
          {!diff?.added_tests.length && (
            <div className="empty-inline">
              No added test names in this stage.
            </div>
          )}
        </Card>
        <Card className="diff-view">
          <div className="card-bar row between">
            <span className="mono">{run.summary.test_file}</span>
            <div className="segmented">
              <button
                type="button"
                className={mode === "unified" ? "selected" : ""}
                onClick={() => setMode("unified")}
              >
                Unified
              </button>
              <button
                type="button"
                className={mode === "split" ? "selected" : ""}
                onClick={() => setMode("split")}
              >
                Split
              </button>
            </div>
          </div>
          {diff ? (
            mode === "split" ? (
              <MergeDiff before={diff.before} after={diff.after} />
            ) : (
              <pre className="unified-diff">
                {diff.unified.split("\n").map((line, i) => (
                  <span
                    key={i}
                    className={
                      line.startsWith("+") && !line.startsWith("+++")
                        ? "added"
                        : line.startsWith("-") && !line.startsWith("---")
                          ? "removed"
                          : ""
                    }
                  >
                    {line}
                    {"\n"}
                  </span>
                ))}
              </pre>
            )
          ) : (
            <div className="empty-inline">Loading diff…</div>
          )}
        </Card>
      </div>
    </>
  );
}

function MergeDiff({ before, after }: { before: string; after: string }) {
  const [element, setElement] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!element) return;
    let disposed = false;
    let view: { destroy: () => void } | undefined;
    import("@codemirror/merge").then(({ MergeView }) => {
      if (!disposed)
        view = new MergeView({
          a: { doc: before },
          b: { doc: after },
          parent: element,
        });
    });
    return () => {
      disposed = true;
      view?.destroy();
    };
  }, [element, before, after]);
  return (
    <section
      ref={setElement}
      className="merge-view"
      aria-label="Side by side test diff"
    />
  );
}

function ModelLog({ run }: { run: RunDetail }) {
  const calls =
    useQuery({
      queryKey: ["llm", run.summary.id],
      queryFn: () => api.llm(run.summary.id),
    }).data ?? [];
  const [selected, setSelected] = useState<string | null>(null);
  const current = calls.find((item) => item.name === selected) ?? calls[0];
  const detail = useQuery({
    queryKey: ["llm-call", run.summary.id, current?.name],
    queryFn: () => api.llmCall(run.summary.id, current?.name),
    enabled: !!current,
  }).data;
  const usage = run.report.usage;
  return (
    <>
      <Card className="usage-card">
        <div className="usage-metrics">
          <div>
            <strong>{usage.calls}</strong>
            <span>Model calls</span>
          </div>
          <div>
            <strong>
              {(usage.input_tokens + usage.output_tokens).toLocaleString()}
            </strong>
            <span>Tokens</span>
          </div>
          <div>
            <strong>
              {duration(
                run.report.stages.reduce(
                  (sum, item) => sum + item.llm_seconds,
                  0,
                ),
              )}
            </strong>
            <span>Model time</span>
          </div>
        </div>
        <div className="usage-breakdown">
          <div className="outcome-bar">
            <span
              style={{
                width: `${usage.input_tokens + usage.output_tokens ? (usage.input_tokens / (usage.input_tokens + usage.output_tokens)) * 100 : 0}%`,
              }}
            />
          </div>
          <div className="row between small muted">
            <span>Input {usage.input_tokens.toLocaleString()}</span>
            <span>Output {usage.output_tokens.toLocaleString()}</span>
          </div>
        </div>
      </Card>
      <div className="call-list">
        {calls.map((item, index) => (
          <button
            type="button"
            key={item.name}
            className={`call-item ${current?.name === item.name ? "selected" : ""}`}
            onClick={() => setSelected(item.name)}
          >
            <span className="call-index">{index + 1}</span>
            <span>
              <strong>
                {item.kind} · attempt {item.attempt}
              </strong>
              <small className="muted">
                +{item.new_tests.length} tests ·{" "}
                {item.input_tokens + item.output_tokens} tokens
              </small>
            </span>
            <span className="muted call-summary">{item.summary}</span>
            {current?.name === item.name ? (
              <ChevronDown size={16} />
            ) : (
              <ChevronRight size={16} />
            )}
          </button>
        ))}
        {!calls.length && (
          <div className="empty-state">No saved model calls for this run.</div>
        )}
      </div>
      {detail && (
        <Card className="call-detail">
          <section>
            <div className="row between">
              <h3>Prompt</h3>
              <button
                type="button"
                className="text-link"
                onClick={() => navigator.clipboard.writeText(detail.prompt)}
              >
                <Copy size={14} /> Copy
              </button>
            </div>
            <pre>{detail.prompt}</pre>
          </section>
          <section>
            <h3>Response summary</h3>
            <p>{detail.summary || "No summary supplied."}</p>
            <h3>New tests</h3>
            {detail.new_tests.length ? (
              <ul>
                {detail.new_tests.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <p className="muted">No new test names.</p>
            )}
            <h3>Equivalent mutants</h3>
            <p>{detail.equivalent_mutants.join(", ") || "None flagged"}</p>
            <h3>Returned test file</h3>
            <pre>{detail.test_file}</pre>
          </section>
        </Card>
      )}
    </>
  );
}
