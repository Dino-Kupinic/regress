import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useLoaderData } from "react-router";
import { Button } from "~/components/ui/button";
import { Card } from "~/components/ui/card";
import { Input } from "~/components/ui/input";
import { api, type Settings } from "~/lib/api";

export async function clientLoader() {
  const [settings, project] = await Promise.all([
    api.settings(),
    api.project(),
  ]);
  return { settings, project };
}

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
  const [saved, setSaved] = useState(false);
  useEffect(() => setDraft(settings.effective), [settings]);
  const changed = Object.entries(draft).filter(
    ([key, value]) => value !== settings.effective[key as keyof Settings],
  );
  const save = useMutation({
    mutationFn: () =>
      api.saveSettings(Object.fromEntries(changed) as Partial<Settings>),
    onSuccess: (response) => {
      client.setQueryData(["settings"], response);
      setSaved(true);
    },
    onError: () => setSaved(false),
  });
  const update = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setDraft((old) => ({ ...old, [key]: value }));
    setSaved(false);
  };
  const numberField = (
    key:
      | "rounds"
      | "max_repairs"
      | "max_mutants"
      | "llm_timeout"
      | "vitest_timeout"
      | "stryker_timeout",
    label: string,
    min: number,
    max: number,
    help: string,
  ) => (
    <label className="setting-number" htmlFor={`setting-${key}`} key={key}>
      <strong>{label}</strong>
      <Input
        id={`setting-${key}`}
        type="number"
        min={min}
        max={max}
        value={draft[key]}
        onChange={(event) => update(key, Number(event.target.value))}
      />
      <small>{help}</small>
    </label>
  );
  return (
    <div className="page settings-page">
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <p>
            Defaults for every run. Options chosen on a single run still win.
          </p>
        </div>
        <span className="mono small muted">{settings.user_config_path}</span>
      </div>
      <div className="settings-layout">
        <div className="settings-form">
          <section>
            <div className="section-heading">
              <div>
                <h2>Model</h2>
                <p>Which OpenAI model writes the tests</p>
              </div>
              <button
                type="button"
                className="text-link"
                onClick={() => models.refetch()}
              >
                Refresh models
              </button>
            </div>
            <Card className="model-choices">
              {(
                models.data?.models ?? [
                  {
                    id: draft.model,
                    created_date: "",
                    newest: false,
                    default: true,
                  },
                ]
              ).map((item) => (
                <label key={item.id} className="model-choice">
                  <input
                    type="radio"
                    name="model"
                    checked={draft.model === item.id}
                    onChange={() => update("model", item.id)}
                  />
                  <span className="mono">{item.id}</span>
                  <span className="muted small">{item.created_date}</span>
                  {item.default && <span className="model-tag">default</span>}
                  {item.newest && (
                    <span className="model-tag light">newest</span>
                  )}
                </label>
              ))}
            </Card>
            <label className="toggle-row">
              <span>
                <strong>Ask which model to use before each run</strong>
                <small>
                  Applies to CLI use; this web app always shows the choice.
                </small>
              </span>
              <input
                type="checkbox"
                checked={draft.ask_model}
                onChange={(event) => update("ask_model", event.target.checked)}
              />
            </label>
            <label className="field">
              Reasoning effort
              <select
                value={draft.reasoning_effort ?? ""}
                onChange={(event) =>
                  update("reasoning_effort", event.target.value || null)
                }
              >
                <option value="">Default</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </label>
          </section>
          <section>
            <div className="section-heading">
              <div>
                <h2>Run defaults &amp; timeouts</h2>
                <p>How hard each run works, and when to give up</p>
              </div>
            </div>
            <div className="settings-cards">
              {numberField(
                "rounds",
                "Improvement rounds",
                0,
                5,
                "0–5 after the first mutation run",
              )}
              {numberField(
                "max_repairs",
                "Repair attempts",
                0,
                5,
                "0–5 retries for a rejected file",
              )}
              {numberField(
                "max_mutants",
                "Mutants per round",
                1,
                200,
                "1–200 sent to the model",
              )}
              {numberField(
                "llm_timeout",
                "Model silence",
                30,
                3600,
                "Seconds before one retry",
              )}
              {numberField(
                "vitest_timeout",
                "Vitest timeout",
                10,
                10000,
                "Seconds per validation",
              )}
              {numberField(
                "stryker_timeout",
                "Stryker timeout",
                30,
                10000,
                "Seconds per mutation run",
              )}
            </div>
            <label className="field runner-field">
              Runner
              <select
                value={draft.runner}
                onChange={(event) =>
                  update("runner", event.target.value as Settings["runner"])
                }
              >
                <option value="auto">Auto</option>
                <option value="bun">Bun</option>
                <option value="npx">npx</option>
              </select>
              <small>Auto uses Bun and falls back to npx.</small>
            </label>
          </section>
        </div>
        <aside className="settings-rail">
          <Card className={`save-card ${changed.length ? "dirty" : ""}`}>
            <h3>
              {changed.length
                ? `${changed.length} unsaved ${changed.length === 1 ? "change" : "changes"}`
                : saved
                  ? "Changes saved"
                  : "All changes saved"}
            </h3>
            <p className="muted small">
              {changed.length
                ? changed.map(([key]) => key.replaceAll("_", " ")).join(" · ")
                : "Your personal defaults are up to date."}
            </p>
            <div className="row gap-sm end">
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
                {save.isPending ? "Saving…" : "Save changes"}
              </Button>
            </div>
            {save.isError && (
              <p className="error-text small" role="alert">
                {save.error.message}
              </p>
            )}
          </Card>
          <Card className="rail-card">
            <h3>Where settings come from</h3>
            <p className="small muted">Later layers win</p>
            {[
              ["1", "Built-in defaults"],
              ["2", "Personal config"],
              ["3", "regress.toml"],
              ["4", "Environment"],
              ["5", "Options on a run"],
            ].map(([no, label]) => (
              <div className="layer-row" key={no}>
                <span className="muted mono">{no}</span>
                <span>{label}</span>
                {label === "Personal config" && (
                  <small className="push">editable here</small>
                )}
              </div>
            ))}
            <p className="muted small">
              Model source: {settings.model_source}. Project overrides remain in
              effect after a personal save.
            </p>
          </Card>
          <Card className="rail-card">
            <h3>Project &amp; toolchain</h3>
            {project.packages.map((item) => (
              <div key={item.name} className="row between small">
                <span className="mono">{item.name}</span>
                <span className={item.version ? "success" : "danger"}>
                  {item.version ?? "missing"}
                </span>
              </div>
            ))}
            <div className="row between small">
              <span>OPENAI_API_KEY</span>
              <span>{project.api_key_set ? "Set on server" : "Not set"}</span>
            </div>
          </Card>
        </aside>
      </div>
    </div>
  );
}
