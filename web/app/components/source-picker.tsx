import { Check, ChevronDown, Search } from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Button } from "~/components/ui/button";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "~/components/ui/input-group";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "~/components/ui/popover";
import type { SourceFile } from "~/lib/api";
import { cn, fileName } from "~/lib/utils";

/** How many matches a source list renders; real projects have thousands of files. */
export const SOURCE_LIMIT = 50;

/** Sources whose path contains the query, file-name matches first. */
export function matchSources(sources: SourceFile[], query: string) {
  const needle = query.trim().toLowerCase();
  if (!needle) return sources;
  const byName: SourceFile[] = [];
  const byPath: SourceFile[] = [];
  for (const item of sources) {
    const path = item.path.toLowerCase();
    if (fileName(path).includes(needle)) byName.push(item);
    else if (path.includes(needle)) byPath.push(item);
  }
  return [...byName, ...byPath];
}

/** A searchable source-file combobox that stays fast with thousands of files. */
export function SourcePicker({
  sources,
  recent = [],
  value,
  onValueChange,
  className,
}: {
  sources: SourceFile[];
  /** Paths shown first while the search is empty, e.g. recently run files. */
  recent?: string[];
  value: string;
  onValueChange: (path: string) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  const known = new Set(sources.map((item) => item.path));
  const pinned = query
    ? []
    : recent.filter((path) => known.has(path)).slice(0, 5);
  const matches = matchSources(sources, query).filter(
    (item) => !pinned.includes(item.path),
  );
  const options = [
    ...pinned,
    ...matches.slice(0, SOURCE_LIMIT).map((item) => item.path),
  ];
  const hidden = matches.length - Math.min(matches.length, SOURCE_LIMIT);

  useEffect(() => {
    list.current
      ?.querySelector(`[data-index="${highlight}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [highlight]);

  const choose = (path: string) => {
    onValueChange(path);
    setOpen(false);
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setHighlight(
        (index) =>
          (index + step + options.length) % Math.max(options.length, 1),
      );
    } else if (event.key === "Enter" && options[highlight]) {
      event.preventDefault();
      choose(options[highlight]);
    }
  };
  const option = (path: string, index: number) => (
    <button
      type="button"
      role="option"
      key={path}
      data-index={index}
      aria-selected={path === value}
      data-highlighted={index === highlight || undefined}
      onMouseMove={() => setHighlight(index)}
      onClick={() => choose(path)}
      className="flex w-full min-w-0 items-center gap-2 rounded-sm px-2 py-1.5 text-left font-mono text-xs outline-none data-highlighted:bg-accent"
    >
      <span className="min-w-0 flex-1 truncate">{path}</span>
      <Check
        className={cn(
          "size-3.5 shrink-0",
          path === value ? "opacity-100" : "opacity-0",
        )}
      />
    </button>
  );

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setQuery("");
          setHighlight(0);
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          role="combobox"
          aria-expanded={open}
          aria-label="Source file to run"
          className={cn(
            "min-w-0 justify-between px-3 font-mono font-normal",
            !value && "text-muted-foreground",
            className,
          )}
        >
          <span className="truncate">{value || "Choose a source file"}</span>
          <ChevronDown data-icon="inline-end" className="opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[min(34rem,calc(100vw-2rem))] gap-0 p-0"
      >
        <div className="border-b p-2">
          <InputGroup>
            <InputGroupAddon>
              <Search />
            </InputGroupAddon>
            <InputGroupInput
              autoFocus
              aria-label="Search source files"
              placeholder={`Search ${sources.length} files`}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setHighlight(0);
              }}
              onKeyDown={onKeyDown}
            />
          </InputGroup>
        </div>
        <div
          ref={list}
          role="listbox"
          aria-label="Source files"
          className="max-h-80 overflow-y-auto p-1 scroll-fade"
        >
          {pinned.length > 0 && (
            <>
              <p className="px-2 pt-1.5 pb-1 text-xs text-muted-foreground">
                Recently run
              </p>
              {pinned.map((path, index) => option(path, index))}
              <p className="px-2 pt-2.5 pb-1 text-xs text-muted-foreground">
                All files
              </p>
            </>
          )}
          {matches
            .slice(0, SOURCE_LIMIT)
            .map((item, index) => option(item.path, pinned.length + index))}
          {!options.length && (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">
              No matching source files.
            </p>
          )}
        </div>
        {hidden > 0 && (
          <p className="border-t px-3 py-2 text-caption text-muted-foreground">
            {hidden} more {hidden === 1 ? "match" : "matches"}. Keep typing to
            narrow the list.
          </p>
        )}
      </PopoverContent>
    </Popover>
  );
}
