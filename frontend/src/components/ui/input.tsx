import * as React from "react";
import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "h-9 w-full min-w-0 rounded-md border border-input bg-card px-3 text-body text-foreground",
        "transition-[border-color,box-shadow] duration-150 ease-out-quart outline-none",
        "placeholder:text-muted-foreground hover:border-input-hover",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/20 focus-visible:outline-none",
        "aria-invalid:border-threat aria-invalid:focus-visible:ring-threat/20",
        "disabled:cursor-not-allowed disabled:bg-muted disabled:opacity-60",
        "read-only:bg-muted",
        "file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-body file:font-medium file:text-foreground",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
