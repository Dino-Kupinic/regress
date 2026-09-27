import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, Copy, X } from "lucide-react";
import type { ReactNode } from "react";
import { type MetaFunction, useLoaderData } from "react-router";
import { toast } from "sonner";
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
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "~/components/ui/input-group";
import { Spinner } from "~/components/ui/spinner";
import { api } from "~/lib/api";
import { copyToClipboard } from "~/lib/clipboard";
import { cn, pageTitle } from "~/lib/utils";

export const meta: MetaFunction = () => [{ title: pageTitle("Project setup") }];

export async function clientLoader() {
  const [project, sources] = await Promise.all([api.project(), api.sources()]);
  return { project, sources };
}

const command = "uv run regress init --yes";

export default function Setup() {
  const initial = useLoaderData<typeof clientLoader>();
  const client = useQueryClient();
  const project = useQuery({
    queryKey: ["project"],
    queryFn: api.project,
    initialData: initial.project,
  }).data;
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: api.sources,
    initialData: initial.sources,
  }).data;
  const init = useMutation({
    mutationFn: api.initProject,
    onSuccess: (result) => {
      client.setQueryData(["project"], result.project);
      client.invalidateQueries({ queryKey: ["project"] });
      toast.success(
        result.installed.length
          ? `Installed ${result.installed.length} packages`
          : "Project checked",
      );
    },
  });
  const missing = project.packages.filter((item) => !item.version);
  const checks: [string, boolean | null][] = [
    ["Project package.json", true],
    ["Vitest + Stryker", !!project.toolchain],
    ["regress.toml", project.config_file ? true : null],
    ["OpenAI API key", project.api_key_set ? true : null],
  ];
  return (
    <Page>
      <PageHeader
        title={`Set up ${project.name}`}
        description={<span className="font-mono">{project.root}</span>}
        actions={<Badge variant="outline">Same as regress init</Badge>}
      />
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,2.2fr)_minmax(280px,1fr)]">
        <ol className="flex flex-col gap-4">
          <Step
            number={1}
            title="Install test dependencies"
            description="Regress runs tests with Vitest and mutates code with StrykerJS."
            status={missing.length ? `${missing.length} to install` : "Ready"}
            done={!missing.length}
          >
            <CardContent className="flex flex-col gap-4">
              <ul className="flex flex-col divide-y rounded-lg border">
                {project.packages.map((item) => (
                  <li
                    key={item.name}
                    className="flex items-center gap-3 px-4 py-2.5 text-sm"
                  >
                    {item.version ? (
                      <Check className="size-4 text-success" />
                    ) : (
                      <span className="size-4 rounded-full border border-dashed border-muted-foreground" />
                    )}
                    <span className="flex-1 font-mono text-xs">
                      {item.name}
                    </span>
                    <span className="text-caption text-muted-foreground tabular-nums">
                      {item.version ?? "Missing"}
                    </span>
                  </li>
                ))}
              </ul>
              <InputGroup>
                <InputGroupInput
                  readOnly
                  value={command}
                  aria-label="Setup command"
                  className="font-mono text-xs"
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupButton
                    size="icon-xs"
                    aria-label="Copy setup command"
                    onClick={() => copyToClipboard(command, "Command copied")}
                  >
                    <Copy />
                  </InputGroupButton>
                </InputGroupAddon>
              </InputGroup>
              {init.isError && (
                <Alert variant="destructive">
                  <CircleAlert />
                  <AlertTitle>Setup failed</AlertTitle>
                  <AlertDescription>{init.error.message}</AlertDescription>
                </Alert>
              )}
            </CardContent>
            <CardFooter className="gap-3">
              <Button
                disabled={init.isPending || !!project.active_run}
                onClick={() => init.mutate()}
              >
                {init.isPending && <Spinner data-icon="inline-start" />}
                {init.isPending
                  ? "Installing"
                  : missing.length
                    ? `Install ${missing.length} packages`
                    : "Check and write config"}
              </Button>
              <span
                className={cn(
                  "text-caption text-muted-foreground",
                  init.isPending && "shimmer",
                )}
              >
                This may take several minutes.
              </span>
            </CardFooter>
          </Step>
          <Step
            number={2}
            title="Write project config"
            description="Share run defaults with the team."
            status={project.config_file ? "Written" : "After install"}
            done={!!project.config_file}
          >
            <CardContent className="flex flex-col gap-3">
              <pre className="rounded-lg bg-muted px-4 py-3 font-mono text-xs leading-relaxed">{`# regress.toml\nrounds = 1\nmax_repairs = 2\nmax_mutants = 40\nrunner = "auto"`}</pre>
              <p className="text-caption text-muted-foreground">
                The setup action writes{" "}
                <code className="font-mono">regress.toml</code> when absent. Run
                reports stay under the ignored{" "}
                <code className="font-mono">.regress/</code> directory.
              </p>
            </CardContent>
          </Step>
          <Step
            number={3}
            title="Connect OpenAI"
            description="Keep the API key in the Python server environment."
            status={project.api_key_set ? "Set" : "Not set"}
            done={project.api_key_set}
          >
            <CardContent>
              <p className="text-sm leading-relaxed">
                Add <code className="font-mono">OPENAI_API_KEY</code> to the
                server environment or to the repository's{" "}
                <code className="font-mono">.env</code> file, then restart{" "}
                <code className="font-mono">regress serve</code>. The web app
                does not store or display the key.
              </p>
            </CardContent>
          </Step>
        </ol>
        <aside className="flex flex-col gap-4 lg:sticky lg:top-4">
          <Card>
            <CardHeader>
              <CardTitle>Readiness</CardTitle>
              <CardAction>
                <Badge variant={project.ready ? "default" : "outline"}>
                  {project.ready ? "Ready" : "Not ready"}
                </Badge>
              </CardAction>
            </CardHeader>
            <CardContent>
              <ul className="flex flex-col">
                {checks.map(([label, ok]) => (
                  <li
                    key={label}
                    className="flex items-center justify-between border-t py-2.5 text-sm first:border-t-0"
                  >
                    <span>{label}</span>
                    {ok === true ? (
                      <Check className="size-4 text-success" />
                    ) : ok === false ? (
                      <X className="size-4 text-destructive" />
                    ) : (
                      <span className="size-4 rounded-full border border-dashed border-muted-foreground" />
                    )}
                  </li>
                ))}
              </ul>
            </CardContent>
            {!project.ready && (
              <CardFooter className="text-caption text-destructive">
                {project.problems.join(" ")}
              </CardFooter>
            )}
          </Card>
          <Card className="gap-0 py-0">
            <CardHeader className="border-b py-4">
              <CardTitle>Files you can test</CardTitle>
              <CardDescription>{sources.length} found</CardDescription>
            </CardHeader>
            <ul className="max-h-80 overflow-y-auto py-2 scroll-fade">
              {sources.map((item) => (
                <li key={item.path} className="px-6 py-1.5 font-mono text-xs">
                  {item.path}
                </li>
              ))}
            </ul>
          </Card>
        </aside>
      </div>
    </Page>
  );
}

function Step({
  number,
  title,
  description,
  status,
  done,
  children,
}: {
  number: number;
  title: string;
  description: string;
  status: string;
  done: boolean;
  children: ReactNode;
}) {
  return (
    <li>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            <span
              className={cn(
                "flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium transition-colors",
                done
                  ? "border-foreground bg-foreground text-background"
                  : "text-muted-foreground",
              )}
            >
              {done ? <Check className="size-3.5" /> : number}
            </span>
            {title}
          </CardTitle>
          <CardDescription className="pl-10">{description}</CardDescription>
          <CardAction>
            <Badge variant={done ? "secondary" : "outline"}>{status}</Badge>
          </CardAction>
        </CardHeader>
        {children}
      </Card>
    </li>
  );
}
