import type { ProductProfile } from "../useProducts";

/*
 * The product deep dive's sections, in page order (one file each): the
 * cited bottom line beside the key figures, who competes with it and how hard, its pressure over
 * time, gaps and parity, what every team should do, competitor hiring in its area, the moves
 * naming it by competitor, and the sources. They share the competitor deep dive's grammar
 * (features/intel: `ProfileSection`, `QuietNote`, `NoteState`, `HowComputed`), so the two deep
 * dives read as one product. Every section with nothing to show says why, and never shows a 0
 * nobody measured. Nothing here is a JFrog company figure.
 */

export const PRODUCT_SECTIONS = [
  { id: "bottom-line", label: "Bottom line" },
  { id: "competitors", label: "Competitors" },
  { id: "pressure", label: "Pressure over time" },
  { id: "gaps", label: "Gaps and parity" },
  { id: "what-to-do", label: "What to do" },
  { id: "hiring", label: "Competitor hiring in this area" },
  { id: "timeline", label: "Signals timeline" },
  { id: "sources", label: "Sources" },
] as const;

export const section = (id: (typeof PRODUCT_SECTIONS)[number]["id"]) => PRODUCT_SECTIONS.find((s) => s.id === id)!;

export type SectionProps = {
  profile: ProductProfile;
  /** The app-zone day the page reads as of. */
  today: string;
  /** A competitor's name from the index (a readable slug while names load). */
  nameOf: (slug: string) => string;
  /** FrogEye has collected for less than the trend's weeks, from the page's one collected-since day. */
  young: boolean;
};

export const QUIET = "text-body text-pretty text-muted-foreground";
/** What a brief-dependent section says without a brief: the Bottom line holds the one note. */
export const COMES_WITH_BRIEF = "Comes with the product brief; see the bottom line above.";
