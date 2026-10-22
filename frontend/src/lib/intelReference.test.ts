import { intelReference } from "@/test/fixtures";
import { collectedWithin, firstBaselineDay, ratio, trendWord, youngCollection } from "./intelReference";

/*
 * the competitor and product pages keep no copy of the rules the
 * API computes with. JFrog's products, the severity weights, the overlap, threat, trend and brief
 * thresholds and the weekly windows come from GET /intel-reference, so a page never states a
 * threshold the backend no longer uses. The guard scans the pages' source (comments left out: a
 * doc comment's worked example is not a rule the page states).
 */

const SOURCES = import.meta.glob<string>(["/src/features/competitors/**/*.{ts,tsx}", "/src/features/products/**/*.{ts,tsx}", "/src/features/intel/**/*.{ts,tsx}", "/src/features/compare/**/*.{ts,tsx}", "/src/lib/products.ts", "/src/components/common/ProductLink.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
});

/** The source without its comments (block, JSDoc and line comments). */
const code = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

const COPIES: [RegExp, string][] = [
  [/"AppTrust"|"MCP Registry"/, "a copy of JFROG_PRODUCTS"],
  [/\b(?:RISING|EASING|RECENT_WEEKS|LEVEL_WEEKS|BRIEF_MIN_SIGNALS)\s*=\s*[\d.]+/, "a threshold constant"],
  [/\b1\.25×|\b0\.8×|\bat most 3×/, "a trend threshold in words"],
  [/high 3, medium 2, low 1/, "the severity weights in words"],
  [/from a score of \d|[Hh]igh from \d|medium from \d/, "a level threshold in words"],
  [/0\.5 and 2\.0|±0\.25/, "the threat factor bounds"],
  [/\b\d+ public signals? in \d+ days\b|\(\d+ needed\)/, "the evidence floor in words"],
  [/\b(?:13|4|9) weeks\b|\bthe 9 before\b|\b9 weeks before\b/, "a trend window in words"],
  [/\b180 days\b|\b179\b/, "the lookback in words or days"],
  [/\b30 days\b|\b30-day\b|-30\)/, "the hiring history window"],
];

it("the competitor and product pages keep no copy of the API's rules", () => {
  const files = Object.entries(SOURCES).filter(([path]) => !/\.test\.[^/]+$/.test(path));
  expect(files.length).toBeGreaterThan(20);
  const offenders = files.flatMap(([path, source]) =>
    code(source)
      .split("\n")
      .flatMap((line, i) => COPIES.filter(([pattern]) => pattern.test(line)).map(([, what]) => `${path}:${i + 1} ${what}: ${line.trim().slice(0, 100)}`)),
  );
  expect(offenders).toEqual([]);
});

it("words a trend by the thresholds the API serves", () => {
  const rules = intelReference().trend;
  expect([trendWord(1.25, rules), trendWord(1.24, rules), trendWord(0.81, rules), trendWord(0.8, rules)]).toEqual(["rising", "steady", "steady", "easing"]);
  const strict = { ...rules, rising_ratio: 2, easing_ratio: 0.5 };
  expect([trendWord(1.6, strict), trendWord(2, strict), trendWord(0.6, strict), trendWord(0.5, strict)]).toEqual(["steady", "rising", "steady", "easing"]);
  expect([ratio(1.25), ratio(0.8), ratio(3)]).toEqual(["1.25×", "0.8×", "3×"]);
});

it("says collection is young exactly when the API's has_baseline would not hold", () => {
  const rules = intelReference().trend;
  // 13 whole weeks ending 20 Oct start on 22 Jul (today − 90 days).
  expect(youngCollection(null, "2026-10-20", rules)).toBe(false);
  expect(youngCollection("2026-07-22", "2026-10-20", rules)).toBe(false);
  expect(youngCollection("2026-07-23", "2026-10-20", rules)).toBe(true);
  expect(youngCollection("2026-09-30", "2026-10-20", rules)).toBe(true);
  expect(firstBaselineDay("2026-09-30", rules)).toBe("2026-12-29");
  expect(youngCollection("2026-09-30", "2026-12-29", rules)).toBe(false);
  expect(youngCollection("2026-09-30", "2026-12-28", rules)).toBe(true);
  // A shorter configured window follows the served weeks.
  expect(youngCollection("2026-09-30", "2026-10-20", { ...rules, weeks: 3 })).toBe(false);
});

it("says when collection began inside a window of days (the 180-day lanes)", () => {
  expect(collectedWithin(null, "2026-10-20", 180)).toBe(false);
  // 180 days ending 20 Oct start on 24 Apr.
  expect(collectedWithin("2026-04-24", "2026-10-20", 180)).toBe(false);
  expect(collectedWithin("2026-04-25", "2026-10-20", 180)).toBe(true);
  expect(collectedWithin("2026-09-30", "2026-10-20", 180)).toBe(true);
});
