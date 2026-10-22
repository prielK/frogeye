import type { Schemas } from "@/lib/api/client";
import { productSlug } from "@/lib/products";
import { JFROG_PRODUCT_NAMES, localDay, threat } from "./fixtures";

/*
 * GET /compare as the tests serve it: the 13 products, four competitors with seeded products
 * (two tier 1, two tier 2) and a handful of pairings covering every way a cell reads: a high
 * threat with its evidence, a low one, evidence that names no threat, a seeded product with no
 * evidence, and a pairing named only by signals (no product of theirs mapped).
 */

type Compare = Schemas["CompareOut"];
type CompareCell = Schemas["CompareCellOut"];

const PRESSURE: Record<string, Schemas["CompareProductOut"]["pressure"]> = { Artifactory: "rising", Xray: "steady", Curation: "easing" };

export const COMPARE_COLUMNS = ["Sonatype", "GitHub", "Cloudsmith", "Socket"];

export function compareCell(overrides: Partial<CompareCell> & Pick<CompareCell, "product_slug" | "competitor_slug">): CompareCell {
  return { their_products: [], threat_level: "none", threat_weight: 0, evidence: 0, last_move: null, threat_signals: [], ...overrides };
}

export function compareMatrix(now: number = Date.now()): Compare {
  const move = (id: number, offset: number, title: string, lane: Schemas["TimelineEventOut"]["lane"] = "Launches") => ({ signal_id: id, day: localDay(offset, now), lane, importance: 70, title });
  return {
    as_of: localDay(0, now),
    lookback_days: 180,
    collected_since: null,
    products: JFROG_PRODUCT_NAMES.map((name) => ({ slug: productSlug(name), name, pressure: PRESSURE[name] ?? "none", pressure_baseline: true })),
    competitors: [
      { slug: "sonatype", name: "Sonatype", tier: 1, relationship: "competitor", threat: threat("high", { score: 18.4 }), product_categories: ["Artifact repository", "Software composition analysis"] },
      { slug: "github", name: "GitHub", tier: 1, relationship: "frenemy", threat: threat("medium"), product_categories: ["Artifact repository"] },
      { slug: "cloudsmith", name: "Cloudsmith", tier: 2, relationship: "competitor", threat: threat("low", { score: 2 }), product_categories: ["Artifact repository", "Supply chain security"] },
      { slug: "socket", name: "Socket", tier: 2, relationship: "competitor", threat: threat("insufficient"), product_categories: ["Supply chain security"] },
    ],
    cells: [
      compareCell({
        product_slug: "artifactory",
        competitor_slug: "sonatype",
        their_products: [{ name: "Nexus Repository", category: "Artifact repository", url: "https://www.sonatype.com/products/sonatype-nexus-repository" }],
        threat_level: "high",
        threat_weight: 7,
        evidence: 9,
        last_move: move(7001, -2, "Nexus adds a dependency cache"),
        threat_signals: [
          { ...move(7001, -2, "Nexus adds a dependency cache"), weight: 3, severity: "high" },
          { ...move(7002, -20, "Sonatype cuts Nexus Pro prices", "Pricing"), weight: 2, severity: "medium" },
          { ...move(7003, -40, "Nexus ships a Helm proxy"), weight: 2, severity: "medium" },
        ],
      }),
      compareCell({
        product_slug: "artifactory",
        competitor_slug: "github",
        their_products: [{ name: "GitHub Packages", category: "Artifact repository", url: "https://docs.github.com/en/packages" }],
        threat_level: "low",
        threat_weight: 1,
        evidence: 1,
        last_move: move(7010, -5, "GitHub Packages adds npm provenance"),
        threat_signals: [{ ...move(7010, -5, "GitHub Packages adds npm provenance"), weight: 1, severity: "low" }],
      }),
      compareCell({
        product_slug: "artifactory",
        competitor_slug: "cloudsmith",
        their_products: [{ name: "Cloudsmith artifact management", category: "Artifact repository", url: null }],
      }),
      compareCell({
        product_slug: "xray",
        competitor_slug: "sonatype",
        their_products: [
          { name: "Sonatype Lifecycle", category: "Software composition analysis", url: "https://www.sonatype.com/products/open-source-security-dependency-management" },
          { name: "Sonatype SBOM Manager", category: "SBOM management", url: "https://www.sonatype.com/products/sbom-manager" },
        ],
        evidence: 2,
        last_move: move(7020, -12, "Sonatype partners with an SBOM consortium", "Partners"),
      }),
      compareCell({
        product_slug: "xray",
        competitor_slug: "cloudsmith",
        threat_level: "medium",
        threat_weight: 3,
        evidence: 3,
        last_move: move(7030, -8, "Cloudsmith adds vulnerability scanning"),
        threat_signals: [{ ...move(7030, -8, "Cloudsmith adds vulnerability scanning"), weight: 3, severity: "high" }],
      }),
      compareCell({
        product_slug: "curation",
        competitor_slug: "socket",
        their_products: [{ name: "Socket", category: "Supply chain security", url: "https://socket.dev/" }],
      }),
    ],
  };
}

/** Nothing collected yet: the seed's pairings, none with evidence. */
export function quietMatrix(now: number = Date.now()): Compare {
  const m = compareMatrix(now);
  return { ...m, cells: m.cells.filter((c) => c.their_products.length > 0).map((c) => ({ ...c, threat_level: "none", threat_weight: 0, evidence: 0, last_move: null, threat_signals: [] })) };
}

/** Before the seed's competitor products are loaded: rows, and no column. */
export function emptyMatrix(now: number = Date.now()): Compare {
  return { ...compareMatrix(now), competitors: [], cells: [] };
}
