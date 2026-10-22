import { Link } from "react-router";
import { orgInitials } from "@/lib/competitors";
import { cn } from "@/lib/utils";

type CompetitorChipProps = {
  slug: string;
  name: string;
  /** JFrog itself: its mark carries the brand tint, and it never links (it has no profile). */
  self?: boolean;
  /** Makes the chip a button, e.g. "show only this competitor". */
  onSelect?: () => void;
  /** Accessible name for the button form. Defaults to the competitor's name. */
  actionLabel?: string;
  /**
   * Text only: for a chip inside a link (a briefing card, a "This week" line) or beside filter
   * buttons, where a link would be nested or read as a filter. Otherwise the chip opens the
   * competitor's profile.
   */
  plain?: boolean;
  className?: string;
};

const SHAPE =
  "inline-flex h-6 max-w-full min-w-0 items-center gap-1.5 rounded-full border bg-card py-0 pr-2.5 pl-[0.1875rem] text-meta font-medium text-foreground";
const CONTROL = cn(
  "cursor-pointer transition-[border-color,background-color] duration-150 ease-out-quart",
  "hover:border-input-hover hover:bg-accent active:bg-secondary",
  "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none",
);

/**
 * A company named in the evidence: an initials mark and its name. Wherever it is not a control
 * already, it links to the competitor's profile (`/competitors/:slug`); JFrog never links.
 */
export function CompetitorChip({ slug, name, self = slug === "jfrog", onSelect, actionLabel, plain = false, className }: CompetitorChipProps) {
  const body = (
    <>
      <span
        aria-hidden
        className={cn(
          "flex size-[1.125rem] shrink-0 items-center justify-center rounded-full text-[0.5625rem] leading-none font-semibold tracking-[0.02em]",
          self ? "bg-primary-soft text-primary-ink" : "bg-secondary text-secondary-foreground",
        )}
      >
        {orgInitials(name)}
      </span>
      <span className="truncate">{name}</span>
    </>
  );
  const shape = cn(SHAPE, className);

  if (onSelect) {
    return (
      <button type="button" data-slug={slug} onClick={onSelect} aria-label={actionLabel ?? name} className={cn(shape, CONTROL)}>
        {body}
      </button>
    );
  }
  if (plain || self) {
    return (
      <span data-slug={slug} className={shape}>
        {body}
      </span>
    );
  }
  return (
    <Link to={`/competitors/${slug}`} data-slug={slug} className={cn(shape, CONTROL)}>
      {body}
    </Link>
  );
}
