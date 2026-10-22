import { ClockIcon, NewspaperIcon, ScissorsLineDashedIcon, type LucideIcon } from "lucide-react";
import type { AnalysisState } from "./query";

/** The states in which a story is read from its stage-A first take, without a full analysis. */
export type NoAnalysisState = Exclude<AnalysisState, "current" | "stale">;

/*
 * the ONE copy source for why a story has no full analysis, used by the analysis page,
 * the drawer and the briefing's inline mark (StoryMeta). The API gives the reason from the rule
 * stage-B selection uses, so the words never promise an analysis that will not be written.
 */
export const NO_ANALYSIS: Record<NoAnalysisState, { icon: LucideIcon; title: string; text: string }> = {
  pending: {
    icon: ClockIcon,
    title: "Full analysis pending",
    text: "FrogEye's agents haven't written the full analysis yet. Until they do, this is their first read of the story.",
  },
  thin: {
    icon: NewspaperIcon,
    title: "Limited evidence: headline only",
    text: "Every source so far is a headline or a few lines without the article text, so there is no full analysis. This is what the sources support.",
  },
  below_threshold: {
    icon: ScissorsLineDashedIcon,
    title: "Short take: not analysed in full",
    text: "FrogEye writes a full analysis for its most important stories, and this one ranks below that line. This is its first read.",
  },
};

export function hasNoAnalysis(state: AnalysisState | undefined): state is NoAnalysisState {
  return state !== undefined && state in NO_ANALYSIS;
}
