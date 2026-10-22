import type { ReactNode } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { CHIP, THREAT, type ThreatLevel } from "./levels";

/**
 * A level as the Exec risk chip's mark and word (high: an up triangle in the threat tone; medium:
 * a diamond; low: a down triangle; "insufficient": a dashed edge), never colour alone. The deep
 * dive reads its threat and each product's overlap with it; `children` replaces the word.
 */
export function ThreatChip({ level, children, className, ...data }: { level: ThreatLevel; children?: ReactNode; className?: string } & Record<`data-${string}`, string>) {
  const look = THREAT[level];
  const Icon = look.icon;
  return (
    <span data-threat={level} {...data} className={cn(CHIP, look.className, className)}>
      {Icon ? <Icon aria-hidden className={cn("size-2.5 shrink-0 fill-current", look.mark)} /> : null}
      {children ?? look.word}
    </span>
  );
}

/**
 * A competitor's threat as a 20px mark whose tooltip says how it was computed, in the API's words
 * (the competitor list and the comparison matrix's columns).
 */
export function ThreatMark({ threat }: { threat: Schemas["ThreatOut"] }) {
  const look = THREAT[threat.level];
  const Icon = look.icon;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          data-threat={threat.level}
          className={cn(
            "relative z-10 cursor-help",
            CHIP,
            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none",
            look.className,
          )}
        >
          {Icon ? <Icon aria-hidden className={cn("size-2.5 shrink-0 fill-current", look.mark)} /> : null}
          {look.word}
        </button>
      </TooltipTrigger>
      <TooltipContent side="top" align="start" className="max-w-80 text-left text-pretty">
        {/* The API's explanation only; "How this is computed" is the deep dives' disclosure, with the formula. */}
        <span className="mb-0.5 block font-semibold">Why this level</span>
        {threat.explanation}
      </TooltipContent>
    </Tooltip>
  );
}
