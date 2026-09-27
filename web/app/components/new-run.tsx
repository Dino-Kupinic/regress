import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, Copy, Search, X } from "lucide-react";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useState,
} from "react";
import { useNavigate } from "react-router";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "~/components/ui/input-group";
import { RadioGroup, RadioGroupItem } from "~/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import { api, type ProjectInfo } from "~/lib/api";
import { copyToClipboard } from "~/lib/clipboard";
import { fileName } from "~/lib/utils";

type DrawerContext = { open: (source?: string) => void; opened: boolean };
const Context = createContext<DrawerContext>({
  open: () => undefined,
  opened: false,
});
export const useNewRun = () => useContext(Context);

const DEFAULT_MODEL = "__default__";

export function NewRunProvider({
  children,
  project,
}: {
  children: ReactNode;
  project?: ProjectInfo;
}) {
  const [opened, setOpened] = useState(false);
  const [source, setSource] = useState("");
  const [search, setSearch] = useState("");
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [rounds, setRounds] = useState(1);
  const [baseline, setBaseline] = useState(false);
  const [generate, setGenerate] = useState(true);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: api.sources,
    enabled: opened,
  });
  const models = useQuery({
    queryKey: ["models"],
    queryFn: () => api.models(),
    enabled: opened,
  });
  const detail = useQuery({
    queryKey: ["source", source],
    queryFn: () => api.source(source),
    enabled: opened && !!source,
  });
  const run = useMutation({
    mutationFn: api.startRun,
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      setOpened(false);
      navigate(`/runs/${encodeURIComponent(result.summary.id)}`);
    },
    onError: (failure) => setError(failure.message),
  });
  useEffect(() => {
    if (detail.data && !detail.data.test_file_exists) {
      setBaseline(false);
      setGenerate(true);
    }
  }, [detail.data]);
  const open = (initial?: string) => {
    setSource(initial ?? "");
    setSearch("");
    setError("");
    setModel(DEFAULT_MODEL);
    setBaseline(false);
    setGenerate(true);
    setRounds(1);
    setOpened(true);
  };
  const filtered =
    sources.data?.filter((item) =>
      item.path.toLowerCase().includes(search.toLowerCase()),
    ) ?? [];
  const hasTests = !!detail.data?.test_file_exists;
  const command = `regress run ${source || "<source>"}${baseline ? " --baseline" : ""}${!generate ? " --no-generate" : ""} --rounds ${rounds} --yes`;
  return (
    <Context.Provider value={{ open, opened }}>
      {children}
      <Sheet open={opened} onOpenChange={setOpened}>
        <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-lg">
          <SheetHeader className="border-b p-6">
            <SheetTitle className="text-subheading">New run</SheetTitle>
            <SheetDescription>
              Generate tests, mutation-test them, improve from survivors.
            </SheetDescription>
          </SheetHeader>
          <div className="flex flex-1 flex-col gap-8 overflow-y-auto p-6 scroll-fade">
            <section className="flex flex-col gap-3">
              <div className="flex items-baseline justify-between">
                <h3 className="font-medium">Source file</h3>
                <span className="text-caption text-muted-foreground tabular-nums">
                  {sources.data?.length ?? 0} files
                </span>
              </div>
              <InputGroup>
                <InputGroupAddon>
                  <Search />
                </InputGroupAddon>
                <InputGroupInput
                  aria-label="Search source files"
                  placeholder="Search files"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </InputGroup>
              <div className="max-h-60 overflow-y-auto rounded-lg border scroll-fade">
                {sources.isPending ? (
                  <div className="flex flex-col gap-3 p-4">
                    {[0, 1, 2].map((index) => (
                      <Skeleton key={index} className="h-5" />
                    ))}
                  </div>
                ) : filtered.length ? (
                  <RadioGroup
                    value={source}
                    onValueChange={setSource}
                    aria-label="Source file"
                    className="gap-0 divide-y"
                  >
                    {filtered.map((item) => (
                      <Field
                        key={item.path}
                        orientation="horizontal"
                        className="px-4 py-3 transition-colors hover:bg-accent has-data-checked:bg-accent"
                      >
                        <RadioGroupItem
                          value={item.path}
                          id={`source-${item.path}`}
                        />
                        <FieldLabel
                          htmlFor={`source-${item.path}`}
                          className="truncate font-mono text-xs font-normal"
                        >
                          {item.path}
                        </FieldLabel>
                        <span className="text-caption text-muted-foreground">
                          {fileName(item.path)}
                        </span>
                      </Field>
                    ))}
                  </RadioGroup>
                ) : (
                  <p className="p-4 text-sm text-muted-foreground">
                    No matching source files.
                  </p>
                )}
              </div>
              {detail.data && (
                <p className="text-caption text-muted-foreground motion-safe:animate-in fade-in-0">
                  <span className="font-mono text-foreground">
                    {detail.data.test_file}
                  </span>{" "}
                  will be {hasTests ? "extended" : "created"}.
                </p>
              )}
            </section>
            <section className="flex flex-col gap-4">
              <h3 className="font-medium">Options</h3>
              <FieldGroup>
                <div className="grid grid-cols-[1fr_9rem] gap-3">
                  <Field>
                    <FieldLabel htmlFor="run-model">Model</FieldLabel>
                    <Select value={model} onValueChange={setModel}>
                      <SelectTrigger id="run-model" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          <SelectItem value={DEFAULT_MODEL}>
                            {models.data?.default_model ?? "Configured default"}
                          </SelectItem>
                          {models.data?.models
                            .filter(
                              (entry) =>
                                entry.id !== models.data?.default_model,
                            )
                            .map((entry) => (
                              <SelectItem key={entry.id} value={entry.id}>
                                {entry.id}
                              </SelectItem>
                            ))}
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="run-rounds">Rounds</FieldLabel>
                    <Input
                      id="run-rounds"
                      type="number"
                      min="0"
                      max="5"
                      value={rounds}
                      onChange={(event) =>
                        setRounds(
                          Math.min(5, Math.max(0, Number(event.target.value))),
                        )
                      }
                    />
                  </Field>
                </div>
                <Field orientation="horizontal" data-disabled={!hasTests}>
                  <FieldContent>
                    <FieldLabel htmlFor="run-baseline">
                      Measure baseline
                    </FieldLabel>
                    <FieldDescription>
                      Mutation-test the existing tests first
                    </FieldDescription>
                  </FieldContent>
                  <Switch
                    id="run-baseline"
                    checked={baseline}
                    disabled={!hasTests}
                    onCheckedChange={setBaseline}
                  />
                </Field>
                <Field orientation="horizontal" data-disabled={!hasTests}>
                  <FieldContent>
                    <FieldLabel htmlFor="run-skip-generation">
                      Skip generation
                    </FieldLabel>
                    <FieldDescription>
                      Improve the existing test file directly
                    </FieldDescription>
                  </FieldContent>
                  <Switch
                    id="run-skip-generation"
                    checked={!generate}
                    disabled={!hasTests}
                    onCheckedChange={(checked) => setGenerate(!checked)}
                  />
                </Field>
              </FieldGroup>
            </section>
            <section className="flex flex-col gap-3">
              <h3 className="font-medium">Pre-flight</h3>
              <ul className="flex flex-col divide-y rounded-lg bg-muted/60 text-sm">
                {project?.packages.map((item) => (
                  <PreflightRow
                    key={item.name}
                    ok={!!item.version}
                    label={item.name}
                    value={item.version ?? "Missing"}
                  />
                ))}
                <PreflightRow
                  ok={!!project?.api_key_set}
                  label="OPENAI_API_KEY"
                  value={project?.api_key_set ? "Set on server" : "Not set"}
                />
              </ul>
              {project && !project.ready && (
                <Alert variant="destructive">
                  <CircleAlert />
                  <AlertTitle>Not ready to run</AlertTitle>
                  <AlertDescription>
                    {project.problems.join(" ")}
                  </AlertDescription>
                </Alert>
              )}
            </section>
          </div>
          <SheetFooter className="gap-4 border-t p-6">
            <InputGroup>
              <InputGroupInput
                readOnly
                value={command}
                aria-label="Equivalent CLI command"
                className="font-mono text-xs"
              />
              <InputGroupAddon align="inline-end">
                <InputGroupButton
                  size="icon-xs"
                  aria-label="Copy command"
                  onClick={() => copyToClipboard(command, "Command copied")}
                >
                  <Copy />
                </InputGroupButton>
              </InputGroupAddon>
            </InputGroup>
            {error && (
              <Alert variant="destructive">
                <CircleAlert />
                <AlertTitle>Could not start the run</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <div className="flex items-center justify-between gap-3">
              <span className="text-caption text-muted-foreground">
                One run at a time
              </span>
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setOpened(false)}>
                  Cancel
                </Button>
                <Button
                  disabled={
                    !source ||
                    !detail.data ||
                    !project?.ready ||
                    run.isPending ||
                    !!project.active_run
                  }
                  onClick={() =>
                    run.mutate({
                      source,
                      model: model === DEFAULT_MODEL ? undefined : model,
                      rounds,
                      baseline: baseline && hasTests,
                      generate: generate || !hasTests,
                    })
                  }
                >
                  {run.isPending && <Spinner data-icon="inline-start" />}
                  {run.isPending ? "Starting" : "Start run"}
                </Button>
              </div>
            </div>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </Context.Provider>
  );
}

function PreflightRow({
  ok,
  label,
  value,
}: {
  ok: boolean;
  label: string;
  value: string;
}) {
  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      {ok ? (
        <Check className="size-4 text-success" />
      ) : (
        <X className="size-4 text-destructive" />
      )}
      <span className="flex-1 truncate font-mono text-xs">{label}</span>
      <span className="text-caption text-muted-foreground">{value}</span>
    </li>
  );
}
