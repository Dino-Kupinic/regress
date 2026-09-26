export interface SlugOptions {
  separator?: string;
  maxLength?: number;
}

/** URL-friendly slug: lowercase ASCII words joined by a separator, cut at a word boundary. */
export function slugify(input: string, { separator = "-", maxLength = 60 }: SlugOptions = {}): string {
  const sep = escapeRegExp(separator);
  const slug = input
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, separator)
    .replace(new RegExp(`^(?:${sep})+|(?:${sep})+$`, "g"), "");
  if (slug.length <= maxLength) {
    return slug;
  }
  const cut = slug.slice(0, maxLength);
  const lastSeparator = cut.lastIndexOf(separator);
  return lastSeparator > 0 ? cut.slice(0, lastSeparator) : cut;
}

/** Shortens text to at most `max` characters, preferring to cut at a space. */
export function truncate(text: string, max: number, ellipsis = "…"): string {
  if (max < ellipsis.length) {
    throw new RangeError("max must be at least the length of the ellipsis");
  }
  if (text.length <= max) {
    return text;
  }
  const room = max - ellipsis.length;
  const cut = text.slice(0, room);
  const lastSpace = cut.lastIndexOf(" ");
  const base = lastSpace > room / 2 ? cut.slice(0, lastSpace) : cut;
  return base.trimEnd() + ellipsis;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
