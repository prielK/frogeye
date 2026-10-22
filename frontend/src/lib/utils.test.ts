import { buttonVariants } from "@/components/ui/button";
import css from "@/styles/globals.css?raw";
import { cn, THEME_SCALES } from "./utils";

/*
 * `cn` must know FrogEye's own theme scales. Plain tailwind-merge reads `text-meta` as a
 * colour (so `text-meta text-threat` lost the size), `shadow-floating` as a shadow colour and
 * the two custom easings as unknown. The scales are read from the stylesheet's @theme block,
 * so a token added there without telling `cn` fails here.
 */

const theme = css.slice(css.indexOf("@theme inline {"));
const tokens = (prefix: string) =>
  [...theme.matchAll(new RegExp(`^\\s*--${prefix}-([a-z0-9-]+):`, "gm"))].map((m) => m[1]!).filter((name) => !name.includes("--"));

it("the type scale has exactly four sizes", () => {
  expect(tokens("text")).toEqual(["meta", "body", "title", "display"]);
});

it("knows every custom font size, shadow and easing in globals.css", () => {
  expect([...THEME_SCALES.text]).toEqual(tokens("text"));
  expect([...THEME_SCALES.shadow]).toEqual(tokens("shadow"));
  expect([...THEME_SCALES.ease]).toEqual(tokens("ease"));
});

/*
 * The four sizes are the only sizes. Every source file is scanned for a size
 * that bypasses them: an arbitrary `text-[…]` length, a Tailwind default size, or a retired
 * role. Responsive and state variants (`sm:text-…`) are uses too, so they are caught as well.
 * the two graphics keep their own sizes, as exact (file, class) pairs, not whole files.
 */
const SOURCES = import.meta.glob<string>("/src/**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true });
const ALLOWED = new Set([
  "/src/components/layout/Wordmark.tsx text-[1.625rem]",
  "/src/components/layout/Wordmark.tsx text-[1.1875rem]",
  "/src/components/common/CompetitorChip.tsx text-[0.5625rem]",
]);
const OTHER_SIZES = [
  /text-\[[0-9.]+(?:rem|px|em)\]/g,
  /\btext-(?:xs|sm|base|lg|xl|[2-9]xl)\b/g,
  /\btext-(?:caption|ui|lede|headline)\b/g,
];

it("no other text size is used", () => {
  const files = Object.entries(SOURCES).filter(([path]) => !/\.test\.[^/]+$/.test(path));
  expect(files.length).toBeGreaterThan(50);
  const offenders = files.flatMap(([path, source]) =>
    OTHER_SIZES.flatMap((pattern) => [...source.matchAll(pattern)].map((m) => `${path} ${m[0]}`)),
  );
  expect(offenders.filter((use) => !ALLOWED.has(use)), "a size outside display / title / body / meta").toEqual([]);
  // The allowlist stays exact: each exempt pair is still in use (else drop it).
  expect([...ALLOWED].filter((pair) => !offenders.includes(pair))).toEqual([]);
});

it("keeps a type-scale size beside a text colour, in either order", () => {
  for (const size of THEME_SCALES.text) {
    expect(cn(`text-${size}`, "text-threat")).toBe(`text-${size} text-threat`);
    expect(cn("text-muted-foreground", `text-${size}`)).toBe(`text-muted-foreground text-${size}`);
  }
});

it("still dedupes two sizes, two colours, two shadows and two easings", () => {
  expect(cn("text-meta", "text-body")).toBe("text-body");
  expect(cn("text-display", "text-title")).toBe("text-title");
  expect(cn("text-meta", "text-sm")).toBe("text-sm");
  expect(cn("text-title", "sm:text-display")).toBe("text-title sm:text-display");
  expect(cn("text-foreground", "text-threat")).toBe("text-threat");
  expect(cn("shadow-sm", "shadow-floating")).toBe("shadow-floating");
  expect(cn("shadow-floating", "shadow-primary")).toBe("shadow-floating shadow-primary");
  expect(cn("ease-out", "ease-out-quart")).toBe("ease-out-quart");
  expect(cn("ease-out-quart", "ease-out-expo")).toBe("ease-out-expo");
});

it("lets a primitive's size and colour survive a caller's className", () => {
  const xsLink = buttonVariants({ variant: "link", size: "xs" });
  expect(xsLink).toContain("text-primary-ink");
  expect(xsLink).toContain("text-meta");
  expect(cn("text-meta font-medium", "text-foreground")).toBe("text-meta font-medium text-foreground");
});
