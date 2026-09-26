import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  Activity,
  ChartNoAxesCombined,
  LayoutGrid,
  List,
  Settings2,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import {
  isRouteErrorResponse,
  Links,
  Meta,
  NavLink,
  Outlet,
  Scripts,
  ScrollRestoration,
  useRouteError,
} from "react-router";
import { NewRunProvider, useNewRun } from "~/components/new-run";
import { Button } from "~/components/ui/button";
import { api } from "~/lib/api";
import { duration, fileName } from "~/lib/utils";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 5_000 },
  },
});

export function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Regress</title>
        <Meta />
        <Links />
      </head>
      <body>
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export function HydrateFallback() {
  return (
    <div className="boot-screen">
      <strong>REGRESS</strong>
      <p>Opening project…</p>
    </div>
  );
}

export default function Root() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppShell />
    </QueryClientProvider>
  );
}

function Logo() {
  return (
    <div className="logo">
      <svg width="17" height="17" viewBox="0 0 96 96" aria-hidden="true">
        <rect x="0" y="0" width="26" height="26" rx="3" />
        <rect x="35" y="0" width="26" height="26" rx="3" />
        <rect x="70" y="0" width="26" height="26" rx="3" />
        <rect x="0" y="35" width="26" height="26" rx="3" />
        <rect x="35" y="35" width="26" height="26" rx="3" />
        <rect x="70" y="35" width="26" height="26" rx="3" />
        <rect x="0" y="70" width="26" height="26" rx="3" />
        <rect x="35" y="70" width="26" height="26" rx="3" />
        <rect
          x="72.5"
          y="72.5"
          width="21"
          height="21"
          rx="1.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="7"
        />
      </svg>
      <span>REGRESS</span>
    </div>
  );
}

function AppShell() {
  const project = useQuery({
    queryKey: ["project"],
    queryFn: api.project,
    refetchInterval: 5_000,
  });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    refetchInterval: (query) =>
      query.state.data?.some((run) => run.active) ? 3_000 : 15_000,
  });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const active = runs.data?.find((run) => run.active);
  return (
    <NewRunProvider project={project.data}>
      <div className="app-shell">
        <Sidebar
          name={project.data?.name}
          active={active}
          model={settings.data?.effective.model}
          apiKeySet={project.data?.api_key_set}
        />
        <main className="main-content">
          {project.isError && (
            <div className="connection-banner" role="alert">
              Could not connect to the Regress API. Start it with{" "}
              <code>uv run regress serve examples</code> on port 8765, then
              refresh.
            </div>
          )}
          <Outlet />
        </main>
      </div>
    </NewRunProvider>
  );
}

function Sidebar({
  name,
  active,
  model,
  apiKeySet,
}: {
  name?: string;
  active?: Awaited<ReturnType<typeof api.runs>>[number];
  model?: string;
  apiKeySet?: boolean;
}) {
  const { open } = useNewRun();
  const [mobileOpen, setMobileOpen] = useState(false);
  const links = [
    { to: "/", label: "Dashboard", icon: LayoutGrid },
    { to: "/runs", label: "Runs", icon: List },
    { to: "/evaluation", label: "Evaluation", icon: ChartNoAxesCombined },
    { to: "/settings", label: "Settings", icon: Settings2 },
  ];
  return (
    <>
      <button
        type="button"
        className="mobile-nav-trigger"
        onClick={() => setMobileOpen(!mobileOpen)}
        aria-expanded={mobileOpen}
        aria-label="Toggle navigation"
      >
        <List size={20} />
      </button>
      <aside className={`sidebar ${mobileOpen ? "sidebar-open" : ""}`}>
        <Logo />
        <div className="project-switch">
          <span className="tiny-dot" />
          <span className="mono truncate">{name ?? "Connecting…"}</span>
          <span className="push muted">⌄</span>
        </div>
        <button
          type="button"
          className="new-run-side"
          onClick={() => {
            open();
            setMobileOpen(false);
          }}
        >
          New run <kbd>N</kbd>
        </button>
        <nav aria-label="Main navigation" className="side-nav">
          {links.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                `side-link ${isActive ? "active" : ""}`
              }
            >
              <Icon size={16} />
              <span>{label}</span>
              {label === "Runs" && active && (
                <span className="push mono tiny">◦ 1</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="side-spacer" />
        {active && (
          <NavLink
            className="active-run-card"
            to={`/runs/${encodeURIComponent(active.id)}`}
          >
            <div className="row gap-sm">
              <Activity size={14} />
              <span className="mono truncate grow">{active.source_file}</span>
              <span className="muted mono tiny">
                {duration(active.duration_seconds)}
              </span>
            </div>
            <p className="muted">Running · {fileName(active.source_file)}</p>
            <div className="mini-progress">
              <span />
            </div>
          </NavLink>
        )}
        <footer className="side-footer">
          <div className="row between">
            <span>Model</span>
            <span className="mono">{model ?? "—"}</span>
          </div>
          <div className="row between">
            <span>API key</span>
            <span>{apiKeySet ? "● Set on server" : "○ Not set"}</span>
          </div>
          <small className="mono">regress 0.1.0</small>
        </footer>
      </aside>
    </>
  );
}

export function ErrorBoundary() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? error.statusText
    : error instanceof Error
      ? error.message
      : "Unexpected error";
  return (
    <div className="error-page">
      <h1>Could not load this page</h1>
      <p>{message}</p>
      <Button onClick={() => window.location.reload()}>Try again</Button>
    </div>
  );
}
