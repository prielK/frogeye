import { CompetitorChip } from "@/components/common/CompetitorChip";
import { nameFromSlug, useCompetitorIndex } from "@/lib/competitors";
import { cn } from "@/lib/utils";

type CompanyChipsProps = {
  slugs: string[];
  max?: number;
  /** Makes each chip a filter button ("Show only Sonatype"), as on the Feed. */
  onSelect?: (slug: string) => void;
  /** The company already filtered to: its chip is not offered as a filter again. */
  active?: string;
  /** Text only, for chips inside a link (a briefing card or story row): a link may not nest. */
  plain?: boolean;
  className?: string;
};

/**
 * The companies a signal names, as chips named from the competitor index; past `max`, a "+N".
 * Each chip opens its competitor's profile, unless the chips are filters (`onSelect`; the one
 * already filtered to stays text beside them, never a link among filters) or sit inside a link
 * (`plain`). JFrog's chip never links: it has no profile.
 */
export function CompanyChips({ slugs, max = 4, onSelect, active, plain = false, className }: CompanyChipsProps) {
  const index = useCompetitorIndex();
  if (slugs.length === 0) return null;
  const shown = slugs.slice(0, max);
  const hidden = slugs.length - shown.length;
  return (
    <ul aria-label="Companies" className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {shown.map((slug) => {
        const name = index.get(slug)?.name ?? nameFromSlug(slug);
        return (
          <li key={slug} className="min-w-0">
            <CompetitorChip
              slug={slug}
              name={name}
              self={index.get(slug)?.is_self}
              onSelect={onSelect && slug !== active ? () => onSelect(slug) : undefined}
              actionLabel={`Show only ${name}`}
              plain={plain || onSelect !== undefined}
            />
          </li>
        );
      })}
      {hidden > 0 ? <li className="text-meta text-muted-foreground">+{hidden}</li> : null}
    </ul>
  );
}
