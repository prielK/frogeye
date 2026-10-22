import { useQuery } from "@tanstack/react-query";
import type { Schemas } from "@/lib/api/client";
import { costsQuery } from "@/lib/api/queries";
import { formatPercent } from "@/lib/format";

export type CostsOut = Schemas["CostsOut"];

/** AI spend against both caps (`GET /costs`, every role). Same key as the Briefing's budget read: one cache entry. */
export function useCosts() {
  return useQuery(costsQuery());
}

/** From this share of a cap on, the meter says so: there is room for about one more run. */
export const NEAR_CAP = 0.8;

export type BudgetState = "ok" | "near" | "reached";

/** Where spend stands against its cap; a cap of zero or less counts as reached (nothing may be spent). */
export function budgetState(spent: number, cap: number): BudgetState {
  if (cap <= 0 || spent >= cap) return "reached";
  return spent / cap >= NEAR_CAP ? "near" : "ok";
}

/** "83%", never more than the bar can show without saying so ("104%" when spend ran past the cap). */
export function capShare(spent: number, cap: number): string {
  return cap > 0 ? formatPercent(spent / cap) : "100%";
}
