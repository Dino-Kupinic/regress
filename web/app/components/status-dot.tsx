import { cn } from "~/lib/utils";

const tones = {
  default: "bg-foreground",
  success: "bg-success",
  destructive: "bg-destructive",
  muted: "bg-muted-foreground",
};

export function StatusDot({
  tone,
  pulse = false,
  className,
}: {
  tone: keyof typeof tones;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn("relative flex size-2 shrink-0", className)}
    >
      {pulse && (
        <span
          className={cn(
            "absolute inline-flex size-full rounded-full opacity-75 motion-safe:animate-ping",
            tones[tone],
          )}
        />
      )}
      <span
        className={cn("relative inline-flex size-2 rounded-full", tones[tone])}
      />
    </span>
  );
}
