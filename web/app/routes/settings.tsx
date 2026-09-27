import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { type MetaFunction, useLoaderData } from "react-router";
import { toast } from "sonner";
import { Page, PageHeader, SectionHeader } from "~/components/page";
import { StatusDot } from "~/components/status-dot";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { RadioGroup, RadioGroupItem } from "~/components/ui/radio-group";
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
import { Switch } from "~/components/ui/switch";
import { api, type Settings } from "~/lib/api";
import { cn, pageTitle } from "~/lib/utils";

export const meta: MetaFunction = () => [{ title: pageTitle("Settings") }];

export async function clientLoader() {
  const [settings, project] = await Promise.all([
    api.settings(),
    api.project(),
  ]);
  return { settings, project };
}

type NumberKey =
  | "rounds"
  | "max_repairs"
  | "max_mutants"
  | "llm_timeout"
  | "vitest_timeout"
  | "stryker_timeout";

const numberFields: {
  key: NumberKey;
  label: string;
  min: number;
  max: number;
  help: string;
}[] = [
  {
    key: "rounds",
    label: "Improvement rounds",
    min: 0,
    max: 5,
    help: "0–5 after the first mutation run",
  },
  {
    key: "max_repairs",
    label: "Repair attempts",
    min: 0,
    max: 5,
    help: "0–5 retries for a rejected file",
  },
  {
    key: "max_mutants",
    label: "Mutants per round",
    min: 1,
    max: 200,
    help: "1–200 sent to the model",
  },
  {
    key: "llm_timeout",
    label: "Model silence",
    min: 30,
    max: 3600,
    help: "Seconds before one retry",
  },
  {
    key: "vitest_timeout",
    label: "Vitest timeout",
    min: 10,
    max: 10000,
    help: "Seconds per validation",
  },
  {
    key: "stryker_timeout",
    label: "Stryker timeout",
    min: 30,
    max: 10000,
    help: "Seconds per mutation run",
  },
];

const layers = [
  "Built-in defaults",
  "Personal config",
  "regress.toml",
  "Environment",
  "Options on a run",
];

export default function SettingsPage() {
  const initial = useLoaderData<typeof clientLoader>();
  const client = useQueryClient();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: api.settings,
    initialData: initial.settings,
  }).data;
  const project = useQuery({
    queryKey: ["project"],
    queryFn: api.project,
    initialData: initial.project,
  }).data;
  const models = useQuery({
    queryKey: ["models"],
    queryFn: () => api.models(),
  });
  const [draft, setDraft] = useState<Settings>(settings.effective);
  useEffect(() => setDraft(settings.effective), [settings]);
  const changed = Object.entries(draft).filter(
    ([key, value]) => value !== settings.effective[key as keyof Settings],
  );
  const save = useMutation({
    mutationFn: () =>
      api.saveSettings(Object.fromEntries(changed) as Partial<Settings>),
    onSuccess: (response) => {
      client.setQueryData(["settings"], response);
      toast.success("Settings saved");
    },
  });
  const update = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    setDraft((old) => ({ ...old, [key]: value }));
  const modelList = models.data?.models ?? [
    { id: draft.model, created_date: "", newest: false, default: true },
  ];
  return (
    <Page>
      <PageHeader
        title="Settings"
        description="Defaults for every run. Options chosen on a single run still win."
        actions={
          <span className="font-mono text-caption text-muted-foreground">
            {settings.user_config_path}
          </span>
        }
      />
      <div className="grid items-start gap-10 lg:grid-cols-[minmax(0,1.9fr)_minmax(280px,0.9fr)]">
        <div className="flex min-w-0 flex-col gap-12">
          <section className="flex flex-col gap-5">
            <SectionHeader
              title="Model"
              description="Which OpenAI model writes the tests"
              action={
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={models.isFetching}
                  onClick={() => models.refetch()}
                >
                  {models.isFetching && <Spinner data-icon="inline-start" />}
                  Refresh models
                </Button>
              }
            />
            <Card className="gap-0 overflow-hidden py-0">
              {models.isPending ? (
                <div className="flex flex-col gap-3 p-4">
                  {[0, 1, 2, 3].map((index) => (
                    <Skeleton key={index} className="h-6" />
                  ))}
                </div>
              ) : (
                <RadioGroup
                  value={draft.model}
                  onValueChange={(value) => update("model", value)}
                  className="max-h-[420px] gap-0 divide-y overflow-y-auto scroll-fade"
                  aria-label="Default model"
                >
                  {modelList.map((item) => (
                    <Field
                      key={item.id}
                      orientation="horizontal"
                      className="px-4 py-3 transition-colors has-data-checked:bg-accent"
                    >
                      <RadioGroupItem value={item.id} id={`model-${item.id}`} />
                      <FieldLabel
                        htmlFor={`model-${item.id}`}
                        className="font-mono font-normal"
                      >
                        {item.id}
                      </FieldLabel>
                      <span className="text-caption text-muted-foreground tabular-nums">
                        {item.created_date}
                      </span>
                      <span className="flex w-36 shrink-0 justify-end gap-1">
                        {item.default && <Badge>Default</Badge>}
                        {item.newest && <Badge variant="outline">Newest</Badge>}
                      </span>
                    </Field>
                  ))}
                </RadioGroup>
              )}
            </Card>
            <FieldGroup>
              <Field orientation="horizontal">
                <FieldContent>
                  <FieldLabel htmlFor="ask-model">
                    Ask which model to use before each run
                  </FieldLabel>
                  <FieldDescription>
                    Applies to CLI use; this web app always shows the choice.
                  </FieldDescription>
                </FieldContent>
                <Switch
                  id="ask-model"
                  checked={draft.ask_model}
                  onCheckedChange={(checked) => update("ask_model", checked)}
                />
              </Field>
              <Field className="max-w-xs">
                <FieldLabel htmlFor="reasoning-effort">
                  Reasoning effort
                </FieldLabel>
                <Select
                  value={draft.reasoning_effort ?? "default"}
                  onValueChange={(value) =>
                    update(
                      "reasoning_effort",
                      value === "default" ? null : value,
                    )
                  }
                >
                  <SelectTrigger id="reasoning-effort" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value="default">Default</SelectItem>
                      <SelectItem value="low">Low</SelectItem>
                      <SelectItem value="medium">Medium</SelectItem>
                      <SelectItem value="high">High</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
            </FieldGroup>
          </section>
          <section className="flex flex-col gap-5">
            <SectionHeader
              title="Run defaults & timeouts"
              description="How hard each run works, and when to give up"
            />
            <FieldGroup className="grid gap-x-4 gap-y-6 sm:grid-cols-2 xl:grid-cols-3">
              {numberFields.map(({ key, label, min, max, help }) => (
                <Field key={key}>
                  <FieldLabel htmlFor={`setting-${key}`}>{label}</FieldLabel>
                  <Input
                    id={`setting-${key}`}
                    type="number"
                    min={min}
                    max={max}
                    value={draft[key]}
                    onChange={(event) =>
                      update(key, Number(event.target.value))
                    }
                  />
                  <FieldDescription>{help}</FieldDescription>
                </Field>
              ))}
            </FieldGroup>
            <Field className="max-w-xs">
              <FieldLabel htmlFor="runner">Runner</FieldLabel>
              <Select
                value={draft.runner}
                onValueChange={(value) =>
                  update("runner", value as Settings["runner"])
                }
              >
                <SelectTrigger id="runner" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value="auto">Auto</SelectItem>
                    <SelectItem value="bun">Bun</SelectItem>
                    <SelectItem value="npx">npx</SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
              <FieldDescription>
                Auto uses Bun and falls back to npx.
              </FieldDescription>
            </Field>
          </section>
        </div>
        <aside className="flex flex-col gap-4 lg:sticky lg:top-4">
          <Card
            className={cn(
              "transition-shadow duration-300",
              changed.length && "ring-foreground",
            )}
          >
            <CardHeader>
              <CardTitle>
                {changed.length
                  ? `${changed.length} unsaved ${changed.length === 1 ? "change" : "changes"}`
                  : "All changes saved"}
              </CardTitle>
              <CardDescription>
                {changed.length
                  ? changed.map(([key]) => key.replaceAll("_", " ")).join(" · ")
                  : "Your personal defaults are up to date."}
              </CardDescription>
            </CardHeader>
            {save.isError && (
              <CardContent>
                <Alert variant="destructive">
                  <CircleAlert />
                  <AlertTitle>Could not save</AlertTitle>
                  <AlertDescription>{save.error.message}</AlertDescription>
                </Alert>
              </CardContent>
            )}
            <CardFooter className="justify-end gap-2">
              <Button
                variant="ghost"
                disabled={!changed.length}
                onClick={() => setDraft(settings.effective)}
              >
                Discard
              </Button>
              <Button
                disabled={!changed.length || save.isPending}
                onClick={() => save.mutate()}
              >
                {save.isPending && <Spinner data-icon="inline-start" />}
                Save changes
              </Button>
            </CardFooter>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Where settings come from</CardTitle>
              <CardDescription>Later layers win</CardDescription>
            </CardHeader>
            <CardContent>
              <ol className="flex flex-col">
                {layers.map((label, index) => (
                  <li
                    key={label}
                    className="flex items-center gap-3 border-t py-2.5 text-sm first:border-t-0"
                  >
                    <span className="font-mono text-xs text-muted-foreground">
                      {index + 1}
                    </span>
                    <span className="flex-1">{label}</span>
                    {label === "Personal config" && (
                      <Badge variant="secondary">Editable here</Badge>
                    )}
                  </li>
                ))}
              </ol>
            </CardContent>
            <CardFooter className="text-caption text-muted-foreground">
              Model source: {settings.model_source}. Project overrides remain in
              effect after a personal save.
            </CardFooter>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Project &amp; toolchain</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2.5 text-sm">
              {project.packages.map((item) => (
                <div key={item.name} className="flex items-center gap-2">
                  <StatusDot tone={item.version ? "success" : "destructive"} />
                  <span className="flex-1 truncate font-mono text-xs">
                    {item.name}
                  </span>
                  <span className="text-caption text-muted-foreground tabular-nums">
                    {item.version ?? "Missing"}
                  </span>
                </div>
              ))}
              <Separator className="my-1" />
              <div className="flex items-center gap-2">
                <StatusDot
                  tone={project.api_key_set ? "success" : "destructive"}
                />
                <span className="flex-1 font-mono text-xs">OPENAI_API_KEY</span>
                <span className="text-caption text-muted-foreground">
                  {project.api_key_set ? "Set on server" : "Not set"}
                </span>
              </div>
            </CardContent>
          </Card>
        </aside>
      </div>
    </Page>
  );
}
