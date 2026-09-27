export function Logo() {
  return (
    <span className="inline-flex items-center gap-2.5">
      <svg viewBox="0 0 96 96" fill="currentColor" aria-hidden="true" className="size-5">
        <rect width="26" height="26" rx="3" />
        <rect x="35" width="26" height="26" rx="3" />
        <rect x="70" width="26" height="26" rx="3" />
        <rect y="35" width="26" height="26" rx="3" />
        <rect x="35" y="35" width="26" height="26" rx="3" />
        <rect x="70" y="35" width="26" height="26" rx="3" />
        <rect y="70" width="26" height="26" rx="3" />
        <rect x="35" y="70" width="26" height="26" rx="3" />
        <rect x="72.5" y="72.5" width="21" height="21" rx="1.5" fill="none" stroke="currentColor" strokeWidth="7" />
      </svg>
      <span className="font-brand text-lg leading-none">REGRESS</span>
      <span className="rounded-full border border-fd-border px-2 py-0.5 text-[10px] font-medium uppercase tracking-widest text-fd-muted-foreground">Docs</span>
    </span>
  );
}
