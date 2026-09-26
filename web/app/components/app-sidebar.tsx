import {
  Activity,
  ChartNoAxesCombined,
  Check,
  ChevronsUpDown,
  Copy,
  Cpu,
  History,
  KeyRound,
  LayoutGrid,
  type LucideIcon,
  Plus,
  Settings2,
  Wrench,
} from "lucide-react";
import { type ComponentProps, useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { LogoMark } from "~/components/logo";
import { useNewRun } from "~/components/new-run";
import { StatusDot } from "~/components/status-dot";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { Kbd } from "~/components/ui/kbd";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "~/components/ui/sidebar";
import type { ProjectInfo, RunSummary } from "~/lib/api";
import { duration, fileName } from "~/lib/utils";

type NavItem = { to: string; label: string; icon: LucideIcon };

const workspaceNav: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutGrid },
  { to: "/runs", label: "Runs", icon: History },
  { to: "/evaluation", label: "Evaluation", icon: ChartNoAxesCombined },
];

const configureNav: NavItem[] = [
  { to: "/settings", label: "Settings", icon: Settings2 },
  { to: "/setup", label: "Project setup", icon: Wrench },
];

export function AppSidebar({
  project,
  activeRun,
  model,
  ...props
}: ComponentProps<typeof Sidebar> & {
  project?: ProjectInfo;
  activeRun?: RunSummary;
  model?: string;
}) {
  return (
    <Sidebar variant="inset" collapsible="icon" {...props}>
      <SidebarHeader>
        <ProjectMenu project={project} />
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <NewRunButton />
          </SidebarGroupContent>
        </SidebarGroup>
        <NavGroup label="Workspace" items={workspaceNav} live={!!activeRun} />
        {activeRun && <ActiveRun run={activeRun} />}
        <NavGroup
          label="Configure"
          items={configureNav}
          className={activeRun ? undefined : "mt-auto"}
        />
      </SidebarContent>
      <SidebarFooter>
        <EnvironmentMenu model={model} apiKeySet={project?.api_key_set} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

function ProjectMenu({ project }: { project?: ProjectInfo }) {
  const { isMobile } = useSidebar();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);
  const problems = project?.problems.length ?? 0;
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground"
            >
              <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground shadow-xs">
                <LogoMark className="size-4" />
              </div>
              <div className="grid flex-1 gap-0.5 text-left leading-tight">
                <span className="truncate font-brand text-lg leading-none">
                  REGRESS
                </span>
                <span className="truncate font-mono text-xs text-muted-foreground">
                  {project?.name ?? "Connecting…"}
                </span>
              </div>
              <ChevronsUpDown className="ml-auto text-muted-foreground" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-(--radix-dropdown-menu-trigger-width) min-w-72"
            align="start"
            side={isMobile ? "bottom" : "right"}
            sideOffset={4}
          >
            <DropdownMenuLabel className="text-xs text-muted-foreground">
              Project
            </DropdownMenuLabel>
            <DropdownMenuLabel className="flex flex-col gap-0.5 pt-0">
              <span>{project?.name ?? "Not connected"}</span>
              <span
                className="truncate font-mono text-xs font-normal text-muted-foreground"
                title={project?.root}
              >
                {project?.root ?? "Start the API to load a project"}
              </span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem asChild>
                <Link to="/setup">
                  <Wrench />
                  Project setup
                  {project && (
                    <DropdownMenuShortcut>
                      {project.ready
                        ? "Ready"
                        : `${problems} ${problems === 1 ? "issue" : "issues"}`}
                    </DropdownMenuShortcut>
                  )}
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={!project}
                onSelect={(event) => {
                  if (!project) return;
                  event.preventDefault();
                  navigator.clipboard
                    .writeText(project.root)
                    .then(() => setCopied(true));
                }}
              >
                {copied ? <Check /> : <Copy />}
                {copied ? "Copied" : "Copy project path"}
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}

function NewRunButton() {
  const { open, opened } = useNewRun();
  const { isMobile, setOpenMobile } = useSidebar();
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        opened ||
        event.defaultPrevented ||
        event.repeat ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.key.toLowerCase() !== "n"
      ) {
        return;
      }
      const target = event.target as HTMLElement | null;
      if (
        target?.isContentEditable ||
        target?.closest("input, textarea, select, [role=menu], [role=dialog]")
      ) {
        return;
      }
      event.preventDefault();
      open();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, opened]);
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          tooltip={{
            children: (
              <>
                New run <Kbd>N</Kbd>
              </>
            ),
          }}
          onClick={() => {
            if (isMobile) setOpenMobile(false);
            open();
          }}
          className="bg-primary text-primary-foreground shadow-xs duration-200 ease-out hover:bg-primary/90 hover:text-primary-foreground active:bg-primary/90 active:text-primary-foreground"
        >
          <Plus />
          <span>New run</span>
          <Kbd className="ml-auto bg-primary-foreground/15 text-primary-foreground/80 group-data-[collapsible=icon]:hidden">
            N
          </Kbd>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}

function NavGroup({
  label,
  items,
  live = false,
  className,
}: {
  label: string;
  items: NavItem[];
  live?: boolean;
  className?: string;
}) {
  const { pathname } = useLocation();
  const { isMobile, setOpenMobile } = useSidebar();
  return (
    <SidebarGroup className={className}>
      <SidebarGroupLabel>{label}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map(({ to, label, icon: Icon }) => (
            <SidebarMenuItem key={to}>
              <SidebarMenuButton
                asChild
                tooltip={label}
                isActive={
                  to === "/" ? pathname === "/" : pathname.startsWith(to)
                }
              >
                <Link
                  to={to}
                  onClick={() => {
                    if (isMobile) setOpenMobile(false);
                  }}
                >
                  <Icon />
                  <span>{label}</span>
                </Link>
              </SidebarMenuButton>
              {live && to === "/runs" && (
                <SidebarMenuBadge>
                  <StatusDot tone="success" pulse />
                  <span className="sr-only">Run in progress</span>
                </SidebarMenuBadge>
              )}
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

function useElapsed(since: string) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const started = Date.parse(since);
  return Number.isNaN(started) ? null : Math.max(0, (now - started) / 1000);
}

function ActiveRun({ run }: { run: RunSummary }) {
  const elapsed = duration(useElapsed(run.created_at));
  const to = `/runs/${encodeURIComponent(run.id)}`;
  return (
    <SidebarGroup className="mt-auto">
      <SidebarGroupLabel>Active run</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu className="hidden group-data-[collapsible=icon]:flex">
          <SidebarMenuItem>
            <SidebarMenuButton
              asChild
              tooltip={`Running ${fileName(run.source_file)} · ${elapsed}`}
            >
              <Link to={to}>
                <Activity />
                <span>{fileName(run.source_file)}</span>
              </Link>
            </SidebarMenuButton>
            <StatusDot
              tone="success"
              pulse
              className="pointer-events-none absolute top-1 right-1"
            />
          </SidebarMenuItem>
        </SidebarMenu>
        <Link
          to={to}
          className="flex flex-col gap-3 rounded-lg border bg-background p-3 text-xs shadow-xs transition-[border-color,box-shadow] duration-200 hover:border-foreground/20 hover:shadow-sm motion-safe:animate-in fade-in-0 slide-in-from-bottom-2 group-data-[collapsible=icon]:hidden"
        >
          <div className="flex items-center gap-2">
            <StatusDot tone="success" pulse />
            <span className="font-medium">Running</span>
            <span className="ml-auto font-mono text-muted-foreground tabular-nums">
              {elapsed}
            </span>
          </div>
          <div className="grid gap-0.5">
            <span className="truncate font-mono">
              {fileName(run.source_file)}
            </span>
            <span className="truncate text-muted-foreground">{run.model}</span>
          </div>
          <div className="relative h-1 overflow-hidden rounded-full bg-muted">
            <span className="absolute inset-y-0 left-0 w-2/5 rounded-full bg-primary motion-safe:animate-indeterminate" />
          </div>
        </Link>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

function EnvironmentMenu({
  model,
  apiKeySet,
}: {
  model?: string;
  apiKeySet?: boolean;
}) {
  const { isMobile } = useSidebar();
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground"
            >
              <div className="relative flex aspect-square size-8 items-center justify-center rounded-lg border bg-background">
                <Cpu />
                <StatusDot
                  tone={apiKeySet ? "success" : "destructive"}
                  className="absolute -top-0.5 -right-0.5 rounded-full ring-2 ring-sidebar"
                />
              </div>
              <div className="grid flex-1 text-left leading-tight">
                <span className="truncate font-mono text-xs font-medium">
                  {model ?? "—"}
                </span>
                <span className="truncate text-xs text-muted-foreground">
                  {apiKeySet ? "API key set on server" : "API key not set"}
                </span>
              </div>
              <ChevronsUpDown className="ml-auto text-muted-foreground" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-(--radix-dropdown-menu-trigger-width) min-w-60"
            align="end"
            side={isMobile ? "bottom" : "right"}
            sideOffset={4}
          >
            <DropdownMenuLabel className="text-xs text-muted-foreground">
              Environment
            </DropdownMenuLabel>
            <DropdownMenuGroup>
              <DropdownMenuItem asChild>
                <Link to="/settings">
                  <Cpu />
                  Default model
                  <DropdownMenuShortcut className="font-mono">
                    {model ?? "—"}
                  </DropdownMenuShortcut>
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link to="/setup">
                  <KeyRound />
                  API key
                  <DropdownMenuShortcut>
                    {apiKeySet ? "Set" : "Missing"}
                  </DropdownMenuShortcut>
                </Link>
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="font-mono text-xs font-normal text-muted-foreground">
              regress 0.1.0
            </DropdownMenuLabel>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
