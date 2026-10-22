import { cn } from "@/lib/utils";
import { STANCE } from "./meta";

/** Stance as icon + word, never color alone. Neutral is left unsaid unless asked for. */
export function StanceLabel({ stance, showNeutral = false, className }: { stance: string | null; showNeutral?: boolean; className?: string }) {
  const meta = stance ? STANCE[stance] : undefined;
  if (!meta || (stance === "neutral" && !showNeutral)) return null;
  const Icon = meta.icon;
  return (
    <span data-stance={stance} className={cn("inline-flex items-center gap-1 font-medium whitespace-nowrap", meta.className, className)}>
      <Icon aria-hidden className="size-3.5 shrink-0" />
      {meta.label}
    </span>
  );
}
