import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, X } from "lucide-react";
import { useLoaderData } from "react-router";
import { Button } from "~/components/ui/button";
import { Card } from "~/components/ui/card";
import { api } from "~/lib/api";

export async function clientLoader() {
  const [project, sources] = await Promise.all([api.project(), api.sources()]);
  return { project, sources };
}

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
    },
  });
  const missing = project.packages.filter((item) => !item.version);
  const command = "uv run regress init --yes";
  return (
    <div className="page setup-page">
      <div className="page-header">
        <div>
          <h1>Set up {project.name}</h1>
          <p className="mono">{project.root}</p>
        </div>
        <span className="badge">Same as regress init</span>
      </div>
      <div className="setup-grid">
        <div className="setup-steps">
          <Card className="setup-step">
            <div className="step-heading">
              <span>1</span>
              <div>
                <h2>Install test dependencies</h2>
                <p className="muted">
                  Regress runs tests with Vitest and mutates code with
                  StrykerJS.
                </p>
              </div>
              <small className="push">
                {missing.length ? `${missing.length} to install` : "Ready"}
              </small>
            </div>
            <div className="step-body">
              {project.packages.map((item) => (
                <div key={item.name} className="package-row">
                  <span className={item.version ? "success" : "warning"}>
                    {item.version ? <Check size={16} /> : "○"}
                  </span>
                  <span className="mono grow">{item.name}</span>
                  <span className="muted">{item.version ?? "Missing"}</span>
                </div>
              ))}
              <div className="command row">
                <code>{command}</code>
                <button
                  type="button"
                  aria-label="Copy setup command"
                  onClick={() => navigator.clipboard.writeText(command)}
                >
                  <Copy size={14} />
                </button>
              </div>
              <div className="row gap-sm">
                <Button
                  disabled={init.isPending || !!project.active_run}
                  onClick={() => init.mutate()}
                >
                  {init.isPending
                    ? "Installing…"
                    : missing.length
                      ? `Install ${missing.length} packages`
                      : "Check and write config"}
                </Button>
                <span className="muted small">
                  This may take several minutes.
                </span>
              </div>
              {init.isError && (
                <p role="alert" className="error-text">
                  {init.error.message}
                </p>
              )}
            </div>
          </Card>
          <Card className="setup-step">
            <div className="step-heading">
              <span>2</span>
              <div>
                <h2>Write project config</h2>
                <p className="muted">Share run defaults with the team.</p>
              </div>
              <small className="push">
                {project.config_file ? "Written" : "After install"}
              </small>
            </div>
            <div className="step-body">
              <pre className="config-preview">{`# regress.toml\nrounds = 1\nmax_repairs = 2\nmax_mutants = 40\nrunner = "auto"`}</pre>
              <p className="muted small">
                The setup action writes <code>regress.toml</code> when absent.
                Run reports stay under the ignored <code>.regress/</code>{" "}
                directory.
              </p>
            </div>
          </Card>
          <Card className="setup-step">
            <div className="step-heading">
              <span>3</span>
              <div>
                <h2>Connect OpenAI</h2>
                <p className="muted">
                  Keep the API key in the Python server environment.
                </p>
              </div>
              <small
                className={project.api_key_set ? "success push" : "danger push"}
              >
                {project.api_key_set ? "Set" : "Not set"}
              </small>
            </div>
            <div className="step-body">
              <p className="small">
                Add <code>OPENAI_API_KEY</code> to the server environment or to
                the repository's <code>.env</code> file, then restart{" "}
                <code>regress serve</code>. The web app does not store or
                display the key.
              </p>
            </div>
          </Card>
        </div>
        <aside className="setup-rail">
          <Card className="rail-card">
            <h3>Readiness</h3>
            <div className="row between">
              <span>Project package.json</span>
              <span className="success">
                <Check size={16} />
              </span>
            </div>
            <div className="row between">
              <span>Vitest + Stryker</span>
              <span className={project.toolchain ? "success" : "danger"}>
                {project.toolchain ? <Check size={16} /> : <X size={16} />}
              </span>
            </div>
            <div className="row between">
              <span>regress.toml</span>
              <span>{project.config_file ? <Check size={16} /> : "○"}</span>
            </div>
            <div className="row between">
              <span>OpenAI API key</span>
              <span>{project.api_key_set ? <Check size={16} /> : "○"}</span>
            </div>
            <p className={project.ready ? "success small" : "error-text small"}>
              {project.ready ? "Ready for a run" : project.problems.join(" ")}
            </p>
          </Card>
          <Card className="rail-card">
            <h3>Files you can test</h3>
            <span className="small muted">{sources.length} found</span>
            {sources.slice(0, 10).map((item) => (
              <div className="package-row mono small" key={item.path}>
                {item.path}
              </div>
            ))}
          </Card>
        </aside>
      </div>
    </div>
  );
}
