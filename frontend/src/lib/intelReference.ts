import { queryOptions, useQuery } from "@tanstack/react-query";
import { createContext, useContext } from "react";
import { api, ApiError, type Schemas } from "@/lib/api/client";
import { shiftDay } from "@/lib/days";

/*
 * The rules the competitor and product pages quote (`GET /intel-reference`):
 * JFrog's products in order, the severity weights, the overlap, threat, trend and
 * brief thresholds, and the day FrogEye began collecting. They come from the backend's own
 * constants and settings, so the pages keep no copies: a trend word, a "How this is computed"
 * formula or a "13 weeks" says what the API computes, whatever its configuration.
 *
 * A page that quotes them reads the reference with its own data and renders its view inside
 * `<RulesContext value={rules}>`; its sections read them with `useRules()`. A reading surface that only needs
 * the product list (a product name that links to its page) reads `useJfrogProducts()`, which is
 * empty-handed while the reference loads, so the name stays text until then.
 */

export type IntelRules = Schemas["IntelReferenceOut"];
export type TrendRules = IntelRules["trend"];

export function intelReferenceQuery() {
  return queryOptions({
    queryKey: ["intel-reference"],
    queryFn: async ({ signal }): Promise<IntelRules> => {
      const { data, response } = await api.GET("/api/v1/intel-reference", { signal });
      if (!data) throw new ApiError(response.status);
      return data;
    },
    // Configuration and one day: it changes with a deploy, or once, the first day anything is filed.
    staleTime: 10 * 60_000,
  });
}

export function useIntelReference() {
  return useQuery(intelReferenceQuery());
}

/** JFrog's products in the backend's order (JFROG_PRODUCTS), or undefined while the reference loads. */
export function useJfrogProducts(): readonly string[] | undefined {
  return useIntelReference().data?.jfrog_products;
}

/** The rules for everything inside it: a page renders `<RulesContext value={rules}>` once its reference has loaded. */
export const RulesContext = createContext<IntelRules | null>(null);

/** The page's rules (inside its `RulesContext`). */
export function useRules(): IntelRules {
  const rules = useContext(RulesContext);
  if (!rules) throw new Error("useRules() needs the page's RulesContext: the page reads GET /intel-reference first");
  return rules;
}

// ---- trends ----------------------------------------------------------------------------------

export type Trend = "rising" | "steady" | "easing";

/** A weekly trend in a word, by the API's thresholds: rising from `rising_ratio`, easing at or under `easing_ratio`. */
export function trendWord(trend: number, rules: TrendRules): Trend {
  return trend >= rules.rising_ratio ? "rising" : trend <= rules.easing_ratio ? "easing" : "steady";
}

/** "1.25×", "0.8×": a ratio as the formulas print it; the one trend formatter. */
export const ratio = (n: number) => `${Number(n.toFixed(2))}×`;

/**
 * A served bound or clamp as the API's explanation prints it (Python's `:g`): "0.5", "2", "0.25",
 * never "2.0", so the explanation and the formula beside it agree.
 */
export const clampFigure = (n: number) => String(Number(n.toFixed(6)));

// ---- collected since -------------------------------------------------------------------

/**
 * whether FrogEye has collected for less than the weekly window a trend compares (the API's
 * `has_baseline`: the window's first day, `weeks` whole 7-day windows back from `today`, falls
 * before the day collection began). Then the weekly series start at the week holding that day,
 * and every trend reads "no baseline yet" for that reason, not for want of signals.
 */
export function youngCollection(collectedSince: string | null, today: string, rules: TrendRules): boolean {
  return collectedSince !== null && shiftDay(today, -(7 * rules.weeks - 1)) < collectedSince;
}

/**
 * The collection start for a window of days (the 180-day swimlanes): whether collection began inside it, so its
 * earlier days are not quiet days but days FrogEye was not collecting.
 */
export function collectedWithin(collectedSince: string | null, today: string, days: number): boolean {
  return collectedSince !== null && shiftDay(today, 1 - days) < collectedSince;
}

/** The first day a trend can have its full collected baseline: `weeks` whole weeks after collection began. */
export function firstBaselineDay(collectedSince: string, rules: TrendRules): string {
  return shiftDay(collectedSince, 7 * rules.weeks - 1);
}
