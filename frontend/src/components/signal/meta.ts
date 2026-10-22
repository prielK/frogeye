import { BadgeCheckIcon, BotIcon, CircleDashedIcon, MinusIcon, ShieldAlertIcon, TrendingUpIcon, type LucideIcon } from "lucide-react";

/* Reader-facing words for a signal's fields. Meaning is always a word (with an icon), never color alone. */

const TYPE_LABEL: Record<string, string> = {
  product_launch: "Product launch",
  feature_update: "Feature update",
  deprecation: "Deprecation",
  pricing_change: "Pricing change",
  messaging_change: "Messaging change",
  partnership: "Partnership",
  funding: "Funding",
  acquisition: "Acquisition",
  earnings_financials: "Earnings",
  leadership_change: "Leadership change",
  hiring_trend: "Hiring trend",
  customer_win_loss: "Customer win or loss",
  security_incident: "Security incident",
  analyst_recognition: "Analyst recognition",
  community_buzz: "Community buzz",
  event_conference: "Event",
  legal_regulatory: "Legal or regulatory",
  industry_trend: "Industry trend",
  standard_spec: "Standard or spec",
  other: "Other",
};

/** "pricing_change" → "Pricing change". */
export function signalTypeLabel(type: string | null | undefined): string | null {
  if (!type) return null;
  return TYPE_LABEL[type] ?? type.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

const SCOPE_LABEL: Record<string, string> = { competitor: "Competitor", industry: "Industry", jfrog: "JFrog" };

export function scopeLabel(scope: string): string {
  return SCOPE_LABEL[scope] ?? scope;
}

export type Look = { label: string; icon: LucideIcon; className: string };

export const STANCE: Record<string, Look> = {
  threat: { label: "Threat", icon: ShieldAlertIcon, className: "text-threat" },
  opportunity: { label: "Opportunity", icon: TrendingUpIcon, className: "text-opportunity" },
  neutral: { label: "Neutral", icon: MinusIcon, className: "text-muted-foreground" },
};

/** Admiralty source reliability, A (best) to F. */
export const RELIABILITY: Record<string, string> = {
  A: "completely reliable",
  B: "usually reliable",
  C: "fairly reliable",
  D: "not usually reliable",
  E: "unreliable",
  F: "reliability cannot be judged",
};

/** Admiralty information credibility, 1 (best) to 6. */
export const CREDIBILITY: Record<number, string> = {
  1: "confirmed by other sources",
  2: "probably true",
  3: "possibly true",
  4: "doubtful",
  5: "improbable",
  6: "truth cannot be judged",
};

export type Trust = Look & { explain: string };

/** Trust states: non-analysts only ever see auto and approved. */
const TRUST: Record<string, Trust> = {
  approved: {
    label: "Analyst approved",
    icon: BadgeCheckIcon,
    className: "text-primary-ink",
    explain: "A CI analyst reviewed and approved this signal.",
  },
  auto: {
    label: "AI analysis",
    icon: BotIcon,
    className: "text-muted-foreground",
    explain: "Written by FrogEye's agents and checked against its sources; no analyst has reviewed it yet.",
  },
  needs_review: {
    label: "Needs review",
    icon: CircleDashedIcon,
    className: "text-warning",
    explain: "The agents weren't sure enough to publish this on their own; it waits for an analyst.",
  },
};

export function trustFor(status: string): Trust {
  return (
    TRUST[status] ?? {
      label: status.charAt(0).toUpperCase() + status.slice(1).replace(/_/g, " "),
      icon: CircleDashedIcon,
      className: "text-muted-foreground",
      explain: "Not part of what readers see.",
    }
  );
}

type Trusted = { status: string; reliability: string | null; credibility: number | null };

/** Higher is less trusted: the approval state first, then the Admiralty grade (F6 worst; ungraded worse still). */
function distrust(s: Trusted): [number, number, number] {
  const state = s.status === "approved" ? 0 : s.status === "auto" ? 1 : 2;
  const reliability = s.reliability ? "ABCDEF".indexOf(s.reliability) : 6;
  return [state, reliability < 0 ? 6 : reliability, s.credibility ?? 7];
}

/**
 * The least-trusted of several signals (a takeaway that cites more than one). Its grade AND
 * its trust state make the takeaway's trust line, so the line never mixes two signals' data.
 */
export function leastTrusted<T extends Trusted>(signals: T[]): T | undefined {
  return signals.reduce<T | undefined>((worst, s) => {
    if (!worst) return s;
    const [a, b] = [distrust(s), distrust(worst)];
    return a[0] !== b[0] ? (a[0] > b[0] ? s : worst) : a[1] !== b[1] ? (a[1] > b[1] ? s : worst) : a[2] > b[2] ? s : worst;
  }, undefined);
}
