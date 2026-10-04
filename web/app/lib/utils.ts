export { cn } from "cn";

export function percent(value: number | null | undefined) {
  return value == null ? "—" : `${Math.round(value)}%`;
}

/** $0.0031, $0.42, $12.30: enough digits to tell small runs apart (as the CLI shows them). */
export function usd(value: number | null | undefined) {
  if (value == null) return "—";
  if (value === 0) return "$0.00";
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
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

export function signedPoints(value: number | null | undefined) {
  if (value == null) return "—";
  const rounded = Math.round(value);
  return `${rounded >= 0 ? "+" : ""}${rounded} pts`;
}

/** The browser tab title for a page, e.g. "Runs · Regress". */
export function pageTitle(...parts: string[]) {
  return [...parts, "Regress"].join(" · ");
}
