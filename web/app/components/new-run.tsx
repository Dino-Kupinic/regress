import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Search, X } from "lucide-react";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { api, type ProjectInfo } from "~/lib/api";
import { fileName } from "~/lib/utils";

type DrawerContext = { open: (source?: string) => void; opened: boolean };
const Context = createContext<DrawerContext>({
  open: () => undefined,
  opened: false,
});
export const useNewRun = () => useContext(Context);

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
  const [model, setModel] = useState("");
  const [rounds, setRounds] = useState(1);
  const [baseline, setBaseline] = useState(false);
  const [generate, setGenerate] = useState(true);
  const [error, setError] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
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
    if (opened) {
      closeRef.current?.focus();
      const onKey = (event: KeyboardEvent) => {
        if (event.key === "Escape") setOpened(false);
      };
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }
  }, [opened]);
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
    setModel("");
    setBaseline(false);
    setGenerate(true);
    setRounds(1);
    setOpened(true);
  };
  const filtered =
    sources.data?.filter((item) =>
      item.path.toLowerCase().includes(search.toLowerCase()),
    ) ?? [];
  const command = `regress run ${source || "<source>"}${baseline ? " --baseline" : ""}${!generate ? " --no-generate" : ""} --rounds ${rounds} --yes`;
  return (
    <Context.Provider value={{ open, opened }}>
      {children}
      {opened && (
        <div className="drawer-layer">
          <aside
            className="drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="new-run-title"
          >
            <header className="drawer-header">
              <div>
                <h2 id="new-run-title">New run</h2>
                <p className="muted">
                  Generate tests, mutation-test them, improve from survivors.
                </p>
              </div>
              <Button
                ref={closeRef}
                variant="ghost"
                size="icon"
                aria-label="Close new run"
                onClick={() => setOpened(false)}
              >
                <X size={17} />
              </Button>
            </header>
            <div className="drawer-body">
              <section>
                <div className="row between">
                  <h3>Source file</h3>
                  <span className="muted small">
                    {sources.data?.length ?? 0} files
                  </span>
                </div>
                <label className="search-field" htmlFor="source-search">
                  <Search size={16} />
                  <Input
                    id="source-search"
                    className="pl-9"
                    aria-label="Search source files"
                    placeholder="Search files"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </label>
                <div className="file-picker">
                  {filtered.map((item) => (
                    <button
                      key={item.path}
                      type="button"
                      className={`file-option ${source === item.path ? "selected" : ""}`}
                      onClick={() => setSource(item.path)}
                    >
                      <span className="radio-mark" aria-hidden="true" />
                      <span className="mono grow">{item.path}</span>
                      <span className="muted small">{fileName(item.path)}</span>
                    </button>
                  ))}
                  {!sources.isLoading && !filtered.length && (
                    <p className="empty-inline">No matching source files.</p>
                  )}
                </div>
                {detail.data && (
                  <p className="small muted">
                    <span className="mono">{detail.data.test_file}</span>{" "}
                    {detail.data.test_file_exists
                      ? "will be extended"
                      : "will be created"}
                    .
                  </p>
                )}
              </section>
              <section>
                <h3>Options</h3>
                <div className="form-grid">
                  <label>
                    Model
                    <select
                      value={model}
                      onChange={(event) => setModel(event.target.value)}
                    >
                      <option value="">
                        {models.data?.default_model ?? "Configured default"}
                      </option>
                      {models.data?.models
                        .filter(
                          (entry) => entry.id !== models.data?.default_model,
                        )
                        .map((entry) => (
                          <option key={entry.id}>{entry.id}</option>
                        ))}
                    </select>
                  </label>
                  <label htmlFor="run-rounds">
                    Improvement rounds
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
                  </label>
                </div>
                <label className="toggle-row">
                  <span>
                    <strong>Measure baseline</strong>
                    <small>Mutation-test existing tests first</small>
                  </span>
                  <input
                    type="checkbox"
                    checked={baseline}
                    disabled={!detail.data?.test_file_exists}
                    onChange={(event) => setBaseline(event.target.checked)}
                  />
                </label>
                <label className="toggle-row">
                  <span>
                    <strong>Skip generation</strong>
                    <small>Improve the existing test file directly</small>
                  </span>
                  <input
                    type="checkbox"
                    checked={!generate}
                    disabled={!detail.data?.test_file_exists}
                    onChange={(event) => setGenerate(!event.target.checked)}
                  />
                </label>
              </section>
              <section>
                <h3>Pre-flight</h3>
                <div className="preflight">
                  {project?.packages.map((item) => (
                    <div key={item.name} className="row gap-sm">
                      <Check
                        size={14}
                        className={item.version ? "success" : "danger"}
                      />
                      <span className="mono">{item.name}</span>
                      <span className="push muted">
                        {item.version ?? "Missing"}
                      </span>
                    </div>
                  ))}
                  <div className="row gap-sm">
                    <Check
                      size={14}
                      className={project?.api_key_set ? "success" : "danger"}
                    />
                    <span>OPENAI_API_KEY</span>
                    <span className="push muted">
                      {project?.api_key_set ? "Set on server" : "Not set"}
                    </span>
                  </div>
                </div>
                {!project?.ready && (
                  <p className="error-text small">
                    {project?.problems.join(" ")}
                  </p>
                )}
              </section>
            </div>
            <footer className="drawer-footer">
              <div className="command row">
                <code>{command}</code>
                <button
                  type="button"
                  onClick={() => navigator.clipboard.writeText(command)}
                  aria-label="Copy command"
                >
                  <Copy size={15} />
                </button>
              </div>
              {error && (
                <p className="error-text" role="alert">
                  {error}
                </p>
              )}
              <div className="row between">
                <span className="small muted">One run at a time</span>
                <div className="row gap-sm">
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
                        model: model || undefined,
                        rounds,
                        baseline: baseline && !!detail.data?.test_file_exists,
                        generate: generate || !detail.data?.test_file_exists,
                      })
                    }
                  >
                    {run.isPending ? "Starting…" : "Start run"}
                  </Button>
                </div>
              </div>
            </footer>
          </aside>
        </div>
      )}
    </Context.Provider>
  );
}
