import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/* "Why ranked here": a signal's importance score, factor by factor (backend agents/nodes/score.py). */

const COMPONENTS: { key: string; label: string; hint: string }[] = [
  { key: "impact", label: "Impact", hint: "How much it changes things for JFrog, as the analyst agent scored it (1 to 5)." },
  { key: "novelty", label: "Novelty", hint: "How new it is against what FrogEye already knew (1 to 5)." },
  { key: "competitor_tier", label: "Who it's about", hint: "Tier-1 competitors weigh most; industry news and JFrog itself less." },
  { key: "type", label: "Kind of event", hint: "Acquisitions, launches and pricing changes weigh most." },
  { key: "kit_priority", label: "Key topics", hint: "Whether it bears on one of the CI team's key intelligence topics." },
  { key: "credibility", label: "Credibility", hint: "How well its sources hold up: the number in the Admiralty grade." },
  { key: "corroboration", label: "Corroboration", hint: "How many independent sources report it." },
];

type Component = { value: number; weight: number; contribution: number };

function asComponent(raw: unknown): Component | null {
  if (!raw || typeof raw !== "object") return null;
  const { value, weight, contribution } = raw as Record<string, unknown>;
  if (typeof value !== "number" || typeof weight !== "number" || typeof contribution !== "number") return null;
  return { value, weight, contribution };
}

const points = (share: number) => Math.round(share * 100);

/** A signal whose sources are all headline-only is scored with a multiplier (< 1). */
function thinMultiplier(raw: unknown): number | null {
  if (!raw || typeof raw !== "object") return null;
  const { thin, multiplier } = raw as Record<string, unknown>;
  return thin === true && typeof multiplier === "number" ? multiplier : null;
}

/** A signal whose full analysis rates no JFrog product is scored with a multiplier (< 1). */
function noProductMultiplier(raw: unknown): number | null {
  if (!raw || typeof raw !== "object") return null;
  const { none, multiplier } = raw as Record<string, unknown>;
  return none === true && typeof multiplier === "number" ? multiplier : null;
}

function dayCount(days: number): string {
  if (days < 1) return "under a day old";
  const whole = Math.round(days);
  return `${whole} ${whole === 1 ? "day" : "days"} old`;
}

/*
 * The age the decay used: 24-hour days at the last scoring (every daily run re-scores recent
 * signals), counted from the event date, or from first seen when the story has none. It is
 * not the header's "First seen", which counts calendar days to now in the app's time zone.
 */
const AGE_FROM: Record<string, string> = { event_date: "from its event date", first_seen: "from first seen" };

function freshness(age: number | null, from: unknown): string {
  if (age === null) return "Freshness when scored";
  const basis = typeof from === "string" ? AGE_FROM[from] : undefined;
  return `Freshness when scored: ${dayCount(age)}${basis ? ` (${basis})` : ""}`;
}

/*
 * Form (dataviz): one bullet-style meter per factor on a common scale, so lengths compare
 * across rows. The track (--meter-track) is the most the factor can add, its weight; the fill
 * (--meter) is what it added. One series, so one hue and no legend; every value is printed
 * beside its bar and the tooltip adds the arithmetic. 8px bars, 4px rounded data end, square
 * baseline. --meter is validated at >= 3:1 on the drawer surface in both themes.
 */
export function ScoreBreakdown({ breakdown }: { breakdown: Record<string, unknown> }) {
  const raw = (breakdown.components ?? {}) as Record<string, unknown>;
  const rows = COMPONENTS.flatMap((c) => {
    const part = asComponent(raw[c.key]);
    return part ? [{ ...c, ...part }] : [];
  });
  if (rows.length === 0) {
    return <p className="text-body text-muted-foreground">Not scored yet: the score appears once the signal is analysed.</p>;
  }
  const scale = Math.max(...rows.map((r) => r.weight)) || 1;
  const total = rows.reduce((sum, r) => sum + r.contribution, 0);
  const decay = typeof breakdown.decay === "number" ? breakdown.decay : null;
  const age = typeof breakdown.age_days === "number" ? breakdown.age_days : null;
  const thin = thinMultiplier(breakdown.evidence_depth);
  const noProduct = noProductMultiplier(breakdown.product_impact);
  const importance =
    typeof breakdown.importance === "number"
      ? breakdown.importance
      : Math.round(100 * total * (decay ?? 1) * (thin ?? 1) * (noProduct ?? 1));
  // decay = 0.5 ** (age / half-life), so the half-life is recoverable without hard-coding config.
  const halfLife = decay !== null && age !== null && age > 0.05 && decay > 0 && decay < 1 ? (age * Math.log(0.5)) / Math.log(decay) : null;

  return (
    // A container, so the label column widens with the space it is given (a side column or a full-width panel).
    <div className="@container flex flex-col gap-3">
      <ul className="flex flex-col">
        {rows.map((r) => (
          <li key={r.key}>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={`${r.label}: ${points(r.contribution)} of ${points(r.weight)} points`}
                  className={cn(
                    "group grid w-full grid-cols-[7.5rem_minmax(0,1fr)_3.5rem] items-center gap-3 rounded-sm px-1.5 py-1.5 text-left @sm:grid-cols-[9rem_minmax(0,1fr)_3.75rem]",
                    "transition-colors duration-150 ease-out-quart hover:bg-accent focus-visible:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                  )}
                >
                  <span className="truncate text-meta text-muted-foreground group-hover:text-foreground">{r.label}</span>
                  <span aria-hidden className="relative h-2">
                    <span className="absolute inset-y-0 left-0 rounded-r-[4px] bg-meter-track" style={{ width: `${(r.weight / scale) * 100}%` }} />
                    {r.contribution > 0 ? (
                      <span
                        className="absolute inset-y-0 left-0 rounded-r-[4px] bg-meter"
                        style={{ width: `max(2px, ${(r.contribution / scale) * 100}%)` }}
                      />
                    ) : null}
                  </span>
                  <span data-numeric className="text-right text-meta">
                    <span className="font-semibold text-foreground">{points(r.contribution)}</span>
                    <span className="text-muted-foreground">/{points(r.weight)}</span>
                  </span>
                </button>
              </TooltipTrigger>
              <TooltipContent side="left" className="max-w-64 text-left">
                <span className="font-semibold">
                  {r.value.toFixed(2)} × weight {r.weight.toFixed(2)} = {points(r.contribution)} points.
                </span>{" "}
                {r.hint}
              </TooltipContent>
            </Tooltip>
          </li>
        ))}
      </ul>
      <div className="flex flex-col gap-1 border-t px-1.5 pt-3 text-meta text-muted-foreground">
        <p className="flex justify-between gap-4">
          <span>Score from these factors</span>
          <span data-numeric className="shrink-0 whitespace-nowrap text-foreground">
            {points(total)} of 100
          </span>
        </p>
        {decay !== null ? (
          <p className="flex justify-between gap-4">
            <span>
              {freshness(age, breakdown.age_from)}
              {halfLife ? `, the score halves every ${Math.round(halfLife)} days` : ""}
            </span>
            <span data-numeric className="shrink-0 whitespace-nowrap text-foreground">
              × {decay.toFixed(2)}
            </span>
          </p>
        ) : null}
        {thin !== null ? (
          <p className="flex justify-between gap-4">
            <span>Thin evidence: headlines only, no article text yet</span>
            <span data-numeric className="shrink-0 whitespace-nowrap text-foreground">
              × {thin.toFixed(2)}
            </span>
          </p>
        ) : null}
        {noProduct !== null ? (
          <p className="flex justify-between gap-4">
            <span>No JFrog product affected</span>
            <span data-numeric className="shrink-0 whitespace-nowrap text-foreground">
              × {noProduct.toFixed(2)}
            </span>
          </p>
        ) : null}
        <p className="flex justify-between gap-4 text-body font-semibold text-foreground">
          <span>Importance {importance}</span>
          <span className="font-normal text-muted-foreground">of 100</span>
        </p>
      </div>
    </div>
  );
}
