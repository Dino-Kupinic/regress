import type * as React from "react";
import { cn } from "~/lib/utils";

export function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div className="table-scroll">
      <table className={cn("table", className)} {...props} />
    </div>
  );
}
