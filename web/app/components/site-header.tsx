import { Fragment } from "react";
import { Link, useLocation } from "react-router";
import { StatusDot } from "~/components/status-dot";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "~/components/ui/breadcrumb";
import { Kbd, KbdGroup } from "~/components/ui/kbd";
import { Separator } from "~/components/ui/separator";
import { SidebarTrigger } from "~/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "~/components/ui/tooltip";

type Crumb = { label: string; to?: string; mono?: boolean };

const sections: Record<string, string> = {
  "": "Dashboard",
  runs: "Runs",
  evaluation: "Evaluation",
  settings: "Settings",
  setup: "Project setup",
};

function crumbsFor(pathname: string): Crumb[] {
  const [section = "", id] = pathname.split("/").filter(Boolean);
  if (section === "batches" && id) {
    // A multi-file run has no list of its own; it belongs with the runs.
    return [
      { label: "Runs", to: "/runs" },
      { label: decodeURIComponent(id), mono: true },
    ];
  }
  const label = sections[section] ?? "Not found";
  if (!id) return [{ label }];
  return [
    { label, to: `/${section}` },
    { label: decodeURIComponent(id), mono: true },
  ];
}

export type ApiState = "connecting" | "online" | "offline";

const apiStatus = {
  connecting: { tone: "muted", label: "Connecting" },
  online: { tone: "success", label: "API online" },
  offline: { tone: "destructive", label: "API offline" },
} as const;

export function SiteHeader({
  projectName,
  api,
}: {
  projectName?: string;
  api: ApiState;
}) {
  const { pathname } = useLocation();
  const crumbs = crumbsFor(pathname);
  const mod =
    typeof navigator !== "undefined" && /Mac|iP/.test(navigator.platform)
      ? "⌘"
      : "Ctrl";
  const status = apiStatus[api];
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4 lg:px-6">
      <Tooltip>
        <TooltipTrigger asChild>
          <SidebarTrigger className="-ml-1.5" />
        </TooltipTrigger>
        <TooltipContent side="bottom">
          Toggle sidebar
          <KbdGroup>
            <Kbd>{mod}</Kbd>
            <Kbd>B</Kbd>
          </KbdGroup>
        </TooltipContent>
      </Tooltip>
      <Separator
        orientation="vertical"
        className="mr-2 data-vertical:h-4 data-vertical:self-center"
      />
      <Breadcrumb className="min-w-0">
        <BreadcrumbList className="flex-nowrap">
          <BreadcrumbItem className="hidden md:inline-flex">
            <BreadcrumbLink asChild>
              <Link to="/" className="font-mono">
                {projectName ?? "regress"}
              </Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          {crumbs.map((crumb, index) => (
            <Fragment key={crumb.label}>
              <BreadcrumbSeparator
                className={index === 0 ? "hidden md:block" : undefined}
              />
              <BreadcrumbItem className="min-w-0">
                {crumb.to ? (
                  <BreadcrumbLink asChild>
                    <Link to={crumb.to}>{crumb.label}</Link>
                  </BreadcrumbLink>
                ) : (
                  <BreadcrumbPage
                    className={crumb.mono ? "truncate font-mono" : "truncate"}
                  >
                    {crumb.label}
                  </BreadcrumbPage>
                )}
              </BreadcrumbItem>
            </Fragment>
          ))}
        </BreadcrumbList>
      </Breadcrumb>
      <div
        className="ml-auto flex shrink-0 items-center gap-2 text-xs text-muted-foreground"
        role="status"
      >
        <StatusDot tone={status.tone} pulse={api === "connecting"} />
        <span className="sr-only sm:not-sr-only">{status.label}</span>
      </div>
    </header>
  );
}
