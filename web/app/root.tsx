import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { CircleAlert, Unplug } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import {
  isRouteErrorResponse,
  Links,
  type LinksFunction,
  Meta,
  type MetaFunction,
  Outlet,
  Scripts,
  ScrollRestoration,
  useLocation,
  useNavigate,
  useNavigation,
  useRouteError,
} from "react-router";
import { toast } from "sonner";
import { AppSidebar } from "~/components/app-sidebar";
import { LogoMark } from "~/components/logo";
import { NewRunProvider } from "~/components/new-run";
import { type ApiState, SiteHeader } from "~/components/site-header";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "~/components/ui/empty";
import { SidebarInset, SidebarProvider } from "~/components/ui/sidebar";
import { Toaster } from "~/components/ui/sonner";
import { TooltipProvider } from "~/components/ui/tooltip";
import { ApiError, api, type RunSummary } from "~/lib/api";
import { cn, fileName, percent, signedPoints } from "~/lib/utils";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A 4xx answer won't change on a retry; only retry network and server errors.
      retry: (failures, error) =>
        failures < 1 && !(error instanceof ApiError && error.status < 500),
      refetchOnWindowFocus: true,
      staleTime: 5_000,
    },
  },
});

export const meta: MetaFunction = () => [{ title: "Regress" }];

export const links: LinksFunction = () => [
  { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" },
];

export function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
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
        <Toaster position="bottom-right" />
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
  useRunFinishedToast(runs.data);
  const apiState: ApiState = project.isError
    ? "offline"
    : project.isSuccess
      ? "online"
      : "connecting";
  return (
    <NewRunProvider project={project.data}>
      <NavigationProgress />
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

/** A thin bar at the top while a page's data loads; hidden for quick loads. */
function NavigationProgress() {
  const busy = useNavigation().state !== "idle";
  return (
    <div
      aria-hidden="true"
      className={cn(
        "pointer-events-none fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden opacity-0 transition-opacity duration-200",
        busy && "opacity-100 delay-150",
      )}
    >
      {busy && (
        <span className="absolute inset-y-0 left-0 w-2/5 bg-primary motion-safe:animate-indeterminate" />
      )}
    </div>
  );
}

/** Tell the user when the active run ends, wherever they are in the app. */
function useRunFinishedToast(runs: RunSummary[] | undefined) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const activeId = runs?.find((run) => run.active)?.id;
  const previous = useRef(activeId);
  useEffect(() => {
    const was = previous.current;
    previous.current = activeId;
    if (!was || was === activeId) return;
    const run = runs?.find((item) => item.id === was);
    if (!run || run.active) return;
    const to = `/runs/${encodeURIComponent(run.id)}`;
    const options = {
      action:
        pathname === to
          ? undefined
          : { label: "Open report", onClick: () => navigate(to) },
    };
    const file = fileName(run.source_file);
    if (run.status === "completed") {
      toast.success(`Run finished · ${file}`, {
        ...options,
        description:
          run.kept_score == null
            ? undefined
            : `Kept score ${percent(run.kept_score)}${run.improvement == null ? "" : ` (${signedPoints(run.improvement)})`}`,
      });
    } else if (run.status === "failed") {
      toast.error(`Run failed · ${file}`, {
        ...options,
        description: run.error?.split("\n")[0],
      });
    } else {
      toast(`Run cancelled · ${file}`, options);
    }
  }, [activeId, runs, pathname, navigate]);
}

export function ErrorBoundary() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? error.statusText
    : error instanceof Error
      ? error.message
      : "Unexpected error";
  return (
    <div className="flex min-h-svh items-center justify-center p-6">
      <Empty className="max-w-md">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <CircleAlert />
          </EmptyMedia>
          <EmptyTitle>Could not load this page</EmptyTitle>
          <EmptyDescription>{message}</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button onClick={() => window.location.reload()}>Try again</Button>
        </EmptyContent>
      </Empty>
    </div>
  );
}
