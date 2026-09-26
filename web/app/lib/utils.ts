import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function percent(value: number | null | undefined) {
  return value == null ? "—" : `${Math.round(value)}%`;
}

export function duration(seconds: number | null | undefined) {
  if (seconds == null) return "—";
  const minutes = Math.floor(seconds / 60);
  const remain = Math.floor(seconds % 60);
  return minutes
    ? `${minutes}m ${String(remain).padStart(2, "0")}s`
    : `${remain}s`;
}

export function dateTime(value: string) {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function fileName(path: string) {
  return path.split("/").at(-1) ?? path;
}
