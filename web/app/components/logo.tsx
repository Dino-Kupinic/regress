import type * as React from "react";

/** A 3×3 grid of killed mutants with one survivor in the corner. */
export function LogoMark(props: React.ComponentProps<"svg">) {
  return (
    <svg viewBox="0 0 96 96" fill="currentColor" aria-hidden="true" {...props}>
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
  );
}
