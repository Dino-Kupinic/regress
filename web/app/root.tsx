import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { Unplug } from "lucide-react";
import { type ReactNode, useState } from "react";
import {
  isRouteErrorResponse,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
  useLocation,
  useRouteError,
} from "react-router";
import { AppSidebar } from "~/components/app-sidebar";
import { LogoMark } from "~/components/logo";
import { NewRunProvider } from "~/components/new-run";
import { type ApiState, SiteHeader } from "~/components/site-header";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { SidebarInset, SidebarProvider } from "~/components/ui/sidebar";
import { TooltipProvider } from "~/components/ui/tooltip";
import { api } from "~/lib/api";
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
    <div className="flex h-svh flex-col items-center justify-center gap-4 bg-sidebar">
      <div className="flex size-11 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm motion-safe:animate-pulse">
        <LogoMark className="size-5" />
      </div>
      <p className="text-sm text-muted-foreground">Opening project…</p>
    </div>
  );
}

export default function Root() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <AppShell />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

function AppShell() {
  const { pathname } = useLocation();
  // The sidebar component writes this cookie whenever it is toggled.
  const [sidebarOpen] = useState(
    () => !document.cookie.split("; ").includes("sidebar_state=false"),
  );
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
  const apiState: ApiState = project.isError
    ? "offline"
    : project.isSuccess
      ? "online"
      : "connecting";
  return (
    <NewRunProvider project={project.data}>
      <SidebarProvider defaultOpen={sidebarOpen}>
        <AppSidebar
          project={project.data}
          activeRun={active}
          model={settings.data?.effective.model}
        />
        <SidebarInset className="min-w-0">
          <SiteHeader projectName={project.data?.name} api={apiState} />
          {project.isError && (
            <div className="px-4 pt-4 lg:px-6">
              <Alert
                variant="destructive"
                className="motion-safe:animate-in fade-in-0 slide-in-from-top-1"
              >
                <Unplug />
                <AlertTitle>Could not connect to the Regress API</AlertTitle>
                <AlertDescription>
                  <p>
                    Start it with{" "}
                    <code className="font-mono">
                      uv run regress serve examples
                    </code>{" "}
                    on port 8765, then refresh.
                  </p>
                </AlertDescription>
              </Alert>
            </div>
          )}
          <div
            key={pathname}
            className="duration-300 ease-out motion-safe:animate-in fade-in-0 slide-in-from-bottom-1"
          >
            <Outlet />
          </div>
        </SidebarInset>
      </SidebarProvider>
    </NewRunProvider>
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
