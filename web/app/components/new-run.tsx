import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Check, CircleAlert, Copy, Search, X } from "lucide-react";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useState,
} from "react";
import { Link, useNavigate } from "react-router";
import { matchSources, SOURCE_LIMIT } from "~/components/source-picker";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
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
/** The API's limit for one multi-file run. */
const MAX_FILES = 100;

export function NewRunProvider({
  children,
  project,
}: {
  children: ReactNode;
  project?: ProjectInfo;
}) {
  const [opened, setOpened] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
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
  // One lookup per selected file (bounded by MAX_FILES): which test file it extends, and whether it exists.
  const details = useQueries({
    queries: selected.map((path) => ({
      queryKey: ["source", path],
      queryFn: () => api.source(path),
      enabled: opened,
    })),
  });
  const single = selected.length === 1;
  const detail = single ? details[0] : undefined;
  const loaded = details.every((item) => item.data || item.isError);
  const failed = selected.filter((_, index) => details[index]?.isError);
  const withTests = details.filter(
    (item) => item.data?.test_file_exists,
  ).length;
  const hasTests = withTests > 0;
  const run = useMutation({
    mutationFn: () => {
      const options = {
        model: model === DEFAULT_MODEL ? undefined : model,
        rounds,
        baseline: baseline && hasTests,
        generate: generate || !hasTests,
      };
      return single
        ? api.startRun({ source: selected[0], ...options }).then((result) => ({
            to: `/runs/${encodeURIComponent(result.summary.id)}`,
          }))
        : api.startBatch({ sources: selected, ...options }).then((result) => ({
            to: `/batches/${encodeURIComponent(result.id)}`,
          }));
    },
    onSuccess: async ({ to }) => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      await queryClient.invalidateQueries({ queryKey: ["project"] });
      setOpened(false);
      navigate(to);
    },
    onError: (failure) => setError(failure.message),
  });
  useEffect(() => {
    if (loaded && !hasTests) {
      setBaseline(false);
      setGenerate(true);
    }
  }, [loaded, hasTests]);
  const toggle = (path: string, checked: boolean) =>
    setSelected((old) =>
      checked
        ? old.includes(path) || old.length >= MAX_FILES
          ? old
          : [...old, path]
        : old.filter((item) => item !== path),
    );
  const open = (initial?: string) => {
    setSelected(initial ? [initial] : []);
    setSearch("");
    setError("");
    setModel(DEFAULT_MODEL);
    setBaseline(false);
    setGenerate(true);
    setRounds(1);
    setOpened(true);
  };
  const matches = matchSources(sources.data ?? [], search);
  // Render a bounded list, keeping the selected files in it, first.
  const filtered = matches.slice(0, SOURCE_LIMIT);
  const pinned = (sources.data ?? []).filter(
    (item) => selected.includes(item.path) && !filtered.includes(item),
  );
  const shown = [...pinned, ...filtered];
  const hidden = matches.length - filtered.length;
  const allShown =
    filtered.length > 0 &&
    filtered.every((item) => selected.includes(item.path));
  const selectShown = (checked: boolean) =>
    setSelected((old) =>
      checked
        ? [
            ...old,
            ...filtered
              .map((item) => item.path)
              .filter((path) => !old.includes(path)),
          ].slice(0, MAX_FILES)
        : old.filter((path) => !filtered.some((item) => item.path === path)),
    );
  const command = [
    "regress run",
    selected.length ? selected.join(" ") : "<source>",
    model !== DEFAULT_MODEL && `--model ${model}`,
    baseline && hasTests && "--baseline",
    !generate && hasTests && "--no-generate",
    `--rounds ${rounds}`,
    "--yes",
  ]
    .filter(Boolean)
    .join(" ");
  const busy = project?.active_run ?? null;
  const blocked =
    busy || project?.active_batch
      ? "Another run is in progress"
      : project && !project.ready
        ? "Finish project setup first"
        : !selected.length
          ? "Choose source files"
          : failed.length
            ? `${failed.length === 1 ? failed[0] : `${failed.length} files`} can't be run`
            : null;
  const optionNote =
    !single && selected.length && hasTests && withTests < selected.length
      ? `${withTests} of ${selected.length} files have tests`
      : null;
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
                <h3 className="font-medium">Source files</h3>
                <span className="text-caption text-muted-foreground tabular-nums">
                  {search
                    ? `${matches.length} of ${sources.data?.length ?? 0}`
                    : `${sources.data?.length ?? 0} files`}
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
              <div className="flex min-h-8 items-center justify-between gap-3">
                <Field orientation="horizontal" className="w-auto">
                  <Checkbox
                    id="select-shown"
                    checked={
                      allShown
                        ? true
                        : filtered.some((item) => selected.includes(item.path))
                          ? "indeterminate"
                          : false
                    }
                    disabled={!filtered.length}
                    onCheckedChange={(checked) => selectShown(checked === true)}
                  />
                  <FieldLabel
                    htmlFor="select-shown"
                    className="text-caption font-normal text-muted-foreground"
                  >
                    {search ? "Select all matches shown" : "Select all shown"}
                  </FieldLabel>
                </Field>
                <span className="flex items-center gap-2 text-caption tabular-nums">
                  {selected.length
                    ? `${selected.length} selected`
                    : "None selected"}
                  {selected.length > 0 && (
                    <Button
                      variant="ghost"
                      size="xs"
                      onClick={() => setSelected([])}
                    >
                      Clear
                    </Button>
                  )}
                </span>
              </div>
              <div className="max-h-60 overflow-y-auto rounded-lg border scroll-fade">
                {sources.isPending ? (
                  <div className="flex flex-col gap-3 p-4">
                    {[0, 1, 2].map((index) => (
                      <Skeleton key={index} className="h-5" />
                    ))}
                  </div>
                ) : shown.length ? (
                  <ul aria-label="Source files" className="divide-y">
                    {shown.map((item) => (
                      <Field
                        key={item.path}
                        orientation="horizontal"
                        role="listitem"
                        className="px-4 py-3 transition-colors hover:bg-accent has-data-checked:bg-accent"
                      >
                        <Checkbox
                          id={`source-${item.path}`}
                          checked={selected.includes(item.path)}
                          disabled={
                            !selected.includes(item.path) &&
                            selected.length >= MAX_FILES
                          }
                          onCheckedChange={(checked) =>
                            toggle(item.path, checked === true)
                          }
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
                  </ul>
                ) : (
                  <p className="p-4 text-sm text-muted-foreground">
                    No matching source files.
                  </p>
                )}
                {hidden > 0 && (
                  <p className="border-t px-4 py-2.5 text-caption text-muted-foreground">
                    {hidden} more {hidden === 1 ? "match" : "matches"}. Keep
                    typing to narrow the list.
                  </p>
                )}
              </div>
              {failed.length > 0 && (
                <p className="text-caption text-destructive">
                  {failed.length === 1 && single
                    ? details[0]?.error?.message
                    : `Can't run ${failed.join(", ")}.`}
                </p>
              )}
              {detail?.data && (
                <p className="text-caption text-muted-foreground motion-safe:animate-in fade-in-0">
                  <span className="font-mono text-foreground">
                    {detail.data.test_file}
                  </span>{" "}
                  will be {hasTests ? "extended" : "created"}.
                </p>
              )}
              {!single && selected.length > 1 && loaded && (
                <p className="text-caption text-muted-foreground motion-safe:animate-in fade-in-0">
                  The files run one after another, each as its own run.{" "}
                  {withTests} of {selected.length} already{" "}
                  {withTests === 1 ? "has" : "have"} tests.
                  {selected.length >= MAX_FILES &&
                    ` At most ${MAX_FILES} files per run.`}
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
                      {optionNote && ` (${optionNote})`}
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
                      {optionNote && `; files without tests are skipped`}
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
                  label={project?.api_key_name ?? "API key"}
                  value={
                    project?.api_key_name === null
                      ? "Not needed"
                      : project?.api_key_set
                        ? "Set on server"
                        : "Not set"
                  }
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
                {busy || project?.active_batch ? (
                  <>
                    Another run is in progress.{" "}
                    <Link
                      to={
                        project?.active_batch
                          ? `/batches/${encodeURIComponent(project.active_batch)}`
                          : `/runs/${encodeURIComponent(busy ?? "")}`
                      }
                      className="text-foreground underline underline-offset-4"
                      onClick={() => setOpened(false)}
                    >
                      Open it
                    </Link>
                  </>
                ) : (
                  (blocked ??
                  (single ? "One run at a time" : "One file at a time"))
                )}
              </span>
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setOpened(false)}>
                  Cancel
                </Button>
                <Button
                  disabled={!!blocked || !loaded || run.isPending}
                  onClick={() => run.mutate()}
                >
                  {run.isPending && <Spinner data-icon="inline-start" />}
                  {run.isPending
                    ? "Starting"
                    : single || !selected.length
                      ? "Start run"
                      : `Run ${selected.length} files`}
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
