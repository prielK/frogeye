import { Fragment, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { scopeLabel, signalTypeLabel, STANCE } from "./meta";
import type { SignalCard } from "./query";
import { StanceLabel } from "./StanceLabel";

/**
 * A signal's classification, stance · type · scope. It follows the title, never sits above it:
 * a headline never wears a label above it. The full form (the drawer header, the analysis page
 * header) says every field, neutral stance included. `compact` (the Explorer's rows: one
 * helper, not a copy) leaves the unremarkable unsaid: a neutral stance and the competitor scope.
 */
export function Classification({
  signal,
  compact = false,
  className,
}: {
  signal: Pick<SignalCard, "stance" | "signal_type" | "scope">;
  compact?: boolean;
  className?: string;
}) {
  const kinds = [signalTypeLabel(signal.signal_type), compact && signal.scope === "competitor" ? null : scopeLabel(signal.scope)].filter(
    (w): w is string => Boolean(w),
  );
  const parts: ReactNode[] = [];
  const stance = signal.stance && STANCE[signal.stance] && !(compact && signal.stance === "neutral");
  if (stance) parts.push(<StanceLabel key="stance" stance={signal.stance} showNeutral />);
  for (const word of kinds) parts.push(<span key={word}>{word}</span>);
  if (parts.length === 0) return null;
  return (
    <span className={cn("inline-flex flex-wrap items-center", compact ? "gap-x-1.5" : "gap-x-2 gap-y-1", className)}>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 ? <span aria-hidden>·</span> : null}
          {part}
        </Fragment>
      ))}
    </span>
  );
}
