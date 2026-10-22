import { useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { INLINE_LINK } from "@/components/common/links";
import { analysisHref } from "@/components/signal/param";
import { ExternalLink } from "@/components/signal/SourceLink";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { HowComputed, MetaLine } from "@/features/intel/grammar";
import { OVERLAP_CHIP } from "@/features/intel/levels";
import { ThreatChip } from "@/features/intel/ThreatChip";
import { dayLabel, dayMonth } from "@/features/intel/words";
import { formatCount, plural } from "@/lib/format";
import { useRules } from "@/lib/intelReference";
import { cn } from "@/lib/utils";
import { cellFormula, cellWord, type CompareCell, type CompareCompetitor, type CompareProduct } from "./useCompare";

/*
 * One pairing up close, in a right-hand sheet (the signal drawer's anatomy): JFrog's product
 * against one competitor, their competing products with the vendor's own page, the threat to the
 * product with every signal it is summed from (each opening its analysis), and the way on to
 * both deep dives. Opened by `?cell=<product>:<competitor>`, so a pairing can be shared.
 */

export type OpenCell = { cell: CompareCell; product: CompareProduct; competitor: CompareCompetitor };

const SEVERITY: Record<string, string> = { high: "High severity", medium: "Medium severity", low: "Low severity" };

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3 border-t border-foreground pt-3">
      <h3 id={id} className="flex items-baseline gap-2 font-headline text-title">
        {title}
        {count !== undefined ? <span className="font-sans text-meta font-normal text-muted-foreground">{count}</span> : null}
      </h3>
      {children}
    </section>
  );
}

function TheirProducts({ open }: { open: OpenCell }) {
  const { cell, product, competitor } = open;
  if (cell.their_products.length === 0) {
    return (
      <p className="text-body text-pretty text-muted-foreground">
        <span className="font-medium text-foreground">{`No product of ${competitor.name}'s is mapped to ${product.name}.`}</span>
        {` Its public signals name ${product.name}, so the pairing is listed.`}
      </p>
    );
  }
  return (
    <ul className="flex flex-col divide-y">
      {cell.their_products.map((p) => (
        <li key={p.name} className="flex flex-col gap-0.5 py-2.5 first:pt-0.5">
          {p.url ? (
            <ExternalLink href={p.url} className="w-fit text-body font-medium text-foreground">
              {p.name}
            </ExternalLink>
          ) : (
            <span className="text-body font-medium text-foreground">{p.name}</span>
          )}
          <span className="text-meta text-muted-foreground">
            <MetaLine parts={[p.category, p.url ? null : "No public page recorded"]} />
          </span>
        </li>
      ))}
    </ul>
  );
}

function Evidence({ open }: { open: OpenCell }) {
  const { cell, product } = open;
  const days = useRules().overlap.lookback_days;
  const signals = cell.threat_signals;
  const move = cell.last_move;
  const moveListed = move !== null && signals.some((s) => s.signal_id === move.signal_id);
  return (
    <>
      {signals.length === 0 ? (
        <p className="text-body text-pretty text-muted-foreground">
          {cell.evidence > 0
            ? `Its public signals name ${product.name} (evidence ${formatCount(cell.evidence)}), but none as a threat in the last ${plural(days, "day")}.`
            : `No public signal of the last ${plural(days, "day")} names ${product.name}.`}
        </p>
      ) : (
        <ol className="flex flex-col divide-y">
          {signals.map((s) => (
            <li key={s.signal_id} className="flex flex-col gap-0.5 py-2.5 first:pt-0.5">
              <Link to={analysisHref(s.signal_id)} className={cn(INLINE_LINK, "w-fit text-body font-medium text-pretty text-foreground")}>
                {s.title}
              </Link>
              <span className="text-meta text-muted-foreground">
                <MetaLine parts={[s.lane, dayLabel(s.day), SEVERITY[s.severity] ?? s.severity, `adds ${formatCount(s.weight)}`]} />
              </span>
            </li>
          ))}
        </ol>
      )}
      {move && !moveListed ? (
        <p className="text-meta text-pretty text-muted-foreground">
          {"Latest move naming it: "}
          <Link to={analysisHref(move.signal_id)} className={cn(INLINE_LINK, "font-medium text-foreground")}>
            {move.title}
          </Link>
          {` · ${move.lane} · ${dayMonth(move.day)}`}
        </p>
      ) : null}
    </>
  );
}

const FOOT_LINK =
  "w-fit rounded-xs text-body font-semibold text-primary-ink decoration-1 underline-offset-[0.22em] hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card focus-visible:outline-none";

/** The pairing's sheet; `open` null keeps it closed (it keeps the last pairing on screen while it animates out). */
export function CellDetail({ open, asOf, onClose }: { open: OpenCell | null; asOf: string; onClose: () => void }) {
  const [shown, setShown] = useState(open);
  if (open !== null && open !== shown) setShown(open);
  const rules = useRules();
  const panel = useRef<HTMLDivElement>(null);
  const days = rules.overlap.lookback_days;
  return (
    <Sheet open={open !== null} onOpenChange={(o) => (o ? undefined : onClose())}>
      <SheetContent
        ref={panel}
        tabIndex={-1}
        side="right"
        className="w-full gap-0 overflow-y-auto p-0 sm:max-w-[38rem]"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          panel.current?.focus();
        }}
      >
        {shown ? (
          <>
            <SheetHeader className="gap-3 border-b px-6 pt-6 pb-5">
              <SheetTitle className="pr-8 font-headline text-title text-balance">{`${shown.product.name} vs ${shown.competitor.name}`}</SheetTitle>
              <SheetDescription className="sr-only">{`Their competing products and the evidence-based threat to ${shown.product.name}.`}</SheetDescription>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-meta text-muted-foreground">
                <ThreatChip level={OVERLAP_CHIP[shown.cell.threat_level]} data-overlap={shown.cell.threat_level}>
                  {shown.cell.threat_level === "none" ? cellWord(shown.cell) : `${cellWord(shown.cell)} threat`}
                </ThreatChip>
                <MetaLine
                  parts={[
                    shown.cell.threat_level !== "none" ? `threat weight ${formatCount(shown.cell.threat_weight)}` : null,
                    shown.cell.evidence > 0 ? `evidence ${formatCount(shown.cell.evidence)}` : null,
                    `${plural(days, "day")} of public signals, to ${dayMonth(asOf)}`,
                  ]}
                />
              </div>
            </SheetHeader>
            <div className="flex flex-1 flex-col gap-8 px-6 pt-6 pb-8">
              <Section title="Their products" count={shown.cell.their_products.length || undefined}>
                <TheirProducts open={shown} />
              </Section>
              <Section title={`Threat to ${shown.product.name}`} count={shown.cell.threat_signals.length || undefined}>
                <Evidence open={shown} />
                <HowComputed>
                  <p>{cellFormula(rules)}</p>
                </HowComputed>
              </Section>
            </div>
            <div className="sticky bottom-0 flex flex-wrap gap-x-6 gap-y-2 border-t bg-card px-6 py-4">
              <Link to={`/competitors/${shown.competitor.slug}`} className={FOOT_LINK}>
                {`${shown.competitor.name}'s profile →`}
              </Link>
              <Link to={`/products/${shown.product.slug}`} className={FOOT_LINK}>
                {`${shown.product.name} deep dive →`}
              </Link>
            </div>
          </>
        ) : (
          <>
            <SheetTitle className="sr-only">Pairing</SheetTitle>
            <SheetDescription className="sr-only">A JFrog product against one competitor.</SheetDescription>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
