import { useMemo } from "react";
import { SelectGroup, SelectItem, SelectLabel, SelectSeparator } from "@/components/ui/select";
import type { Competitor } from "@/lib/api/client";

/** Companies by tier, lowest first, names A–Z within each tier. JFrog itself is never one. */
function groupByTier(competitors: Competitor[]): { tier: number; list: Competitor[] }[] {
  const byTier = new Map<number, Competitor[]>();
  for (const c of competitors) if (!c.is_self) byTier.set(c.tier, [...(byTier.get(c.tier) ?? []), c]);
  return [...byTier.entries()]
    .sort(([a], [b]) => a - b)
    .map(([tier, list]) => ({ tier, list: [...list].sort((a, b) => a.name.localeCompare(b.name)) }));
}

/**
 * The company options of a competitor filter (Feed, Signals Explorer, Search everything): one
 * group per tier, "Tier N" labelled, each opening on a separator. JFrog itself is left out: it is never a signal's company and its own news is never listed, so the option could
 * only lead to an empty list. Place it inside a SelectContent after the caller's "All companies".
 */
export function CompetitorSelectItems({ competitors }: { competitors: Competitor[] | undefined }) {
  const tiers = useMemo(() => groupByTier(competitors ?? []), [competitors]);
  return tiers.map(({ tier, list }) => (
    <SelectGroup key={tier}>
      <SelectSeparator />
      {tier > 0 ? <SelectLabel>Tier {tier}</SelectLabel> : null}
      {list.map((c) => (
        <SelectItem key={c.slug} value={c.slug}>
          {c.name}
        </SelectItem>
      ))}
    </SelectGroup>
  ));
}
