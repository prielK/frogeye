import { CircleCheckIcon, CircleDashedIcon, CircleXIcon, TriangleAlertIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A run's state ("running" | "interrupted" | "failed" | "succeeded", or anything newer shown
 * as is) as icon plus word, never colour alone: running pulses, failures and interruptions
 * take a soft fill, success stays quiet.
 */
export function RunStatusBadge({ state }: { state: string }) {
  const base = "inline-flex h-5 w-fit items-center gap-1.5 rounded-sm text-meta font-medium whitespace-nowrap";
  if (state === "running") {
    return (
      <span className={cn(base, "bg-primary-soft px-1.5 text-primary-ink")}>
        <span aria-hidden className="relative flex size-2">
          <span className="absolute inset-0 animate-ping rounded-full bg-primary opacity-60 [animation-duration:1.6s]" />
          <span className="relative size-2 rounded-full bg-primary" />
        </span>
        Running
      </span>
    );
  }
  if (state === "interrupted") {
    return (
      <span className={cn(base, "bg-warning-soft px-1.5 text-warning")}>
        <TriangleAlertIcon aria-hidden className="size-3.5" />
        Interrupted
      </span>
    );
  }
  if (state === "failed") {
    return (
      <span className={cn(base, "bg-threat-soft px-1.5 text-threat")}>
        <CircleXIcon aria-hidden className="size-3.5" />
        Failed
      </span>
    );
  }
  if (state === "succeeded") {
    return (
      <span className={cn(base, "text-muted-foreground")}>
        <CircleCheckIcon aria-hidden className="size-3.5 text-primary-ink" />
        Succeeded
      </span>
    );
  }
  // A status this UI doesn't know yet: neutral, never the success check.
  return (
    <span className={cn(base, "text-muted-foreground")}>
      <CircleDashedIcon aria-hidden className="size-3.5" />
      {state.charAt(0).toUpperCase() + state.slice(1).replace(/_/g, " ")}
    </span>
  );
}
