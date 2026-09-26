import { useQuery } from "@tanstack/react-query";
import { FlaskConical, SquareTerminal, TriangleAlert } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { Link, useLoaderData } from "react-router";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Label,
  Pie,
  PieChart,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts";
import { useNewRun } from "~/components/new-run";
import { Page, PageHeader, SectionHeader } from "~/components/page";
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "~/components/ui/alert";
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
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "~/components/ui/item";
import { Progress } from "~/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "~/components/ui/toggle-group";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "~/components/ui/tooltip";
import { api, type Mutation, type RunSummary } from "~/lib/api";
import { cn, dateTime, fileName, percent, signedPoints } from "~/lib/utils";

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

const TARGET = 80;

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
    .filter((run): run is RunSummary => !!run);
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
    (run) => (run.kept_score ?? 0) >= TARGET,
  ).length;
  const latest = runs.find(
    (run) => run.status === "completed" && run.kept_score != null,
  );
  const latestDetail = useQuery({
    queryKey: ["run", latest?.id],
    queryFn: () => api.run(latest?.id ?? ""),
    enabled: !!latest?.id,
  }).data;
  const detailFor = (path: string) =>
    initial.details[sources.findIndex((item) => item.path === path)];
  const chosenDetail = detailFor(source);
  const withTests = initial.details.filter(
    (item) => item?.test_file_exists,
  ).length;
  const rows = sources.filter((item) => {
    const run = latestByFile.get(item.path);
    if (filter === "never") return !run;
    if (filter === "low") return !run || (run.kept_score ?? 0) < 60;
    return true;
  });
  const keptStages = latestDetail?.report.stages.filter(
    (stage) => stage.mutation && !stage.rejected,
  );
  const mutation = latestDetail?.report.stages.find(
    (stage) => stage.label === latestDetail.report.kept_stage,
  )?.mutation;
  return (
    <Page>
      <PageHeader
        title="Dashboard"
        description={
          <>
            {sources.length} source files · {withTests} with tests ·{" "}
            {project.toolchain
              ? `Vitest ${project.toolchain.vitest} · Stryker ${project.toolchain.stryker} · ${project.toolchain.runner}`
              : "Toolchain needs setup"}
          </>
        }
        actions={
          <span className="text-caption text-muted-foreground">
            {runs[0]
              ? `Last run ${dateTime(runs[0].created_at)}`
              : "No runs yet"}
          </span>
        }
      />
      {!project.ready && (
        <Alert>
          <TriangleAlert />
          <AlertTitle>Project setup is incomplete</AlertTitle>
          <AlertDescription>
            {project.problems[0] ?? "Finish setup before starting a run."}
          </AlertDescription>
          <AlertAction>
            <Button variant="outline" size="sm" asChild>
              <Link to="/setup">Set up project</Link>
            </Button>
          </AlertAction>
        </Alert>
      )}
      <div className="flex flex-col gap-2 rounded-3xl border p-2 sm:flex-row sm:items-center sm:rounded-full sm:pl-5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <SquareTerminal className="size-4 shrink-0 text-muted-foreground" />
          <Select value={source} onValueChange={setSource}>
            <SelectTrigger
              variant="ghost"
              aria-label="Source file to run"
              className="min-w-0 font-mono"
            >
              <SelectValue placeholder="Choose a source file" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {sources.map((item) => (
                  <SelectItem
                    key={item.path}
                    value={item.path}
                    className="font-mono"
                  >
                    {item.path}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <span className="hidden truncate text-caption text-muted-foreground md:inline">
            {chosenDetail
              ? `${chosenDetail.test_file} will be ${chosenDetail.test_file_exists ? "extended" : "created"}`
              : "Choose a source file"}
          </span>
        </div>
        <div className="flex items-center justify-end gap-2">
          <Badge variant="secondary" className="hidden font-mono sm:flex">
            {settings.effective.model}
          </Badge>
          <Button
            onClick={() => open(source)}
            disabled={!source || !project.ready || !!project.active_run}
          >
            Run regress
          </Button>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Project mutation score"
          value={percent(average)}
          footer={`${completed.length} of ${sources.length} files measured · target ${TARGET}%`}
        >
          <TargetProgress value={average ?? 0} />
        </StatCard>
        <StatCard
          label={`Files at ${TARGET}% or more`}
          value={atTarget}
          unit={`of ${sources.length}`}
          footer="Based on latest completed runs"
        >
          <div className="flex flex-wrap gap-1">
            {sources.map((item) => {
              const score = latestCompletedByFile.get(item.path)?.kept_score;
              return (
                <Tooltip key={item.path}>
                  <TooltipTrigger asChild>
                    <span
                      className={cn(
                        "size-4 rounded-[3px] border transition-colors",
                        (score ?? 0) >= TARGET
                          ? "border-foreground bg-foreground"
                          : "bg-muted",
                      )}
                    />
                  </TooltipTrigger>
                  <TooltipContent>
                    <span className="font-mono">{fileName(item.path)}</span>{" "}
                    {percent(score)}
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </div>
        </StatCard>
        <StatCard
          label="Tests added by Regress"
          value={added}
          footer="From latest completed runs"
        >
          <AddedTestsChart runs={runs} />
        </StatCard>
        <StatCard
          label="Runs"
          value={runs.length}
          footer={`${runs.filter((run) => run.active).length} active · ${runs.filter((run) => run.status === "completed").length} completed · ${runs.filter((run) => run.status === "failed").length} failed`}
        >
          <RunMix runs={runs} />
        </StatCard>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <ScoreHistory runs={runs} className="lg:col-span-2" />
        <Card>
          <CardHeader>
            <CardTitle>Latest completed run</CardTitle>
            <CardDescription className="font-mono">
              {latest ? fileName(latest.source_file) : "No completed runs"}
            </CardDescription>
            {latest && (
              <CardAction>
                <Button variant="outline" size="sm" asChild>
                  <Link to={`/runs/${encodeURIComponent(latest.id)}`}>
                    Report
                  </Link>
                </Button>
              </CardAction>
            )}
          </CardHeader>
          <CardContent className="flex flex-1 flex-col gap-5">
            {latest ? (
              <>
                <OutcomeDonut mutation={mutation} score={latest.kept_score} />
                <div className="flex flex-col gap-2.5">
                  {keptStages?.map((stage) => (
                    <div
                      key={stage.label}
                      className="grid grid-cols-[1fr_7rem_2.5rem] items-center gap-3 text-caption"
                    >
                      <span className="truncate text-muted-foreground">
                        {stage.label}
                      </span>
                      <Progress value={stage.mutation?.score ?? 0} />
                      <span className="text-right font-medium tabular-nums">
                        {percent(stage.mutation?.score)}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <Empty className="p-6">
                <EmptyHeader>
                  <EmptyTitle>Run your first source file</EmptyTitle>
                  <EmptyDescription>
                    Your latest report will appear here.
                  </EmptyDescription>
                </EmptyHeader>
                <EmptyContent>
                  <Button onClick={() => open()}>New run</Button>
                </EmptyContent>
              </Empty>
            )}
          </CardContent>
          {latest?.improvement != null && (
            <CardFooter className="text-caption text-muted-foreground">
              {signedPoints(latest.improvement)} against the first measured
              stage
            </CardFooter>
          )}
        </Card>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Mutation score by file</CardTitle>
            <CardDescription>Latest kept tests for each source</CardDescription>
            <CardAction>
              <Badge variant="outline">Target {TARGET}%</Badge>
            </CardAction>
          </CardHeader>
          <CardContent>
            <ScoreByFile
              files={sources.map((item) => {
                const run = latestByFile.get(item.path);
                return {
                  file: fileName(item.path),
                  score: run?.kept_score ?? 0,
                  label: run?.active ? "Running" : percent(run?.kept_score),
                };
              })}
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Mutants by operator</CardTitle>
            <CardDescription>
              {latest
                ? `${fileName(latest.source_file)} · ${mutation?.mutants.length ?? 0} mutants`
                : "Run a source file to see operator coverage"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <OperatorChart mutation={mutation} />
          </CardContent>
        </Card>
      </div>
      <Item variant="outline">
        <ItemMedia variant="icon">
          <FlaskConical />
        </ItemMedia>
        <ItemContent>
          <ItemTitle>Hidden-bug evaluation</ItemTitle>
          <ItemDescription>
            Mutation scores are one signal. Check whether better tests also
            catch seeded regressions.
          </ItemDescription>
        </ItemContent>
        <ItemActions>
          <Button variant="outline" size="sm" asChild>
            <Link to="/evaluation">Open evaluation</Link>
          </Button>
        </ItemActions>
      </Item>
      <section className="flex flex-col gap-4">
        <SectionHeader
          title="Source files"
          description="Inspect a file's latest run or start a new one."
          action={
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              value={filter}
              onValueChange={(value) =>
                value && setFilter(value as typeof filter)
              }
            >
              <ToggleGroupItem value="all">
                All {sources.length}
              </ToggleGroupItem>
              <ToggleGroupItem value="never">Never run</ToggleGroupItem>
              <ToggleGroupItem value="low">Below 60%</ToggleGroupItem>
            </ToggleGroup>
          }
        />
        <Card className="py-0">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>File</TableHead>
                <TableHead className="text-right">Lines</TableHead>
                <TableHead>Test file</TableHead>
                <TableHead className="text-right">Tests</TableHead>
                <TableHead className="w-44">Kept score</TableHead>
                <TableHead>Last run</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((item) => {
                const detail = detailFor(item.path);
                const run = latestByFile.get(item.path);
                return (
                  <TableRow key={item.path}>
                    <TableCell className="font-mono">{item.path}</TableCell>
                    <TableCell className="text-right text-muted-foreground tabular-nums">
                      {detail?.lines ?? "—"}
                    </TableCell>
                    <TableCell className="font-mono text-muted-foreground">
                      {detail?.test_file ?? "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {run?.tests_after ?? run?.tests_before ?? "—"}
                    </TableCell>
                    <TableCell>
                      {run?.active ? (
                        <span className="shimmer text-muted-foreground">
                          Running
                        </span>
                      ) : (
                        <div className="flex items-center gap-3">
                          <Progress
                            value={run?.kept_score ?? 0}
                            className="w-20"
                          />
                          <span className="tabular-nums">
                            {percent(run?.kept_score)}
                          </span>
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {run ? dateTime(run.created_at) : "Never"}
                    </TableCell>
                    <TableCell className="text-right">
                      {run ? (
                        <Button variant="ghost" size="sm" asChild>
                          <Link to={`/runs/${encodeURIComponent(run.id)}`}>
                            Open
                          </Link>
                        </Button>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => open(item.path)}
                        >
                          Run
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          {!rows.length && (
            <Empty className="border-t p-8">
              <EmptyHeader>
                <EmptyTitle>No source files match this filter</EmptyTitle>
              </EmptyHeader>
            </Empty>
          )}
        </Card>
      </section>
    </Page>
  );
}

function StatCard({
  label,
  value,
  unit,
  footer,
  children,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  footer: string;
  children: ReactNode;
}) {
  return (
    <Card className="gap-4">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <p className="text-display font-medium tabular-nums">
          {value}
          {unit && (
            <span className="ml-1.5 text-sm font-normal tracking-normal text-muted-foreground">
              {unit}
            </span>
          )}
        </p>
      </CardHeader>
      <CardContent className="mt-auto">{children}</CardContent>
      <CardFooter className="text-caption text-muted-foreground">
        {footer}
      </CardFooter>
    </Card>
  );
}

function TargetProgress({ value }: { value: number }) {
  return (
    <div className="relative py-1">
      <Progress value={value} aria-label="Project mutation score" />
      <span
        aria-hidden="true"
        className="absolute inset-y-0 w-px bg-foreground"
        style={{ left: `${TARGET}%` }}
      />
    </div>
  );
}

function AddedTestsChart({ runs }: { runs: RunSummary[] }) {
  const data = runs
    .filter((run) => run.status === "completed" && run.tests_after != null)
    .slice(0, 16)
    .reverse()
    .map((run) => ({
      id: run.id,
      file: fileName(run.source_file),
      added: Math.max(0, (run.tests_after ?? 0) - run.tests_before),
    }));
  if (!data.length) return <div className="h-10" />;
  return (
    <ChartContainer
      config={{ added: { label: "Tests added", color: "var(--chart-1)" } }}
      className="aspect-auto h-10 w-full"
    >
      <BarChart data={data} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
        <ChartTooltip
          cursor={false}
          content={
            <ChartTooltipContent
              labelFormatter={(_, payload) => payload?.[0]?.payload.file}
            />
          }
        />
        <Bar
          dataKey="added"
          fill="var(--color-added)"
          radius={2}
          maxBarSize={10}
        />
      </BarChart>
    </ChartContainer>
  );
}

function RunMix({ runs }: { runs: RunSummary[] }) {
  const parts = [
    {
      key: "completed",
      className: "bg-foreground",
      count: runs.filter((run) => run.status === "completed").length,
    },
    {
      key: "running",
      className: "bg-success",
      count: runs.filter((run) => run.active).length,
    },
    {
      key: "cancelled",
      className: "bg-chart-4",
      count: runs.filter((run) => run.status === "cancelled").length,
    },
    {
      key: "failed",
      className: "bg-destructive",
      count: runs.filter((run) => run.status === "failed").length,
    },
  ].filter((part) => part.count);
  return (
    <div className="flex h-1.5 w-full gap-0.5 overflow-hidden rounded-full bg-muted">
      {parts.map((part) => (
        <span
          key={part.key}
          className={cn("h-full", part.className)}
          style={{ flexGrow: part.count }}
        />
      ))}
    </div>
  );
}

const historyConfig = {
  score: { label: "Kept score", color: "var(--chart-1)" },
} satisfies ChartConfig;

function ScoreHistory({
  runs,
  className,
}: {
  runs: RunSummary[];
  className?: string;
}) {
  const data = runs
    .filter((run) => run.status === "completed" && run.kept_score != null)
    .slice(0, 24)
    .reverse()
    .map((run) => ({
      file: fileName(run.source_file),
      when: new Date(run.created_at).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }),
      score: Math.round(run.kept_score ?? 0),
    }));
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Mutation score over time</CardTitle>
        <CardDescription>Kept score of each completed run</CardDescription>
        <CardAction>
          <Badge variant="outline">Target {TARGET}%</Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex-1">
        {data.length > 1 ? (
          <ChartContainer
            config={historyConfig}
            className="aspect-auto h-[240px] w-full"
          >
            <AreaChart data={data} margin={{ top: 8, right: 8, left: 0 }}>
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey="when"
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                minTickGap={32}
              />
              <YAxis
                domain={[0, 100]}
                ticks={[0, 20, 40, 60, 80, 100]}
                tickLine={false}
                axisLine={false}
                width={40}
                tickFormatter={(value) => `${value}%`}
              />
              <ReferenceLine
                y={TARGET}
                stroke="var(--foreground)"
                strokeDasharray="4 4"
                strokeOpacity={0.4}
              />
              <ChartTooltip
                cursor={false}
                content={
                  <ChartTooltipContent
                    indicator="line"
                    labelFormatter={(_, payload) =>
                      `${payload?.[0]?.payload.file} · ${payload?.[0]?.payload.when}`
                    }
                    formatter={(value) => (
                      <span className="flex w-full justify-between gap-4">
                        <span className="text-muted-foreground">
                          Kept score
                        </span>
                        <span className="font-mono font-medium tabular-nums">
                          {value}%
                        </span>
                      </span>
                    )}
                  />
                }
              />
              <Area
                dataKey="score"
                type="monotone"
                stroke="var(--color-score)"
                strokeWidth={2}
                fill="var(--color-score)"
                fillOpacity={0.05}
                dot={{ r: 3, fill: "var(--background)", strokeWidth: 2 }}
                activeDot={{ r: 4 }}
              />
            </AreaChart>
          </ChartContainer>
        ) : (
          <Empty className="h-[240px] p-6">
            <EmptyHeader>
              <EmptyTitle>Not enough history yet</EmptyTitle>
              <EmptyDescription>
                Complete two or more runs to see the trend.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
      </CardContent>
    </Card>
  );
}

const outcomeConfig = {
  count: { label: "Mutants" },
  killed: { label: "Killed", color: "var(--chart-1)" },
  timeout: { label: "Timeout", color: "var(--chart-2)" },
  survived: { label: "Survived", color: "var(--chart-3)" },
  uncovered: { label: "No coverage", color: "var(--chart-5)" },
} satisfies ChartConfig;

function OutcomeDonut({
  mutation,
  score,
}: {
  mutation?: Mutation | null;
  score: number | null;
}) {
  const data = mutation
    ? [
        { key: "killed", count: mutation.killed },
        { key: "timeout", count: mutation.timeout },
        { key: "survived", count: mutation.survived },
        { key: "uncovered", count: mutation.no_coverage },
      ]
        .filter((item) => item.count)
        .map((item) => ({ ...item, fill: `var(--color-${item.key})` }))
    : [];
  return (
    <div className="flex items-center gap-6">
      <ChartContainer
        config={outcomeConfig}
        className="aspect-square h-[150px] shrink-0"
      >
        <PieChart>
          <ChartTooltip
            cursor={false}
            content={<ChartTooltipContent hideLabel nameKey="key" />}
          />
          <Pie
            data={data.length ? data : [{ key: "uncovered", count: 1 }]}
            dataKey="count"
            nameKey="key"
            innerRadius={52}
            outerRadius={68}
            strokeWidth={2}
            stroke="var(--background)"
            isAnimationActive
          >
            <Label
              content={({ viewBox }) => {
                if (!viewBox || !("cx" in viewBox)) return null;
                return (
                  <text
                    x={viewBox.cx}
                    y={viewBox.cy}
                    textAnchor="middle"
                    dominantBaseline="middle"
                  >
                    <tspan
                      x={viewBox.cx}
                      y={viewBox.cy}
                      className="fill-foreground text-2xl font-medium"
                    >
                      {percent(score)}
                    </tspan>
                    <tspan
                      x={viewBox.cx}
                      y={(viewBox.cy ?? 0) + 20}
                      className="fill-muted-foreground text-xs"
                    >
                      kept
                    </tspan>
                  </text>
                );
              }}
            />
          </Pie>
        </PieChart>
      </ChartContainer>
      <dl className="grid flex-1 gap-2 text-caption">
        {(["killed", "timeout", "survived", "uncovered"] as const).map(
          (key) => (
            <div key={key} className="flex items-center gap-2">
              <span
                className="size-2 shrink-0 rounded-[2px]"
                style={{ background: outcomeConfig[key].color }}
              />
              <dt className="flex-1 text-muted-foreground">
                {outcomeConfig[key].label}
              </dt>
              <dd className="font-medium tabular-nums">
                {mutation
                  ? {
                      killed: mutation.killed,
                      timeout: mutation.timeout,
                      survived: mutation.survived,
                      uncovered: mutation.no_coverage,
                    }[key]
                  : "—"}
              </dd>
            </div>
          ),
        )}
      </dl>
    </div>
  );
}

const fileConfig = {
  score: { label: "Kept score", color: "var(--chart-1)" },
} satisfies ChartConfig;

function ScoreByFile({
  files,
}: {
  files: { file: string; score: number; label: string }[];
}) {
  return (
    <ChartContainer
      config={fileConfig}
      className="aspect-auto w-full"
      style={{ height: Math.max(120, files.length * 40) }}
    >
      <BarChart data={files} layout="vertical" margin={{ left: 0, right: 0 }}>
        <CartesianGrid horizontal={false} />
        <YAxis
          yAxisId="file"
          dataKey="file"
          type="category"
          tickLine={false}
          axisLine={false}
          width={110}
          tick={{ fontFamily: "var(--font-mono)" }}
        />
        <YAxis
          yAxisId="label"
          orientation="right"
          dataKey="label"
          type="category"
          tickLine={false}
          axisLine={false}
          width={64}
          tick={{ fill: "var(--foreground)", fontWeight: 500 }}
        />
        <XAxis type="number" domain={[0, 100]} hide />
        <ReferenceLine
          yAxisId="file"
          x={TARGET}
          stroke="var(--foreground)"
          strokeDasharray="4 4"
          strokeOpacity={0.4}
        />
        <ChartTooltip
          cursor={false}
          content={<ChartTooltipContent hideIndicator />}
        />
        <Bar
          yAxisId="file"
          dataKey="score"
          fill="var(--color-score)"
          radius={999}
          barSize={8}
        />
      </BarChart>
    </ChartContainer>
  );
}

const operatorConfig = {
  killed: { label: "Killed", color: "var(--chart-1)" },
  survived: { label: "Not killed", color: "var(--chart-4)" },
} satisfies ChartConfig;

function OperatorChart({ mutation }: { mutation?: Mutation | null }) {
  const data = useMemo(() => {
    const counts = new Map<string, { killed: number; survived: number }>();
    for (const mutant of mutation?.mutants ?? []) {
      const item = counts.get(mutant.mutator) ?? { killed: 0, survived: 0 };
      if (mutant.status === "Killed" || mutant.status === "Timeout")
        item.killed++;
      else item.survived++;
      counts.set(mutant.mutator, item);
    }
    return [...counts.entries()]
      .map(([name, item]) => ({
        name,
        ...item,
        ratio: `${item.killed} / ${item.killed + item.survived}`,
      }))
      .sort((a, b) => b.killed + b.survived - (a.killed + a.survived))
      .slice(0, 6);
  }, [mutation]);
  if (!data.length) {
    return (
      <Empty className="h-[200px] p-6">
        <EmptyHeader>
          <EmptyDescription>No mutation data yet.</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }
  return (
    <ChartContainer
      config={operatorConfig}
      className="aspect-auto w-full"
      style={{ height: data.length * 40 + 40 }}
    >
      <BarChart data={data} layout="vertical" margin={{ left: 0, right: 0 }}>
        <YAxis
          yAxisId="name"
          dataKey="name"
          type="category"
          tickLine={false}
          axisLine={false}
          width={150}
          tick={{ fontFamily: "var(--font-mono)" }}
          tickFormatter={(name: string) =>
            name.length > 18 ? `${name.slice(0, 17)}…` : name
          }
        />
        <YAxis
          yAxisId="ratio"
          orientation="right"
          dataKey="ratio"
          type="category"
          tickLine={false}
          axisLine={false}
          width={56}
          tick={{ fill: "var(--foreground)", fontWeight: 500 }}
        />
        <XAxis type="number" hide />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar
          yAxisId="name"
          dataKey="killed"
          stackId="outcome"
          fill="var(--color-killed)"
          barSize={8}
          radius={[999, 0, 0, 999]}
        />
        <Bar
          yAxisId="name"
          dataKey="survived"
          stackId="outcome"
          fill="var(--color-survived)"
          barSize={8}
          radius={[0, 999, 999, 0]}
        />
      </BarChart>
    </ChartContainer>
  );
}
