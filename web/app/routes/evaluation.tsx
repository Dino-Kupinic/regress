import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { useState } from "react";
import { type MetaFunction, useLoaderData } from "react-router";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { Page, PageHeader } from "~/components/page";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import {
  type ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "~/components/ui/chart";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "~/components/ui/empty";
import { Field, FieldLabel } from "~/components/ui/field";
import { Progress } from "~/components/ui/progress";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { api, type EvalResult } from "~/lib/api";
import { dateTime, pageTitle, percent } from "~/lib/utils";

export const meta: MetaFunction = () => [{ title: pageTitle("Evaluation") }];

export async function clientLoader() {
  const [evaluations, suites, job] = await Promise.all([
    api.evaluations(),
    api.evaluationSuites(),
    api.evaluationJob(),
  ]);
  return { evaluations, suites, job };
}

const approaches = [
  { key: "existing", label: "Existing tests", color: "var(--chart-4)" },
  { key: "oneshot", label: "One-shot AI", color: "var(--chart-3)" },
  { key: "regress", label: "Regress", color: "var(--chart-1)" },
  { key: "oracle", label: "Oracle", color: "var(--chart-2)" },
] as const;

const chartConfig = Object.fromEntries(
  approaches.map((item) => [
    item.key,
    { label: item.label, color: item.color },
  ]),
) satisfies ChartConfig;

const stageFor = (module: EvalResult["modules"][number], label: string) =>
  module.stages.find((entry) =>
    entry.label.toLowerCase().includes(label.toLowerCase()),
  );

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
  const running = job.status === "running";
  const chartData =
    latest?.modules.map((item) => ({
      module: item.module,
      ...Object.fromEntries(
        approaches.map(({ key, label }) => {
          const stage = stageFor(item, label);
          const bugs = stage ? stage.caught.length + stage.missed.length : 0;
          return [
            key,
            stage && bugs ? Math.round((stage.caught.length / bugs) * 100) : 0,
          ];
        }),
      ),
    })) ?? [];
  return (
    <Page>
      <PageHeader
        title="Evaluation"
        description={`Hidden-bug benchmark · ${suites.length} modules · ${suites.reduce((sum, item) => sum + item.bugs.length, 0)} seeded regressions`}
        actions={
          <>
            <Field orientation="horizontal" className="w-auto">
              <Switch
                id="oracle-only"
                checked={oracleOnly}
                onCheckedChange={setOracleOnly}
              />
              <FieldLabel htmlFor="oracle-only" className="font-normal">
                Oracle only
              </FieldLabel>
            </Field>
            <Button
              disabled={!suites.length || running || run.isPending}
              onClick={() => run.mutate()}
            >
              {(running || run.isPending) && (
                <Spinner data-icon="inline-start" />
              )}
              {running ? "Running" : "Run evaluation"}
            </Button>
          </>
        }
      />
      {run.isError && (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>Could not start the evaluation</AlertTitle>
          <AlertDescription>{run.error.message}</AlertDescription>
        </Alert>
      )}
      {running && (
        <Alert className="motion-safe:animate-in fade-in-0 slide-in-from-top-1">
          <Spinner />
          <AlertTitle className="shimmer">
            Evaluation running in the background
          </AlertTitle>
          <AlertDescription className="flex flex-col gap-3">
            <p>
              Started {job.started_at ? dateTime(job.started_at) : "now"}.
              Results refresh automatically.
            </p>
            <pre className="max-h-48 w-full overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap scroll-fade">
              {job.output || "Waiting for output…"}
            </pre>
          </AlertDescription>
        </Alert>
      )}
      {job.status === "failed" && (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>Evaluation failed</AlertTitle>
          <AlertDescription>
            <pre className="max-h-48 w-full overflow-auto font-mono text-xs whitespace-pre-wrap scroll-fade">
              {job.output}
            </pre>
          </AlertDescription>
        </Alert>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {approaches.map(({ key, label }) => {
          const data = summary(label);
          return (
            <Card key={key}>
              <CardHeader>
                <CardDescription>{label}</CardDescription>
                <p className="text-display font-medium tabular-nums">
                  {percent(data.score)}
                  <span className="ml-1.5 text-sm font-normal tracking-normal text-muted-foreground">
                    mutation score
                  </span>
                </p>
              </CardHeader>
              <CardContent>
                <Progress
                  value={data.total ? (data.caught / data.total) * 100 : 0}
                  aria-label={`${label}: hidden bugs caught`}
                />
              </CardContent>
              <CardFooter className="text-caption text-muted-foreground">
                {data.total
                  ? `${data.caught} of ${data.total} hidden bugs caught`
                  : "Not in this evaluation"}
              </CardFooter>
            </Card>
          );
        })}
      </div>
      {partial && (
        <p className="-mt-6 text-caption text-muted-foreground">
          Totals cover {latest.counted_modules.length} of{" "}
          {latest.modules.length} modules: only modules with a result in every
          column count, so each column adds up the same modules.
        </p>
      )}
      {!latest ? (
        <Card>
          <Empty className="p-12">
            <EmptyHeader>
              <EmptyTitle>No evaluation results yet</EmptyTitle>
              <EmptyDescription>
                Run the oracle check to validate the hidden bugs, or run a full
                model comparison.
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <code className="rounded-full bg-muted px-4 py-1.5 font-mono text-xs">
                regress eval examples --oracle
              </code>
            </EmptyContent>
          </Empty>
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Hidden bugs caught by module</CardTitle>
              <CardDescription>
                {latest.mode} evaluation · {dateTime(latest.created_at)} ·{" "}
                <span className="font-mono">
                  {latest.output_dir ?? ".regress/eval"}
                </span>
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ChartContainer
                config={chartConfig}
                className="aspect-auto h-[260px] w-full"
              >
                <BarChart data={chartData} margin={{ left: 0, right: 0 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis
                    dataKey="module"
                    tickLine={false}
                    axisLine={false}
                    tickMargin={8}
                    tick={{ fontFamily: "var(--font-mono)" }}
                  />
                  <YAxis
                    domain={[0, 100]}
                    ticks={[0, 50, 100]}
                    tickLine={false}
                    axisLine={false}
                    width={40}
                    tickFormatter={(value) => `${value}%`}
                  />
                  <ChartTooltip
                    cursor={{ fill: "var(--accent)" }}
                    content={
                      <ChartTooltipContent
                        formatter={(value, name) => (
                          <span className="flex w-full items-center justify-between gap-4">
                            <span className="flex items-center gap-2 text-muted-foreground">
                              <span
                                className="size-2 rounded-[2px]"
                                style={{
                                  background: `var(--color-${String(name)})`,
                                }}
                              />
                              {
                                chartConfig[name as keyof typeof chartConfig]
                                  ?.label
                              }
                            </span>
                            <span className="font-mono font-medium tabular-nums">
                              {value}%
                            </span>
                          </span>
                        )}
                      />
                    }
                  />
                  <ChartLegend content={<ChartLegendContent />} />
                  {approaches.map(({ key }) => (
                    <Bar
                      key={key}
                      dataKey={key}
                      fill={`var(--color-${key})`}
                      radius={[3, 3, 0, 0]}
                      maxBarSize={18}
                    />
                  ))}
                </BarChart>
              </ChartContainer>
            </CardContent>
          </Card>
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.8fr)_minmax(300px,1fr)]">
            <div className="flex min-w-0 flex-col gap-4">
              <Card className="gap-0 py-0">
                <CardHeader className="border-b py-4">
                  <CardTitle>Results by module</CardTitle>
                  <CardDescription>
                    Hidden-bug catch rate. A bug counts as caught when any test
                    fails with it applied.
                  </CardDescription>
                </CardHeader>
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>Module</TableHead>
                      {approaches.map(({ key, label }) => (
                        <TableHead key={key} className="text-right">
                          {label}
                        </TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {latest.modules.map((item) => (
                      <TableRow
                        key={item.module}
                        data-state={
                          suite?.name === item.module ? "selected" : undefined
                        }
                        className="cursor-pointer"
                        onClick={() => setSelected(item.module)}
                      >
                        <TableCell className="whitespace-normal">
                          <button
                            type="button"
                            className="font-mono font-medium underline-offset-4 hover:underline"
                            onClick={() => setSelected(item.module)}
                          >
                            {item.module}
                          </button>
                          {item.error && (
                            <p className="text-caption text-destructive">
                              {item.error}
                            </p>
                          )}
                          {item.note && (
                            <p className="text-caption text-muted-foreground">
                              {item.note}
                            </p>
                          )}
                        </TableCell>
                        {approaches.map(({ key, label }) => {
                          const stage = stageFor(item, label);
                          return (
                            <TableCell
                              className="text-right tabular-nums"
                              key={key}
                            >
                              {stage ? (
                                <span className="flex flex-col items-end">
                                  <span className="font-medium">
                                    {percent(stage.score)}
                                  </span>
                                  <span className="text-xs text-muted-foreground">
                                    {stage.caught.length}/
                                    {stage.caught.length + stage.missed.length}{" "}
                                    bugs
                                  </span>
                                </span>
                              ) : (
                                <span className="text-muted-foreground">—</span>
                              )}
                            </TableCell>
                          );
                        })}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Card>
              <Card className="gap-0 py-0">
                <CardHeader className="border-b py-4">
                  <CardTitle>Past evaluations</CardTitle>
                  <CardAction className="text-caption text-muted-foreground">
                    {evaluations.length}
                  </CardAction>
                </CardHeader>
                <ul className="max-h-72 divide-y overflow-y-auto scroll-fade">
                  {evaluations.map((item, index) => (
                    <li
                      key={item.output_dir ?? index}
                      className="grid grid-cols-[1fr_auto_auto] items-center gap-4 px-6 py-3 text-sm"
                    >
                      <span className="font-mono text-xs">
                        {dateTime(item.created_at)}
                      </span>
                      <Badge variant={index === 0 ? "default" : "secondary"}>
                        {item.mode}
                      </Badge>
                      <span className="text-caption text-muted-foreground tabular-nums">
                        {item.modules.length} modules
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
            <Card className="gap-0 py-0 lg:sticky lg:top-4">
              <CardHeader className="border-b py-4">
                <CardTitle className="font-mono">
                  {suite?.name ?? "Select a module"}
                </CardTitle>
                <CardDescription>
                  {suite?.bugs.length ?? 0} hidden bugs
                </CardDescription>
              </CardHeader>
              <ul
                key={suite?.name}
                className="max-h-[720px] divide-y overflow-y-auto scroll-fade motion-safe:animate-in fade-in-0"
              >
                {suite?.bugs.map((bug) => (
                  <li key={bug.id} className="flex flex-col gap-2.5 px-6 py-4">
                    <span className="font-mono text-xs font-medium">
                      {bug.id}
                    </span>
                    <p className="text-sm leading-relaxed">{bug.description}</p>
                    <div className="flex flex-col gap-1 font-mono text-xs">
                      <code className="rounded-md bg-destructive/5 px-2.5 py-1.5 break-all text-muted-foreground">
                        − {bug.find}
                      </code>
                      <code className="rounded-md bg-success/5 px-2.5 py-1.5 break-all">
                        + {bug.replace}
                      </code>
                    </div>
                    {moduleResult && (
                      <div className="flex flex-wrap gap-1.5">
                        {moduleResult.stages.map((stage) => {
                          const caught = stage.caught.includes(bug.id);
                          const missed = stage.missed.includes(bug.id);
                          return (
                            <Badge
                              key={stage.label}
                              variant={
                                caught
                                  ? "default"
                                  : missed
                                    ? "outline"
                                    : "secondary"
                              }
                            >
                              {stage.label}:{" "}
                              {caught ? "caught" : missed ? "missed" : "—"}
                            </Badge>
                          );
                        })}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        </>
      )}
    </Page>
  );
}
