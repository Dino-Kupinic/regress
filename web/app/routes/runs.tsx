import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  Link,
  type MetaFunction,
  useLoaderData,
  useNavigate,
} from "react-router";
import { Bar, BarChart, CartesianGrid, Cell, XAxis, YAxis } from "recharts";
import { useNewRun } from "~/components/new-run";
import { Page, PageHeader } from "~/components/page";
import { RunStatusBadge } from "~/components/run-status";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import {
  type ChartConfig,
  ChartContainer,
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
import { api, type RunSummary } from "~/lib/api";
import {
  dateTime,
  duration,
  fileName,
  pageTitle,
  percent,
  signedPoints,
} from "~/lib/utils";

export const meta: MetaFunction = () => [{ title: pageTitle("Runs") }];

export async function clientLoader() {
  return Promise.all([api.runs(), api.sources()]).then(([runs, sources]) => ({
    runs,
    sources,
  }));
}

const statuses = [
  "all",
  "running",
  "completed",
  "failed",
  "cancelled",
] as const;
type StatusFilter = (typeof statuses)[number];

export default function Runs() {
  const initial = useLoaderData<typeof clientLoader>();
  const { open } = useNewRun();
  const navigate = useNavigate();
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    initialData: initial.runs,
    refetchInterval: 5_000,
  }).data;
  const [status, setStatus] = useState<StatusFilter>("all");
  const [source, setSource] = useState("all");
  const filtered = useMemo(
    () =>
      runs.filter(
        (run) =>
          (status === "all" ||
            (status === "running" ? run.active : run.status === status)) &&
          (source === "all" || run.source_file === source),
      ),
    [runs, status, source],
  );
  const today = runs.filter(
    (run) =>
      new Date(run.created_at).toDateString() === new Date().toDateString(),
  );
  const count = (item: StatusFilter) =>
    item === "all"
      ? runs.length
      : item === "running"
        ? runs.filter((run) => run.active).length
        : runs.filter((run) => run.status === item).length;
  return (
    <Page>
      <PageHeader
        title="Runs"
        description={`${today.length} today · ${runs.filter((run) => run.active).length} running · stored in .regress/runs`}
        actions={
          <Select value={source} onValueChange={setSource}>
            <SelectTrigger aria-label="Filter by source file" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              <SelectGroup>
                <SelectItem value="all">All files</SelectItem>
                {initial.sources.map((item) => (
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
        }
      />
      <RunHistory runs={filtered} />
      <section className="flex flex-col gap-4">
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={status}
          onValueChange={(value) => value && setStatus(value as StatusFilter)}
          className="flex-wrap"
        >
          {statuses.map((item) => (
            <ToggleGroupItem key={item} value={item}>
              {item[0].toUpperCase() + item.slice(1)}
              <span className="text-muted-foreground tabular-nums">
                {count(item)}
              </span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <Card className="py-0">
          {filtered.length ? (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Run</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Tests</TableHead>
                  <TableHead className="text-right">First</TableHead>
                  <TableHead className="text-right">Kept</TableHead>
                  <TableHead className="text-right">Change</TableHead>
                  <TableHead className="text-right">Duration</TableHead>
                  <TableHead>Started</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((run) => (
                  <TableRow
                    key={run.id}
                    className="cursor-pointer"
                    onClick={() =>
                      navigate(`/runs/${encodeURIComponent(run.id)}`)
                    }
                  >
                    <TableCell>
                      <Link
                        to={`/runs/${encodeURIComponent(run.id)}`}
                        className="font-mono font-medium underline-offset-4 hover:underline"
                        onClick={(event) => event.stopPropagation()}
                      >
                        {run.id}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <div className="font-mono">{run.source_file}</div>
                      <div className="text-xs text-muted-foreground">
                        {run.model}
                      </div>
                    </TableCell>
                    <TableCell>
                      <RunStatusBadge status={run.status} active={run.active} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {run.tests_before} → {run.tests_after ?? "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {percent(run.reference_score)}
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {percent(run.kept_score)}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground tabular-nums">
                      {signedPoints(run.improvement)}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground tabular-nums">
                      {run.active ? "—" : duration(run.duration_seconds)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {dateTime(run.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <Empty className="p-10">
              <EmptyHeader>
                <EmptyTitle>No runs match these filters</EmptyTitle>
                <EmptyDescription>
                  Change the filters or start a run.
                </EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button onClick={() => open()}>New run</Button>
              </EmptyContent>
            </Empty>
          )}
        </Card>
      </section>
    </Page>
  );
}

const historyConfig = {
  score: { label: "Kept score" },
  completed: { label: "Completed", color: "var(--chart-1)" },
  running: { label: "Running", color: "var(--success)" },
  failed: { label: "Failed", color: "var(--destructive)" },
  cancelled: { label: "Cancelled", color: "var(--chart-4)" },
} satisfies ChartConfig;

function RunHistory({ runs }: { runs: RunSummary[] }) {
  const data = runs
    .slice(0, 30)
    .reverse()
    .map((run) => {
      const state = run.active ? "running" : run.status;
      return {
        id: run.id,
        state,
        file: fileName(run.source_file),
        when: new Date(run.created_at).toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }),
        // Runs without a score still get a short bar so they stay visible.
        score: Math.max(3, Math.round(run.kept_score ?? 0)),
        label:
          run.kept_score == null
            ? historyConfig[state].label
            : percent(run.kept_score),
      };
    });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Run history</CardTitle>
        <CardDescription>
          Kept mutation score of the last {data.length}{" "}
          {data.length === 1 ? "run" : "runs"}, oldest first
        </CardDescription>
      </CardHeader>
      <CardContent>
        {data.length ? (
          <ChartContainer
            config={historyConfig}
            className="aspect-auto h-[200px] w-full"
          >
            <BarChart data={data} margin={{ top: 8, left: 0, right: 0 }}>
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
                ticks={[0, 50, 100]}
                tickLine={false}
                axisLine={false}
                width={40}
                tickFormatter={(value) => `${value}%`}
              />
              <ChartTooltip
                cursor={false}
                content={
                  <ChartTooltipContent
                    hideIndicator
                    labelFormatter={(_, payload) =>
                      `${payload?.[0]?.payload.file} · ${payload?.[0]?.payload.when}`
                    }
                    formatter={(_, __, item) => (
                      <span className="flex w-full justify-between gap-4">
                        <span className="text-muted-foreground">
                          {historyConfig[item.payload.state as RunState].label}
                        </span>
                        <span className="font-mono font-medium tabular-nums">
                          {item.payload.label}
                        </span>
                      </span>
                    )}
                  />
                }
              />
              <Bar dataKey="score" radius={[3, 3, 0, 0]} maxBarSize={28}>
                {data.map((item) => (
                  <Cell key={item.id} fill={`var(--color-${item.state})`} />
                ))}
              </Bar>
            </BarChart>
          </ChartContainer>
        ) : (
          <Empty className="h-[200px] p-6">
            <EmptyHeader>
              <EmptyDescription>No runs to chart.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
      </CardContent>
    </Card>
  );
}

type RunState = "running" | RunSummary["status"];
