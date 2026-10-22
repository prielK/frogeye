import { DiamondIcon, TriangleIcon, type LucideIcon } from "lucide-react";
import type { Schemas } from "@/lib/api/client";

/*
 * The level marks the intelligence pages share: the competitor list's threat,
 * the deep dives' overlap and a product's pressure all wear one 20px chip, so the two deep dives
 * read alike and a table refactor never breaks the product page. `ThreatChip` draws them.
 */

export type ThreatLevel = Schemas["ThreatOut"]["level"];

/** A threat level's look: the Exec risk chip's marks (up triangle, diamond, down triangle), so a level reads by shape and word. */
export const THREAT: Record<ThreatLevel, { word: string; icon?: LucideIcon; mark?: string; className: string }> = {
  high: { word: "High", icon: TriangleIcon, className: "border-threat/30 bg-threat-soft text-threat" },
  medium: { word: "Medium", icon: DiamondIcon, className: "border-input bg-card text-foreground" },
  low: { word: "Low", icon: TriangleIcon, mark: "rotate-180", className: "border-input bg-card text-muted-foreground" },
  // Not measured: the kit's "not known" idiom, a dashed edge, and the words.
  insufficient: { word: "Insufficient data", className: "border-dashed border-input text-muted-foreground" },
};

/** The 20px chip every level mark wears (threat, overlap, a product's pressure). */
export const CHIP = "inline-flex h-5 w-fit items-center gap-1 rounded-sm border px-1.5 text-meta font-medium whitespace-nowrap";

/**
 * An overlap level (a product's, or a competitor's threat to a JFrog product) as the threat chip
 * shows it: "none" is "No recent evidence" on the dashed edge, since the seed already says they
 * compete (the competitor's Products vs JFrog and the product page's Competitors read alike).
 */
export const OVERLAP_CHIP = { high: "high", medium: "medium", low: "low", none: "insufficient" } as const;
export const OVERLAP_WORD = { high: "High", medium: "Medium", low: "Low", none: "No recent evidence" } as const;
