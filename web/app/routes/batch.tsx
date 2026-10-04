import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Copy } from "lucide-react";
import {
  Link,
  type MetaFunction,
  useLoaderData,
  useParams,
} from "react-router";
import { toast } from "sonner";
import { Page, PageHeader } from "~/components/page";
import { RunStatusBadge } from "~/components/run-status";
import { StatusDot } from "~/components/status-dot";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import { Progress } from "~/components/ui/progress";
import { Spinner } from "~/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { api, type BatchFile } from "~/lib/api";
import { copyToClipboard } from "~/lib/clipboard";
import {
  dateTime,
  duration,
  pageTitle,
  percent,
  signedPoints,
} from "~/lib/utils";

export async function clientLoader({
  params,
}: {
  params: { batchId?: string };
}) {
  if (!params.batchId) throw new Error("Missing batch ID");
  return api.batch(params.batchId);
}

export const meta: MetaFunction<typeof clientLoader> = ({ loaderData }) => [
  {
    title: pageTitle(
      loaderData ? `${loaderData.files.length} files` : "Batch",
      "Runs",
    ),
  },
];

const FINISHED: BatchFile["status"][] = [
  "completed",
  "failed",
  "cancelled",
  "skipped",
];

export default function BatchPage() {
  const initial = useLoaderData<typeof clientLoader>();
  const { batchId = "" } = useParams();
  const client = useQueryClient();
  const batch = useQuery({
    queryKey: ["batch", batchId],
    queryFn: () => api.batch(batchId),
    initialData: initial,
    refetchInterval: (query) => (query.state.data?.active ? 1_500 : false),
  }).data;
  const cancel = useMutation({
    mutationFn: () => api.cancelBatch(batchId),
    onSuccess: (detail) => {
      client.setQueryData(["batch", batchId], detail);
      toast("Cancelling. The test file of the current run is restored.");
    },
    onError: (failure) => toast.error(failure.message),
  });
  const done = batch.files.filter((file) =>
    FINISHED.includes(file.status),
  ).length;
  const running = batch.files.find((file) => file.status === "running");
  const command = `regress run ${batch.files.map((file) => file.source_file).join(" ")} --yes`;
  const { combined } = batch;
  return (
    <Page>
      <PageHeader
        title={`${batch.files.length} files`}
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>{dateTime(batch.created_at)}</span>
            <span className="font-mono">{batch.model}</span>
            {!batch.active && <span>{duration(batch.duration_seconds)}</span>}
          </span>
        }
        actions={
          <>
            <RunStatusBadge status={batch.status} active={batch.active} />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => copyToClipboard(command, "Command copied")}
            >
              <Copy data-icon="inline-start" />
              Copy command
            </Button>
            {batch.active && (
              <Button
                variant="outline"
                size="sm"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate()}
              >
                {cancel.isPending && <Spinner data-icon="inline-start" />}
                Cancel
              </Button>
            )}
          </>
        }
      />
      {batch.active && (
        <Card>
          <CardHeader>
            <CardTitle>
              <span className="shimmer">
                {running
                  ? `Running ${running.source_file}`
                  : "Starting the next file"}
              </span>
            </CardTitle>
            <CardDescription>
              {done} of {batch.files.length} files done. Each file is its own
              run; other runs wait until the last one is over.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Progress value={(100 * done) / batch.files.length} />
            {batch.current_run && (
              <Link
                to={`/runs/${encodeURIComponent(batch.current_run)}`}
                className="text-sm underline underline-offset-4"
              >
                Follow the current run
              </Link>
            )}
          </CardContent>
        </Card>
      )}
      {batch.status === "failed" && !batch.active && (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>Some files failed</AlertTitle>
          <AlertDescription>
            The other files still ran. Open a failed file's run for details.
          </AlertDescription>
        </Alert>
      )}
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="First" value={percent(combined.score_first)} />
        <Stat label="Kept" value={percent(combined.score_kept)} />
        <Stat
          label="Change"
          value={
            combined.score_first !== null && combined.score_kept !== null
              ? signedPoints(combined.score_kept - combined.score_first)
              : "—"
          }
          note={
            combined.files
              ? `Combined over ${combined.files} ${combined.files === 1 ? "file" : "files"} scored in both`
              : "No file scored yet"
          }
        />
      </div>
      <Card className="gap-0 overflow-hidden py-0">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-4">File</TableHead>
              <TableHead className="text-right">Tests</TableHead>
              <TableHead className="text-right">First</TableHead>
              <TableHead className="text-right">Kept</TableHead>
              <TableHead className="text-right">Change</TableHead>
              <TableHead className="pr-4">Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {batch.files.map((file) => (
              <TableRow key={file.source_file}>
                <TableCell className="pl-4">
                  <div className="flex max-w-[22rem] flex-col gap-0.5 lg:max-w-[28rem]">
                    {file.run_id ? (
                      <Link
                        to={`/runs/${encodeURIComponent(file.run_id)}`}
                        className="truncate font-mono text-xs underline-offset-4 hover:underline"
                      >
                        {file.source_file}
                      </Link>
                    ) : (
                      <span className="truncate font-mono text-xs">
                        {file.source_file}
                      </span>
                    )}
                    {file.error && (
                      <span className="truncate text-caption text-muted-foreground">
                        {file.error}
                      </span>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {file.tests_kept === null
                    ? "—"
                    : `${file.tests_first ?? 0} → ${file.tests_kept}`}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {percent(file.score_first)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {percent(file.score_kept)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {file.score_first !== null && file.score_kept !== null
                    ? signedPoints(file.score_kept - file.score_first)
                    : "—"}
                </TableCell>
                <TableCell className="pr-4">
                  <FileStatus status={file.status} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      <p className="text-caption text-muted-foreground">
        First is the first tests measured: the one-shot tests, or the existing
        ones when generation is skipped. Combined counts every mutant of the
        files scored in both columns.
      </p>
    </Page>
  );
}

function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <Card className="gap-1 py-5">
      <CardContent className="flex flex-col gap-1">
        <span className="text-caption text-muted-foreground">{label}</span>
        <span className="text-heading font-semibold tabular-nums">{value}</span>
        {note && (
          <span className="text-caption text-muted-foreground">{note}</span>
        )}
      </CardContent>
    </Card>
  );
}

function FileStatus({ status }: { status: BatchFile["status"] }) {
  if (status === "running") return <RunStatusBadge status="running" active />;
  if (status === "completed" || status === "failed" || status === "cancelled")
    return <RunStatusBadge status={status} />;
  return (
    <Badge variant="secondary">
      <StatusDot tone="muted" />
      {status === "skipped" ? "Skipped" : "Waiting"}
    </Badge>
  );
}
