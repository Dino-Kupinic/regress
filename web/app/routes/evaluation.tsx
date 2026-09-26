import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useLoaderData } from "react-router";
import { Button } from "~/components/ui/button";
import { Card } from "~/components/ui/card";
import { Table } from "~/components/ui/table";
import { api } from "~/lib/api";
import { dateTime, percent } from "~/lib/utils";

export async function clientLoader() {
  const [evaluations, suites, job] = await Promise.all([
    api.evaluations(),
    api.evaluationSuites(),
    api.evaluationJob(),
  ]);
  return { evaluations, suites, job };
}

export default function Evaluation() {
  const initial = useLoaderData<typeof clientLoader>();
  const client = useQueryClient();
  const evaluations = useQuery({
    queryKey: ["evaluations"],
    queryFn: api.evaluations,
    initialData: initial.evaluations,
    refetchInterval: 4_000,
  }).data;
  const suites = useQuery({
    queryKey: ["evaluation-suites"],
    queryFn: api.evaluationSuites,
    initialData: initial.suites,
  }).data;
  const job = useQuery({
    queryKey: ["evaluation-job"],
    queryFn: api.evaluationJob,
    initialData: initial.job,
    refetchInterval: 2_000,
  }).data;
  const [oracleOnly, setOracleOnly] = useState(true);
  const [selected, setSelected] = useState(suites[0]?.name ?? "");
  const run = useMutation({
    mutationFn: () => api.startEvaluation(oracleOnly ? "oracle" : "full"),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["evaluation-job"] });
      client.invalidateQueries({ queryKey: ["evaluations"] });
    },
  });
  const latest = evaluations[0];
  const suite = suites.find((item) => item.name === selected) ?? suites[0];
  const moduleResult = latest?.modules.find(
    (item) => item.module === suite?.name,
  );
  const labels = ["Existing tests", "One-shot AI", "Regress", "Oracle"];
  // The API sums each column over the same modules, so totals are not recomputed here.
  const summary = (label: string) => {
    const total = latest?.totals.find((item) => item.label === label);
    return {
      score: total?.score ?? null,
      caught: total?.caught ?? 0,
      total: total?.bugs_total ?? 0,
    };
  };
  const partial =
    latest && latest.counted_modules.length < latest.modules.length;
  return (
    <div className="page evaluation-page">
      <div className="page-header">
        <div>
          <h1>Evaluation</h1>
          <p>
            Hidden-bug benchmark · {suites.length} modules ·{" "}
            {suites.reduce((sum, item) => sum + item.bugs.length, 0)} seeded
            regressions
          </p>
        </div>
        <div className="row gap-sm">
          <label className="oracle-toggle">
            <input
              type="checkbox"
              checked={oracleOnly}
              onChange={(event) => setOracleOnly(event.target.checked)}
            />{" "}
            Oracle only
          </label>
          <Button
            disabled={
              !suites.length || job.status === "running" || run.isPending
            }
            onClick={() => run.mutate()}
          >
            {job.status === "running" ? "Running…" : "Run evaluation"}
          </Button>
        </div>
      </div>
      {run.isError && (
        <p className="error-text" role="alert">
          {run.error.message}
        </p>
      )}
      {job.status === "running" && (
        <Card className="eval-notice">
          <strong>Evaluation running in the background</strong>
          <p className="small muted">
            Started {job.started_at ? dateTime(job.started_at) : "now"}. Results
            refresh automatically.
          </p>
          <pre>{job.output || "Waiting for output…"}</pre>
        </Card>
      )}
      {job.status === "failed" && (
        <Card className="eval-notice">
          <strong className="danger">Evaluation failed</strong>
          <pre>{job.output}</pre>
        </Card>
      )}
      <div className="eval-summary">
        {labels.map((label) => {
          const data = summary(label);
          return (
            <Card key={label}>
              <span className="muted small">{label}</span>
              <div className="row gap-sm">
                <strong
                  className={
                    data.score != null && data.score < 60 ? "danger" : "success"
                  }
                >
                  {percent(data.score)}
                </strong>
                <span className="small muted">
                  {data.total
                    ? `${data.caught}/${data.total} bugs caught`
                    : "Not in this evaluation"}
                </span>
              </div>
              <div className="meter">
                <span style={{ width: `${data.score ?? 0}%` }} />
              </div>
            </Card>
          );
        })}
      </div>
      {partial && (
        <p className="small muted">
          Totals cover {latest.counted_modules.length} of{" "}
          {latest.modules.length} modules: only modules with a result in every
          column count, so each column adds up the same modules.
        </p>
      )}
      {!latest ? (
        <Card className="empty-state">
          <h3>No evaluation results yet</h3>
          <p>
            Run the oracle check to validate the hidden bugs, or run a full
            model comparison.
          </p>
          <p className="mono small">regress eval examples --oracle</p>
        </Card>
      ) : (
        <>
          <div className="eval-notice">
            Last evaluation: {latest.mode} · {dateTime(latest.created_at)} ·{" "}
            {latest.output_dir ?? "saved in .regress/eval"}
          </div>
          <div className="eval-layout">
            <div>
              <Card>
                <div className="card-bar">
                  <h3>Mutation score and hidden bugs caught</h3>
                  <p className="small muted">
                    A bug counts as caught when any test fails with it applied.
                  </p>
                </div>
                <Table>
                  <thead>
                    <tr>
                      <th>Module</th>
                      {labels.map((label) => (
                        <th key={label} className="right">
                          {label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {latest.modules.map((item) => (
                      <tr
                        key={item.module}
                        className={
                          suite?.name === item.module ? "selected-row" : ""
                        }
                        onClick={() => setSelected(item.module)}
                      >
                        <td>
                          <button
                            type="button"
                            className="text-link mono"
                            onClick={() => setSelected(item.module)}
                          >
                            {item.module}
                          </button>
                          {item.error && (
                            <p className="small danger">{item.error}</p>
                          )}
                          {item.note && (
                            <p className="small muted">{item.note}</p>
                          )}
                        </td>
                        {labels.map((label) => {
                          const stage = item.stages.find((entry) =>
                            entry.label
                              .toLowerCase()
                              .includes(label.toLowerCase()),
                          );
                          return (
                            <td className="right" key={label}>
                              {stage ? (
                                <>
                                  <span
                                    className={
                                      stage.score != null && stage.score < 60
                                        ? "danger"
                                        : "success"
                                    }
                                  >
                                    {percent(stage.score)}
                                  </span>{" "}
                                  <small className="muted">
                                    {stage.caught.length}/
                                    {stage.caught.length + stage.missed.length}
                                  </small>
                                </>
                              ) : (
                                "—"
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </Card>
              <Card className="past-evals">
                <div className="card-bar">
                  <h3>Past evaluations</h3>
                </div>
                {evaluations.map((item) => (
                  <div key={item.output_dir} className="row between">
                    <span className="mono small">
                      {dateTime(item.created_at)}
                    </span>
                    <span>{item.mode}</span>
                    <span className="muted small">
                      {item.modules.length} modules
                    </span>
                  </div>
                ))}
              </Card>
            </div>
            <Card className="bug-detail">
              <div className="card-bar row between">
                <div>
                  <h3 className="mono">{suite?.name ?? "Select a module"}</h3>
                  <p className="small muted">
                    {suite?.bugs.length ?? 0} hidden bugs
                  </p>
                </div>
              </div>
              {suite?.bugs.map((bug) => (
                <div key={bug.id} className="bug-item">
                  <strong className="mono">{bug.id}</strong>
                  <p>{bug.description}</p>
                  <div className="mono bug-diff">
                    <div>− {bug.find}</div>
                    <div>+ {bug.replace}</div>
                  </div>
                  <small className="muted">
                    {moduleResult?.stages
                      .map(
                        (stage) =>
                          `${stage.label}: ${stage.caught.includes(bug.id) ? "caught" : stage.missed.includes(bug.id) ? "missed" : "—"}`,
                      )
                      .join(" · ")}
                  </small>
                </div>
              ))}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
