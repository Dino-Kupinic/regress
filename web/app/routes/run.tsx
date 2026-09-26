import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useLoaderData, useParams, useSearchParams } from "react-router";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts";
import { useNewRun } from "~/components/new-run";
import { Page } from "~/components/page";
import { RunStatusBadge } from "~/components/run-status";
import { StatusDot } from "~/components/status-dot";
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
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "~/components/ui/empty";
import { Input } from "~/components/ui/input";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemGroup,
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
import { Separator } from "~/components/ui/separator";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "~/components/ui/toggle-group";
import {
  api,
  type Mutant,
  type Mutation,
  type RunDetail,
  type RunEvent,
} from "~/lib/api";
import { copyToClipboard } from "~/lib/clipboard";
import {
  cn,
  dateTime,
  duration,
  fileName,
  percent,
  signedPoints,
} from "~/lib/utils";

export async function clientLoader({ params }: { params: { runId?: string } }) {
  if (!params.runId) throw new Error("Missing run ID");
  return Promise.all([api.run(params.runId), api.events(params.runId)]).then(
    ([run, events]) => ({ run, events }),
  );
}

type Tab = "overview" | "mutants" | "tests" | "model";
const TARGET = 80;

export default function RunPage() {
  const initial = useLoaderData<typeof clientLoader>();
  const { runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "overview";
  const { open } = useNewRun();
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
    setParams(next === "overview" ? {} : { tab: next }, { replace: true });
  const measured = run.report.stages.filter(
    (stage) => stage.mutation && !stage.rejected,
  );
  const kept = run.report.stages.find(
    (stage) => stage.label === run.report.kept_stage,
  );
  const current = kept?.mutation ?? measured.at(-1)?.mutation;
  const isLive = run.summary.active;
  const statusText = isLive
    ? "Running"
    : run.summary.status[0].toUpperCase() + run.summary.status.slice(1);
  return (
    <Page className="gap-8">
      <header className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="truncate font-mono text-heading font-medium tracking-normal">
              {run.summary.source_file}
            </h1>
            <RunStatusBadge status={run.summary.status} active={isLive} />
          </div>
          <dl className="flex flex-wrap gap-x-6 gap-y-1 text-caption">
            <Meta label="Tests">
              <span className="font-mono">{run.summary.test_file}</span>
            </Meta>
            <Meta label="Model">{run.summary.model}</Meta>
            <Meta label="Stages">{measured.length}</Meta>
            <Meta label="Duration">
              {duration(
                run.live?.elapsed_seconds ?? run.summary.duration_seconds,
              )}
            </Meta>
            <Meta label="Started">{dateTime(run.summary.created_at)}</Meta>
          </dl>
        </div>
        <div className="flex shrink-0 gap-2">
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
                Artifacts
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
      </header>
      {run.summary.error && (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>{statusText}</AlertTitle>
          <AlertDescription>
            <p className="font-mono text-xs whitespace-pre-wrap">
              {run.summary.error}
            </p>
          </AlertDescription>
        </Alert>
      )}
      {cancel.isError && (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>Could not cancel the run</AlertTitle>
          <AlertDescription>{cancel.error.message}</AlertDescription>
        </Alert>
      )}
      {isLive ? (
        <LiveRun run={run} events={events} />
      ) : (
        <Tabs
          value={tab}
          onValueChange={(value) => setTab(value as Tab)}
          className="gap-6"
        >
          <div className="border-b">
            <TabsList variant="line" className="-mb-px">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="mutants">
                Mutants
                <span className="text-muted-foreground tabular-nums">
                  {current?.mutants.length ?? ""}
                </span>
              </TabsTrigger>
              <TabsTrigger value="tests">Tests</TabsTrigger>
              <TabsTrigger value="model">
                Model log
                <span className="text-muted-foreground tabular-nums">
                  {run.report.usage.calls || ""}
                </span>
              </TabsTrigger>
            </TabsList>
          </div>
          <TabsContent
            value="overview"
            className="motion-safe:animate-in fade-in-0"
          >
            <Overview run={run} setTab={setTab} />
          </TabsContent>
          <TabsContent
            value="mutants"
            className="motion-safe:animate-in fade-in-0"
          >
            <Mutants run={run} />
          </TabsContent>
          <TabsContent
            value="tests"
            className="motion-safe:animate-in fade-in-0"
          >
            <Tests run={run} />
          </TabsContent>
          <TabsContent
            value="model"
            className="motion-safe:animate-in fade-in-0"
          >
            <ModelLog run={run} />
          </TabsContent>
        </Tabs>
      )}
    </Page>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-1.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

const phases = ["Validate", "Generate", "Mutate", "Improve", "Compare"];

function LiveRun({ run, events }: { run: RunDetail; events: RunEvent[] }) {
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
  const feed = useRef<HTMLDivElement>(null);
  const eventCount = events.length;
  // Follow new events while the reader is near the bottom of the feed.
  useEffect(() => {
    const element = feed.current;
    if (!element || !eventCount) return;
    const distance =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    if (distance < 160)
      element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
  }, [eventCount]);
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardContent className="overflow-x-auto">
          <ol className="grid min-w-[560px] grid-cols-5 gap-3">
            {phases.map((name, index) => {
              const state =
                index < phase
                  ? "done"
                  : index === phase
                    ? "current"
                    : "waiting";
              return (
                <li key={name} className="flex flex-col gap-3">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-medium transition-colors duration-500",
                        state === "done" &&
                          "border-foreground bg-foreground text-background",
                        state === "current" && "border-foreground",
                        state === "waiting" && "text-muted-foreground",
                      )}
                    >
                      {state === "done" ? (
                        <Check className="size-3.5" />
                      ) : state === "current" ? (
                        <Spinner className="size-3.5" />
                      ) : (
                        index + 1
                      )}
                    </span>
                    {index < phases.length - 1 && (
                      <span
                        className={cn(
                          "h-px flex-1 transition-colors duration-500",
                          index < phase ? "bg-foreground" : "bg-border",
                        )}
                      />
                    )}
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <span
                      className={cn(
                        "text-sm font-medium",
                        state === "waiting" && "text-muted-foreground",
                      )}
                    >
                      {name}
                    </span>
                    <span
                      className={cn(
                        "truncate text-caption text-muted-foreground",
                        state === "current" && "shimmer",
                      )}
                    >
                      {state === "current"
                        ? (run.live?.activity_status ?? "In progress")
                        : state === "done"
                          ? "Done"
                          : "Waiting"}
                    </span>
                  </div>
                </li>
              );
            })}
          </ol>
        </CardContent>
      </Card>
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(300px,1fr)]">
        <Card className="gap-0 py-0">
          <CardHeader className="border-b py-4">
            <CardTitle>Activity</CardTitle>
            <CardDescription>{events.length} events</CardDescription>
          </CardHeader>
          <div
            ref={feed}
            className="max-h-[520px] overflow-y-auto py-2 scroll-fade"
          >
            {events.length ? (
              <ol>
                {events.map((event) => (
                  <li
                    key={event.seq}
                    className="grid grid-cols-[4.5rem_0.5rem_1fr] items-baseline gap-3 px-6 py-2 text-sm motion-safe:animate-in fade-in-0 slide-in-from-bottom-1"
                  >
                    <time className="font-mono text-xs text-muted-foreground">
                      {new Date(event.time).toLocaleTimeString(undefined, {
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                      })}
                    </time>
                    <StatusDot
                      tone={
                        event.type === "rejected" || event.type === "error"
                          ? "destructive"
                          : "default"
                      }
                      className="self-center"
                    />
                    <span className="leading-relaxed">{event.message}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="shimmer px-6 py-6 text-sm text-muted-foreground">
                Waiting for the first activity…
              </p>
            )}
            {run.live?.activity && (
              <div className="mx-3 my-1 grid grid-cols-[4.5rem_0.5rem_1fr] items-baseline gap-3 rounded-md bg-accent px-3 py-2 text-sm">
                <span className="font-mono text-xs text-muted-foreground">
                  now
                </span>
                <StatusDot tone="success" pulse className="self-center" />
                <span className="shimmer">
                  {run.live.activity} · {run.live.activity_status ?? "working"}
                </span>
              </div>
            )}
          </div>
        </Card>
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardDescription>Results so far</CardDescription>
              <p className="text-display font-medium tabular-nums">
                {latestMutation ? percent(latestMutation.score) : "—"}
              </p>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {latestMutation ? (
                <>
                  <Progress
                    value={
                      latestMutation.mutants.length
                        ? (latestMutation.killed /
                            latestMutation.mutants.length) *
                          100
                        : 0
                    }
                  />
                  <p className="text-caption text-muted-foreground">
                    {latestMutation.killed} killed · {latestMutation.survived}{" "}
                    survived · {latestMutation.no_coverage} uncovered
                  </p>
                </>
              ) : (
                <p className="shimmer text-caption text-muted-foreground">
                  Awaiting the first mutation test
                </p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardDescription>Current activity</CardDescription>
              <CardTitle className="shimmer">
                {run.live?.activity ?? "Starting run…"}
              </CardTitle>
            </CardHeader>
            <CardContent className="text-caption text-muted-foreground">
              Elapsed {duration(run.live?.elapsed_seconds)}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

const stageConfig = {
  score: { label: "Mutation score" },
  kept: { label: "Kept", color: "var(--chart-1)" },
  other: { label: "Not kept", color: "var(--chart-4)" },
} satisfies ChartConfig;

const densityConfig = {
  count: { label: "Mutants", color: "var(--chart-1)" },
} satisfies ChartConfig;

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
  });
  const shown = artifacts.data
    ?.filter(
      (item) =>
        item.path === "report.json" ||
        ["tests/", "llm/", "stryker/", "vitest/"].some((prefix) =>
          item.path.startsWith(prefix),
        ),
    )
    .slice(0, 8);
  const density = useMemo(() => {
    const map = new Map<number, number>();
    for (const mutant of mutation?.mutants ?? [])
      map.set(mutant.start_line, (map.get(mutant.start_line) ?? 0) + 1);
    return [...map.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([line, count]) => ({ line, count }));
  }, [mutation]);
  const stageData = measured.map((stage) => ({
    label: stage.label,
    score: Math.round(stage.mutation?.score ?? 0),
    display: percent(stage.mutation?.score),
    kept: stage.label === run.report.kept_stage,
  }));
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardDescription>Mutation score · kept</CardDescription>
            <p className="text-display font-medium tabular-nums">
              {percent(run.summary.kept_score)}
            </p>
            <CardAction>
              <Badge variant="outline">
                {signedPoints(run.summary.improvement)} vs first stage
              </Badge>
            </CardAction>
          </CardHeader>
          <CardContent>
            <ChartContainer
              config={stageConfig}
              className="aspect-auto h-[220px] w-full"
            >
              <BarChart
                data={stageData}
                margin={{ top: 24, left: 0, right: 0 }}
              >
                <CartesianGrid vertical={false} />
                <XAxis
                  dataKey="label"
                  tickLine={false}
                  axisLine={false}
                  tickMargin={8}
                />
                <YAxis
                  domain={[0, 100]}
                  ticks={[0, 50, 100]}
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
                  label={{
                    value: `${TARGET}% target`,
                    position: "insideTopRight",
                    fill: "var(--muted-foreground)",
                    fontSize: 11,
                  }}
                />
                <ChartTooltip
                  cursor={false}
                  content={<ChartTooltipContent hideIndicator />}
                />
                <Bar dataKey="score" radius={[4, 4, 0, 0]} maxBarSize={72}>
                  {stageData.map((item) => (
                    <Cell
                      key={item.label}
                      fill={
                        item.kept ? "var(--color-kept)" : "var(--color-other)"
                      }
                    />
                  ))}
                  <LabelList
                    dataKey="display"
                    position="top"
                    offset={8}
                    className="fill-foreground font-medium"
                    fontSize={12}
                  />
                </Bar>
              </BarChart>
            </ChartContainer>
          </CardContent>
          <CardFooter className="text-caption text-muted-foreground">
            Detected ÷ valid mutants. Timeouts count as detected.
          </CardFooter>
        </Card>
        <OutcomeCard mutation={mutation} />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>
            Mutants along {fileName(run.summary.source_file)}
          </CardTitle>
          <CardDescription>
            Mutants per source line. Select a bar to explore them.
          </CardDescription>
          <CardAction>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setTab("mutants")}
            >
              Explore mutants
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          {density.length ? (
            <ChartContainer
              config={densityConfig}
              className="aspect-auto h-[140px] w-full"
            >
              <BarChart data={density} margin={{ top: 4, left: 0, right: 0 }}>
                <XAxis
                  dataKey="line"
                  type="number"
                  domain={[1, "dataMax"]}
                  tickLine={false}
                  axisLine={false}
                  tickMargin={8}
                  allowDecimals={false}
                />
                <YAxis hide allowDecimals={false} />
                <ChartTooltip
                  cursor={false}
                  content={
                    <ChartTooltipContent
                      labelFormatter={(_, payload) =>
                        `Line ${payload?.[0]?.payload.line}`
                      }
                    />
                  }
                />
                <Bar
                  dataKey="count"
                  fill="var(--color-count)"
                  radius={[2, 2, 0, 0]}
                  maxBarSize={6}
                  className="cursor-pointer"
                  onClick={() => setTab("mutants")}
                />
              </BarChart>
            </ChartContainer>
          ) : (
            <Empty className="h-[140px] p-6">
              <EmptyHeader>
                <EmptyDescription>
                  No mutant data in this report.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </CardContent>
      </Card>
      <div className="grid items-start gap-4 lg:grid-cols-5">
        <Card className="gap-0 py-0 lg:col-span-3">
          <CardHeader className="border-b py-4">
            <CardTitle>Stages</CardTitle>
          </CardHeader>
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Stage</TableHead>
                <TableHead className="text-right">Tests</TableHead>
                <TableHead className="text-right">Killed</TableHead>
                <TableHead className="text-right">Survived</TableHead>
                <TableHead className="text-right">No cov.</TableHead>
                <TableHead className="text-right">Score</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {stages.map((stage) => (
                <TableRow key={stage.label}>
                  <TableCell>
                    <span className="flex items-center gap-2">
                      {stage.label}
                      {stage.label === run.report.kept_stage && (
                        <Badge>Kept</Badge>
                      )}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {stage.test_count}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {stage.mutation?.killed ?? "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {stage.mutation?.survived ?? "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {stage.mutation?.no_coverage ?? "—"}
                  </TableCell>
                  <TableCell className="text-right font-medium tabular-nums">
                    {percent(stage.mutation?.score)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <CardFooter className="border-t py-4 text-caption text-muted-foreground">
            {run.report.model} · {run.report.usage.calls} calls ·{" "}
            {run.report.usage.input_tokens.toLocaleString()} input /{" "}
            {run.report.usage.output_tokens.toLocaleString()} output tokens
          </CardFooter>
        </Card>
        <Card id="artifacts" className="scroll-mt-20 gap-0 py-0 lg:col-span-2">
          <CardHeader className="border-b py-4">
            <CardTitle>Artifacts</CardTitle>
            <CardDescription>Saved under .regress/runs</CardDescription>
          </CardHeader>
          {artifacts.isPending ? (
            <div className="flex flex-col gap-3 p-4">
              {[0, 1, 2, 3].map((index) => (
                <Skeleton key={index} className="h-5" />
              ))}
            </div>
          ) : shown?.length ? (
            <ItemGroup className="p-2">
              {shown.map((item) => (
                <Item key={item.path} size="sm" asChild>
                  <a
                    href={`/api/runs/${encodeURIComponent(run.summary.id)}/artifacts/${item.path.split("/").map(encodeURIComponent).join("/")}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <ItemContent>
                      <ItemTitle className="font-mono text-xs font-normal">
                        {item.path}
                      </ItemTitle>
                    </ItemContent>
                    <ItemActions className="text-caption text-muted-foreground tabular-nums">
                      {(item.size / 1024).toFixed(1)} KB
                    </ItemActions>
                  </a>
                </Item>
              ))}
            </ItemGroup>
          ) : (
            <p className="p-6 text-sm text-muted-foreground">
              No artifacts found.
            </p>
          )}
        </Card>
      </div>
    </div>
  );
}

function OutcomeCard({ mutation }: { mutation?: Mutation | null }) {
  const total = mutation?.mutants.length ?? 0;
  const rows: [string, number | undefined][] = [
    ["Killed", mutation?.killed],
    ["Survived", mutation?.survived],
    ["No coverage", mutation?.no_coverage],
    ["Timeout", mutation?.timeout],
    ["Compile / runtime error", mutation?.errors],
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Mutant outcomes</CardTitle>
        <CardDescription>{total} mutants in the kept stage</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {rows.map(([label, count]) => (
          <div key={label} className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between text-sm">
              <span className="text-muted-foreground">{label}</span>
              <span className="font-medium tabular-nums">{count ?? "—"}</span>
            </div>
            <Progress value={total ? ((count ?? 0) / total) * 100 : 0} />
          </div>
        ))}
      </CardContent>
      <CardFooter className="mt-auto text-caption text-muted-foreground">
        Only the selected source and test file were mutation tested.
      </CardFooter>
    </Card>
  );
}

type MutantFilter = "all" | "killed" | "survived" | "uncovered";

const matchesFilter = (item: Mutant, filter: MutantFilter) =>
  filter === "all" ||
  (filter === "killed"
    ? item.detected
    : filter === "survived"
      ? item.status === "Survived"
      : item.status === "NoCoverage");

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
  const [filter, setFilter] = useState<MutantFilter>("all");
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
      matchesFilter(item, filter) &&
      (operator === "all" || item.mutator === operator) &&
      (!line || String(item.start_line).includes(line)),
  );
  const active = visible.find((item) => item.id === selected) ?? visible[0];
  const byLine = new Map<number, Mutant[]>();
  for (const item of visible)
    byLine.set(item.start_line, [...(byLine.get(item.start_line) ?? []), item]);
  const filters: [MutantFilter, string][] = [
    ["all", "All"],
    ["killed", "Killed"],
    ["survived", "Survived"],
    ["uncovered", "No coverage"],
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={filter}
          onValueChange={(value) => value && setFilter(value as MutantFilter)}
          className="flex-wrap"
        >
          {filters.map(([key, label]) => (
            <ToggleGroupItem key={key} value={key}>
              {label}
              <span className="text-muted-foreground tabular-nums">
                {mutants.filter((item) => matchesFilter(item, key)).length}
              </span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <div className="flex flex-wrap gap-2">
          <Select
            value={String(stageIndex)}
            onValueChange={(value) => setStageIndex(Number(value))}
          >
            <SelectTrigger size="sm" aria-label="Mutation stage">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {measured.map(({ stage, index }) => (
                  <SelectItem value={String(index)} key={index}>
                    {stage.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <Select value={operator} onValueChange={setOperator}>
            <SelectTrigger size="sm" aria-label="Filter by operator">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value="all">All operators</SelectItem>
                {operators.map((item) => (
                  <SelectItem key={item} value={item} className="font-mono">
                    {item}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <Input
            aria-label="Go to line"
            placeholder="Go to line"
            inputMode="numeric"
            className="h-8 w-28"
            value={line}
            onChange={(event) => setLine(event.target.value.replace(/\D/g, ""))}
          />
        </div>
      </div>
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.75fr)_minmax(300px,1fr)]">
        <Card className="min-w-0 gap-0 py-0">
          <CardHeader className="border-b py-4">
            <CardTitle className="truncate font-mono text-sm">
              {run.summary.source_file}
            </CardTitle>
            <CardAction className="text-caption text-muted-foreground">
              One square per mutant
            </CardAction>
          </CardHeader>
          <div className="max-h-[720px] overflow-auto py-2 font-mono text-xs scroll-fade">
            {source ? (
              source.content.split("\n").map((text, index) => {
                const lineNo = index + 1;
                const row = byLine.get(lineNo) ?? [];
                const isSelected = row.some((item) => item.id === active?.id);
                return (
                  <div
                    key={lineNo}
                    className={cn(
                      "grid min-h-6 grid-cols-[2.75rem_5.5rem_minmax(max-content,1fr)] items-center gap-2 border-l-2 border-transparent pr-4 pl-2",
                      isSelected && "border-foreground bg-accent",
                    )}
                  >
                    <span className="text-right text-muted-foreground tabular-nums select-none">
                      {lineNo}
                    </span>
                    <span className="flex items-center gap-0.5">
                      {row.slice(0, 5).map((item) => (
                        <button
                          type="button"
                          key={item.id}
                          title={`${item.mutator} · ${item.status}`}
                          aria-label={`Mutant ${item.id}: ${item.mutator}, ${item.status}`}
                          className={cn(
                            "size-2.5 rounded-[2px] border border-foreground transition-transform hover:scale-125",
                            item.detected ? "bg-foreground" : "bg-background",
                            item.id === active?.id &&
                              "ring-2 ring-ring/50 ring-offset-1",
                          )}
                          onClick={() => setSelected(item.id)}
                        />
                      ))}
                      {row.length > 5 && (
                        <span className="text-[10px] text-muted-foreground">
                          +{row.length - 5}
                        </span>
                      )}
                    </span>
                    <code className="whitespace-pre">{text || " "}</code>
                  </div>
                );
              })
            ) : (
              <div className="flex flex-col gap-2 p-4">
                {[0, 1, 2, 3, 4, 5].map((index) => (
                  <Skeleton key={index} className="h-4" />
                ))}
              </div>
            )}
          </div>
        </Card>
        <MutantDetail mutant={active} />
      </div>
    </div>
  );
}

function MutantDetail({ mutant }: { mutant?: Mutant }) {
  if (!mutant) {
    return (
      <Card>
        <Empty className="p-8">
          <EmptyHeader>
            <EmptyTitle>No mutants match these filters</EmptyTitle>
          </EmptyHeader>
        </Empty>
      </Card>
    );
  }
  return (
    <Card
      key={mutant.id}
      className="lg:sticky lg:top-4 motion-safe:animate-in fade-in-0"
    >
      <CardHeader>
        <CardTitle>Mutant {mutant.id}</CardTitle>
        <CardDescription className="font-mono">
          {mutant.mutator} · line {mutant.start_line}, cols{" "}
          {mutant.start_column}–{mutant.end_column}
        </CardDescription>
        <CardAction>
          <Badge
            variant={
              mutant.detected
                ? "default"
                : mutant.status === "Survived"
                  ? "destructive"
                  : "secondary"
            }
          >
            {mutant.status}
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <div className="flex flex-col gap-1 font-mono text-xs">
          <pre className="overflow-x-auto rounded-md bg-destructive/5 px-3 py-2 text-muted-foreground">
            − {mutant.original_line ?? mutant.original}
          </pre>
          <pre className="overflow-x-auto rounded-md bg-success/5 px-3 py-2">
            + {mutant.mutated_line ?? mutant.replacement}
          </pre>
        </div>
        <p>
          {mutant.status === "Survived"
            ? "Tests ran this code, but no assertion failed."
            : mutant.status === "NoCoverage"
              ? "No test covered this code."
              : "A test detected this mutation."}
        </p>
        <Separator />
        <div className="flex flex-col gap-2">
          <h3 className="font-medium">Covered by</h3>
          <p className="text-caption text-muted-foreground">
            {mutant.covered_by.length} tests · {mutant.killed_by.length} failed
          </p>
          <ul className="flex flex-col gap-1">
            {mutant.covered_by.slice(0, 6).map((item) => (
              <li key={item} className="text-caption">
                {item}
              </li>
            ))}
          </ul>
        </div>
        <Separator />
        <div className="flex flex-col gap-2">
          <h3 className="font-medium">Mutation</h3>
          <p className="text-caption">{mutant.summary}</p>
          {mutant.equivalent && (
            <p className="text-caption text-muted-foreground">
              Flagged as likely equivalent by the model.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
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
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-caption text-muted-foreground">Compare</span>
        <Badge variant="secondary" className="font-mono">
          {diff?.before_label ?? "Original"}
        </Badge>
        <span className="text-muted-foreground">→</span>
        <Select
          value={String(index)}
          onValueChange={(value) => setIndex(Number(value))}
        >
          <SelectTrigger size="sm" aria-label="Comparison stage">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {stages.map(({ stage, index: option }) => (
                <SelectItem value={String(option)} key={option}>
                  {stage.label} · {stage.test_count} tests
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <span className="ml-auto text-caption text-muted-foreground">
          {diff?.added_tests.length ?? 0} tests added
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={!diff}
          onClick={() =>
            diff && copyToClipboard(diff.after, "Test file copied")
          }
        >
          Copy file
        </Button>
      </div>
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(240px,0.9fr)_minmax(0,2fr)]">
        <Card className="gap-0 py-0">
          <CardHeader className="border-b py-4">
            <CardTitle>Added tests</CardTitle>
            <CardAction className="text-caption text-muted-foreground tabular-nums">
              {diff?.added_tests.length ?? 0}
            </CardAction>
          </CardHeader>
          {diff?.added_tests.length ? (
            <ul className="max-h-[640px] overflow-y-auto py-2 scroll-fade">
              {diff.added_tests.map((name) => (
                <li
                  key={name}
                  className="grid grid-cols-[1rem_1fr] gap-2 px-6 py-2 text-sm"
                >
                  <span className="font-mono text-success">+</span>
                  {name}
                </li>
              ))}
            </ul>
          ) : (
            <p className="p-6 text-sm text-muted-foreground">
              No added test names in this stage.
            </p>
          )}
        </Card>
        <Card className="min-w-0 gap-0 py-0">
          <CardHeader className="border-b py-3">
            <CardTitle className="truncate font-mono text-sm">
              {run.summary.test_file}
            </CardTitle>
            <CardAction>
              <ToggleGroup
                type="single"
                variant="outline"
                size="sm"
                value={mode}
                onValueChange={(value) =>
                  value && setMode(value as "unified" | "split")
                }
              >
                <ToggleGroupItem value="unified">Unified</ToggleGroupItem>
                <ToggleGroupItem value="split">Split</ToggleGroupItem>
              </ToggleGroup>
            </CardAction>
          </CardHeader>
          {diff ? (
            mode === "split" ? (
              <MergeDiff before={diff.before} after={diff.after} />
            ) : (
              <pre className="max-h-[800px] overflow-auto py-3 font-mono text-xs leading-relaxed scroll-fade">
                {diff.unified.split("\n").map((line, i) => (
                  <span
                    key={i}
                    className={cn(
                      "block min-h-5 px-4",
                      line.startsWith("+") &&
                        !line.startsWith("+++") &&
                        "bg-success/10",
                      line.startsWith("-") &&
                        !line.startsWith("---") &&
                        "bg-destructive/10",
                      line.startsWith("@@") && "text-muted-foreground",
                    )}
                  >
                    {line}
                    {"\n"}
                  </span>
                ))}
              </pre>
            )
          ) : (
            <div className="flex flex-col gap-2 p-4">
              {[0, 1, 2, 3, 4, 5, 6].map((key) => (
                <Skeleton key={key} className="h-4" />
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
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
      className="max-h-[800px] overflow-auto text-xs [&_.cm-mergeView]:min-w-[700px]"
      aria-label="Side by side test diff"
    />
  );
}

const tokenConfig = {
  input: { label: "Input", color: "var(--chart-1)" },
  output: { label: "Output", color: "var(--chart-3)" },
} satisfies ChartConfig;

function ModelLog({ run }: { run: RunDetail }) {
  const calls =
    useQuery({
      queryKey: ["llm", run.summary.id],
      queryFn: () => api.llm(run.summary.id),
    }).data ?? [];
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<"prompt" | "response" | "file">("prompt");
  const current = calls.find((item) => item.name === selected) ?? calls[0];
  const detail = useQuery({
    queryKey: ["llm-call", run.summary.id, current?.name],
    queryFn: () => api.llmCall(run.summary.id, current?.name),
    enabled: !!current,
  });
  const usage = run.report.usage;
  const tokens = calls.map((item, index) => ({
    call: `#${index + 1}`,
    name: `${item.kind} · attempt ${item.attempt}`,
    input: item.input_tokens,
    output: item.output_tokens,
  }));
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <UsageStat label="Model calls" value={usage.calls} />
        <UsageStat
          label="Tokens"
          value={(usage.input_tokens + usage.output_tokens).toLocaleString()}
          detail={`${usage.input_tokens.toLocaleString()} in · ${usage.output_tokens.toLocaleString()} out`}
        />
        <UsageStat
          label="Model time"
          value={duration(
            run.report.stages.reduce((sum, item) => sum + item.llm_seconds, 0),
          )}
        />
      </div>
      {tokens.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Tokens per call</CardTitle>
            <CardDescription>
              Input and output tokens, in call order
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer
              config={tokenConfig}
              className="aspect-auto h-[180px] w-full"
            >
              <BarChart data={tokens} margin={{ left: 0, right: 0 }}>
                <CartesianGrid vertical={false} />
                <XAxis
                  dataKey="call"
                  tickLine={false}
                  axisLine={false}
                  tickMargin={8}
                />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  width={48}
                  tickFormatter={(value: number) =>
                    value >= 1000 ? `${Math.round(value / 1000)}k` : `${value}`
                  }
                />
                <ChartTooltip
                  cursor={false}
                  content={
                    <ChartTooltipContent
                      labelFormatter={(_, payload) =>
                        payload?.[0]?.payload.name
                      }
                    />
                  }
                />
                <ChartLegend content={<ChartLegendContent />} />
                <Bar
                  dataKey="input"
                  stackId="tokens"
                  fill="var(--color-input)"
                  maxBarSize={40}
                />
                <Bar
                  dataKey="output"
                  stackId="tokens"
                  fill="var(--color-output)"
                  radius={[3, 3, 0, 0]}
                  maxBarSize={40}
                />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>
      )}
      {calls.length ? (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(260px,0.8fr)_minmax(0,2fr)]">
          <Card className="gap-0 py-0">
            <CardHeader className="border-b py-4">
              <CardTitle>Calls</CardTitle>
            </CardHeader>
            <ItemGroup className="gap-1 p-2">
              {calls.map((item, index) => (
                <Item
                  key={item.name}
                  size="sm"
                  asChild
                  className={cn(
                    "cursor-pointer text-left hover:bg-accent",
                    current?.name === item.name && "bg-accent",
                  )}
                >
                  <button type="button" onClick={() => setSelected(item.name)}>
                    <span
                      className={cn(
                        "flex size-6 shrink-0 items-center justify-center rounded-full text-xs tabular-nums transition-colors",
                        current?.name === item.name
                          ? "bg-foreground text-background"
                          : "bg-muted",
                      )}
                    >
                      {index + 1}
                    </span>
                    <ItemContent className="min-w-0">
                      <ItemTitle>
                        {item.kind} · attempt {item.attempt}
                      </ItemTitle>
                      <span className="truncate text-caption text-muted-foreground">
                        +{item.new_tests.length} tests ·{" "}
                        {(
                          item.input_tokens + item.output_tokens
                        ).toLocaleString()}{" "}
                        tokens
                      </span>
                    </ItemContent>
                  </button>
                </Item>
              ))}
            </ItemGroup>
          </Card>
          <Card className="min-w-0 gap-0 py-0">
            <CardHeader className="border-b py-3">
              <CardTitle>
                {current?.kind} · attempt {current?.attempt}
              </CardTitle>
              <CardDescription className="truncate">
                {current?.summary || "No summary supplied."}
              </CardDescription>
              <CardAction className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!detail.data}
                  onClick={() =>
                    detail.data &&
                    copyToClipboard(detail.data.prompt, "Prompt copied")
                  }
                >
                  Copy prompt
                </Button>
              </CardAction>
            </CardHeader>
            <Tabs
              value={view}
              onValueChange={(value) => setView(value as typeof view)}
              className="gap-0"
            >
              <div className="px-6 pt-4">
                <TabsList>
                  <TabsTrigger value="prompt">Prompt</TabsTrigger>
                  <TabsTrigger value="response">Response</TabsTrigger>
                  <TabsTrigger value="file">Returned file</TabsTrigger>
                </TabsList>
              </div>
              {detail.data ? (
                <>
                  <TabsContent value="prompt">
                    <CodeBlock>{detail.data.prompt}</CodeBlock>
                  </TabsContent>
                  <TabsContent value="response">
                    <div className="flex flex-col gap-5 p-6">
                      <section className="flex flex-col gap-1.5">
                        <h3 className="font-medium">Summary</h3>
                        <p className="leading-relaxed">
                          {detail.data.summary || "No summary supplied."}
                        </p>
                      </section>
                      <section className="flex flex-col gap-1.5">
                        <h3 className="font-medium">New tests</h3>
                        {detail.data.new_tests.length ? (
                          <ul className="flex flex-col gap-1">
                            {detail.data.new_tests.map((item) => (
                              <li key={item} className="text-caption">
                                <span className="mr-2 font-mono text-success">
                                  +
                                </span>
                                {item}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="text-caption text-muted-foreground">
                            No new test names.
                          </p>
                        )}
                      </section>
                      <section className="flex flex-col gap-1.5">
                        <h3 className="font-medium">Equivalent mutants</h3>
                        <p className="text-caption">
                          {detail.data.equivalent_mutants.join(", ") ||
                            "None flagged"}
                        </p>
                      </section>
                    </div>
                  </TabsContent>
                  <TabsContent value="file">
                    <CodeBlock>{detail.data.test_file}</CodeBlock>
                  </TabsContent>
                </>
              ) : (
                <div className="flex flex-col gap-2 p-6">
                  {[0, 1, 2, 3, 4].map((key) => (
                    <Skeleton key={key} className="h-4" />
                  ))}
                </div>
              )}
            </Tabs>
          </Card>
        </div>
      ) : (
        <Card>
          <Empty className="p-10">
            <EmptyHeader>
              <EmptyTitle>No saved model calls</EmptyTitle>
              <EmptyDescription>
                This run did not record any model calls.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        </Card>
      )}
    </div>
  );
}

function UsageStat({
  label,
  value,
  detail,
}: {
  label: string;
  value: ReactNode;
  detail?: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <p className="text-subheading font-medium tabular-nums">{value}</p>
      </CardHeader>
      {detail && (
        <CardFooter className="text-caption text-muted-foreground">
          {detail}
        </CardFooter>
      )}
    </Card>
  );
}

function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="max-h-[560px] overflow-auto px-6 py-4 font-mono text-xs leading-relaxed whitespace-pre-wrap scroll-fade">
      {children}
    </pre>
  );
}
