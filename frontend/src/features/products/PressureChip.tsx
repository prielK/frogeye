import { ArrowDownRightIcon, ArrowRightIcon, ArrowUpRightIcon, type LucideIcon } from "lucide-react";
import { useRules } from "@/lib/intelReference";
import { cn } from "@/lib/utils";
import { CHIP } from "@/features/intel/levels";
import { pressureLook, windowWords, type PressureLook, type ProductSummary } from "./useProducts";

/**
 * A product's pressure as a mark and words, the threat chip's grammar (ThreatChip) so the two deep
 * dives read alike: rising is an up-right arrow in the threat tone on its soft ground, steady a
 * level arrow in ink, easing a down-right arrow muted, and no pressure says so in full on
 * the kit's "nothing measured" dashed edge. Pressure with no baseline yet (FrogEye has
 * collected for less than the trend's weeks) is ink with no arrow at all, since no direction is
 * known. Never colour alone: the arrow and the words carry it.
 */
const LOOK: Record<Exclude<PressureLook, "none">, { word: string; icon?: LucideIcon; className: string }> = {
  rising: { word: "Rising pressure", icon: ArrowUpRightIcon, className: "border-threat/30 bg-threat-soft text-threat" },
  baseline: { word: "Pressure, no baseline yet", className: "border-input bg-card text-foreground" },
  steady: { word: "Steady pressure", icon: ArrowRightIcon, className: "border-input bg-card text-foreground" },
  easing: { word: "Easing pressure", icon: ArrowDownRightIcon, className: "border-input bg-card text-muted-foreground" },
};
// The long words may wrap in a narrow column rather than run past it.
const NONE = "h-auto min-h-5 border-dashed border-input py-px whitespace-normal text-muted-foreground";

type PressureChipProps = {
  product: Pick<ProductSummary, "pressure" | "pressure_baseline">;
  /** The page's collected-since day (read from one place per page) and whether it is young. */
  collectedSince: string | null;
  young: boolean;
  className?: string;
};

export function PressureChip({ product, collectedSince, young, className }: PressureChipProps) {
  const rules = useRules();
  const look = pressureLook(product);
  if (look === "none") {
    return (
      <span data-pressure="none" className={cn(CHIP, "max-w-full", NONE, className)}>
        {`No competitive pressure ${windowWords(young, collectedSince, rules)}`}
      </span>
    );
  }
  const { word, icon: Icon, className: tone } = LOOK[look];
  return (
    <span data-pressure={look} className={cn(CHIP, "max-w-full", tone, className)}>
      {Icon ? <Icon aria-hidden className="size-3 shrink-0" strokeWidth={2.25} /> : null}
      {word}
    </span>
  );
}
