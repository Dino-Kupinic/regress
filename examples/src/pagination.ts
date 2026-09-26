export interface Page {
  page: number;
  pageSize: number;
  totalItems: number;
  totalPages: number;
  offset: number;
  hasPrevious: boolean;
  hasNext: boolean;
}

export const MAX_PAGE_SIZE = 100;

/** Resolves a requested page, clamping it into range. There is always at least one page. */
export function paginate(totalItems: number, page: number, pageSize = 20): Page {
  if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > MAX_PAGE_SIZE) {
    throw new RangeError(`pageSize must be an integer between 1 and ${MAX_PAGE_SIZE}`);
  }
  if (!Number.isInteger(totalItems) || totalItems < 0) {
    throw new RangeError("totalItems must be a non-negative integer");
  }
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const current = Math.min(Math.max(1, Math.trunc(page)), totalPages);
  return {
    page: current,
    pageSize,
    totalItems,
    totalPages,
    offset: (current - 1) * pageSize,
    hasPrevious: current > 1,
    hasNext: current < totalPages,
  };
}

/** Page numbers to show in a pager: a window of `size` pages around the current one. */
export function pageWindow(current: number, totalPages: number, size = 5): number[] {
  const half = Math.floor(size / 2);
  let start = Math.max(1, current - half);
  const end = Math.min(totalPages, start + size - 1);
  start = Math.max(1, end - size + 1);
  const pages: number[] = [];
  for (let page = start; page <= end; page++) {
    pages.push(page);
  }
  return pages;
}
