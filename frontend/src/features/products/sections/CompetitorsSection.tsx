import { Link } from "react-router";
import { CompetitorChip } from "@/components/common/CompetitorChip";
import { INLINE_LINK } from "@/components/common/links";
import { analysisHref } from "@/components/signal/param";
import { HowComputed, MetaLine, ProfileSection, QuietNote } from "@/features/intel/grammar";
import { ThreatChip } from "@/features/intel/ThreatChip";
import { dayMonth } from "@/features/intel/words";
import { formatCount, plural } from "@/lib/format";
import { useRules } from "@/lib/intelReference";
import { cn } from "@/lib/utils";
import { OVERLAP_CHIP, OVERLAP_WORD } from "@/features/intel/levels";
import { competitorsFormula, competitorsSummary, type ProductCompetitor } from "../useProducts";
import { section, type SectionProps } from "./shared";

/*
 * Competitors: who competes with it and how hard. A table from `lg` (four columns need the room
 * the sidebar leaves there); below, each row reflows into a block with its cells labelled in
 * place, so a laptop or a phone never scrolls it sideways.
 */

const HEAD = "h-9 px-3 text-left align-bottom text-meta font-medium whitespace-nowrap text-muted-foreground first:pl-0";
const CELL = "px-3 py-3.5 align-top first:pl-0 max-lg:block max-lg:p-0";
const LABEL = "mb-1 block text-meta text-muted-foreground lg:hidden";

function CompetitorRow({ competitor: c, product, nameOf }: { competitor: ProductCompetitor; product: string; nameOf: (slug: string) => string }) {
  const days = useRules().threat.lookback_days;
  const move = c.last_move;
  return (
    <tr role="row" className="border-b last:border-b-0 max-lg:grid max-lg:grid-cols-2 max-lg:gap-x-4 max-lg:gap-y-3 max-lg:py-4">
      <td role="cell" className={cn(CELL, "max-lg:col-span-2")}>
        <CompetitorChip slug={c.competitor_slug} name={nameOf(c.competitor_slug)} self={false} />
      </td>
      <td role="cell" className={CELL}>
        <span className={LABEL}>Their products</span>
        {c.their_products.length > 0 ? (
          <ul className="flex flex-col gap-0.5">
            {c.their_products.map((p) => (
              <li key={p} className="text-body text-foreground">
                {p}
              </li>
            ))}
          </ul>
        ) : (
          <span className="text-meta text-muted-foreground">No product of theirs mapped</span>
        )}
      </td>
      <td role="cell" className={CELL}>
        <span className={LABEL}>{`Threat to ${product}`}</span>
        <span className="flex flex-col items-start gap-1">
          <ThreatChip level={OVERLAP_CHIP[c.threat_level]} data-overlap={c.threat_level}>
            {/* The threat counts threat entries only, so evidence of another effect is "no threat named", never "no evidence" */}
            {c.threat_level === "none" && c.evidence > 0 ? "No threat named" : OVERLAP_WORD[c.threat_level]}
          </ThreatChip>
          {c.evidence > 0 ? <span className="text-meta text-muted-foreground">{`evidence ${formatCount(c.evidence)}`}</span> : null}
        </span>
      </td>
      <td role="cell" className={cn(CELL, "max-lg:col-span-2")}>
        <span className={LABEL}>Last move</span>
        {move ? (
          <span className="flex flex-col gap-0.5">
            <Link to={analysisHref(move.signal_id)} className={cn(INLINE_LINK, "line-clamp-3 text-body text-pretty text-foreground")}>
              {move.title}
            </Link>
            <span className="text-meta text-muted-foreground">
              <MetaLine parts={[move.lane, dayMonth(move.day)]} />
            </span>
          </span>
        ) : (
          <span className="text-meta text-muted-foreground">{`No move naming it in ${plural(days, "day")}`}</span>
        )}
      </td>
    </tr>
  );
}

export function CompetitorsSection({ profile, nameOf }: SectionProps) {
  const rules = useRules();
  const name = profile.name;
  const comps = profile.competitors;
  return (
    <ProfileSection section={section("competitors")} meta={`Threat to ${name}, ${plural(rules.overlap.lookback_days, "day")} of public signals`}>
      {comps.length === 0 ? (
        <QuietNote
          note={`No competitor is mapped to ${name} yet`}
          hint={`Competitors appear here when FrogEye's seed maps one of their products to ${name}, or when a public signal's analysis names ${name}.`}
        />
      ) : (
        <>
          <p className="max-w-[72ch] text-body text-pretty text-muted-foreground">{competitorsSummary(comps, name, nameOf, rules.overlap.lookback_days)}</p>
          <table role="table" aria-label="Competitors" className="w-full table-fixed border-collapse text-body max-lg:block">
            <colgroup>
              <col className="w-[22%]" />
              <col className="w-[22%]" />
              <col className="w-[11.5rem]" />
              <col />
            </colgroup>
            <thead role="rowgroup" className="border-b max-lg:hidden">
              <tr role="row">
                <th role="columnheader" className={HEAD}>
                  Competitor
                </th>
                <th role="columnheader" className={HEAD}>
                  Their products
                </th>
                <th role="columnheader" className={HEAD}>
                  {`Threat to ${name}`}
                </th>
                <th role="columnheader" className={HEAD}>
                  Last move
                </th>
              </tr>
            </thead>
            <tbody role="rowgroup" className="max-lg:block">
              {comps.map((c) => (
                <CompetitorRow key={c.competitor_slug} competitor={c} product={name} nameOf={nameOf} />
              ))}
            </tbody>
          </table>
          <HowComputed>
            <p>{competitorsFormula(name, rules)}</p>
          </HowComputed>
        </>
      )}
    </ProfileSection>
  );
}
