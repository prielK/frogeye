import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type EmptyStateProps = {
  title: ReactNode;
  /** What this place is for and how it fills up: teach, don't just say "nothing here". */
  hint?: ReactNode;
  action?: ReactNode;
  /** "error" draws the panel in the threat tone for failures with a recovery action. */
  tone?: "default" | "error";
  className?: string;
};

export function EmptyState({ title, hint, action, tone = "default", className }: EmptyStateProps) {
  return (
    <div
      role={tone === "error" ? "alert" : undefined}
      className={cn(
        "flex max-w-[65ch] flex-col items-start gap-1.5 rounded-lg border border-dashed px-5 py-6",
        tone === "error" && "border-solid border-threat/35 bg-threat-soft/60",
        className,
      )}
    >
      <p className={cn("text-body font-medium text-foreground", tone === "error" && "text-threat")}>{title}</p>
      {hint ? <p className="text-body text-muted-foreground">{hint}</p> : null}
      {action ? <div className="mt-2.5 flex flex-wrap items-center gap-2">{action}</div> : null}
    </div>
  );
}
