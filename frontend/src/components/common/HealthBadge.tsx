import { CircleCheckIcon, CircleXIcon, TriangleAlertIcon, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type Health = "ok" | "degraded" | "down";

const HEALTH: Record<Health, { label: string; icon: LucideIcon; className: string }> = {
  // Healthy is the common case: brand-green mark, no fill, so problems are what stand out.
  ok: { label: "OK", icon: CircleCheckIcon, className: "text-muted-foreground [&>svg]:text-primary-ink" },
  degraded: { label: "Degraded", icon: TriangleAlertIcon, className: "bg-warning-soft text-warning" },
  down: { label: "Down", icon: CircleXIcon, className: "bg-threat-soft text-threat" },
};

function isHealth(value: string): value is Health {
  return value in HEALTH;
}

/** Source health as icon + word, never color alone: ok (brand), degraded (amber), down (threat). */
export function HealthBadge({ health, className }: { health: string; className?: string }) {
  const meta = isHealth(health) ? HEALTH[health] : null;
  const Icon = meta?.icon ?? CircleXIcon;
  return (
    <span
      data-health={health}
      className={cn(
        "inline-flex h-5 w-fit shrink-0 items-center gap-1 rounded-sm px-1.5 text-meta font-medium whitespace-nowrap",
        meta?.className ?? "bg-neutral-soft text-neutral",
        health === "ok" && "px-0",
        className,
      )}
    >
      <Icon aria-hidden className="size-3.5 shrink-0" />
      {meta?.label ?? health}
    </span>
  );
}
