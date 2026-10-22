import { CalendarIcon, CircleCheckIcon, CircleDashedIcon } from "lucide-react";
import { useId, type ReactNode } from "react";
import { INLINE_LINK } from "@/components/common/links";
import { ProductLink } from "@/components/common/ProductLink";
import { EvidenceGrade } from "@/components/signal/EvidenceGrade";
import { CREDIBILITY, RELIABILITY, STANCE } from "@/components/signal/meta";
import type { CitedText, ProductImpact, RiskOpportunity, SignalDetail, WatchItem } from "@/components/signal/query";
import { ExternalLink } from "@/components/signal/SourceLink";
import { StanceLabel } from "@/components/signal/StanceLabel";
import { hostOf } from "@/lib/format";
import { plainDay } from "@/lib/time";
import { cn } from "@/lib/utils";
import { Cites } from "./CitedSources";

/*
 * The analysis page's sections. Each starts on a 1px ink rule with its head in the serif at
 * `title`; paragraphs run at `body` in a ~72ch column. The three ledgers
 * (impact on JFrog, risks and opportunities, what to watch) share one grammar: a 10.5rem term
 * column, the cited sentence beside it, hairlines between rows; on phones the term sits on
 * its own line above. Meaning is always a word with its icon, never colour alone.
 */

export function AnalysisSection({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={cn("flex flex-col gap-4 border-t border-foreground pt-4", className)}>
      <h2 id={id} className="font-headline text-title">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** Cited paragraphs, 2 to 4 sentences each, 16px apart. */
export function CitedParagraphs({ items }: { items: CitedText[] }) {
  return (
    <div className="flex flex-col gap-4">
      {items.map((item, i) => (
        <p key={i} className="text-body text-foreground">
          {item.text}
          <Cites cites={item.cites} />
        </p>
      ))}
    </div>
  );
}

const LEVEL: Record<ProductImpact["severity"], string> = { high: "High", medium: "Medium", low: "Low" };

/** A risk wears the threat stance's icon and colour, an opportunity the opportunity's (components/signal/meta STANCE), each with its own word. */
function KindLabel({ kind }: { kind: RiskOpportunity["kind"] }) {
  const look = STANCE[kind === "risk" ? "threat" : "opportunity"]!;
  const Icon = look.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 font-medium whitespace-nowrap", look.className)}>
      <Icon aria-hidden className="size-3.5 shrink-0" />
      {kind === "risk" ? "Risk" : look.label}
    </span>
  );
}

/**
 * "Threat · High severity": the effect as icon + word, the severity as a word (high in ink, the
 * others muted). `stacked` (the ledger's term column) puts them on two lines from `sm`, so a
 * long pair never breaks at the dot.
 */
export function ImpactLabel({ effect, severity, stacked = false }: Pick<ProductImpact, "effect" | "severity"> & { stacked?: boolean }) {
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-x-1.5 text-meta", stacked && "sm:flex-col sm:items-start sm:gap-y-0.5")}>
      {/* An effect is a stance on one product: the same icon, word and colour as everywhere else. */}
      <StanceLabel stance={effect} showNeutral />
      <span aria-hidden className={cn("text-muted-foreground", stacked && "sm:hidden")}>
        ·
      </span>
      <span className={severity === "high" ? "font-medium text-foreground" : "text-muted-foreground"}>{LEVEL[severity]} severity</span>
    </span>
  );
}

const LEDGER_ROW = "grid gap-x-6 gap-y-1 py-3.5 first:pt-1 last:pb-0 sm:grid-cols-[10.5rem_minmax(0,1fr)]";

function ImpactRow({ impact }: { impact: ProductImpact }) {
  const id = useId();
  return (
    <li aria-labelledby={id} className={LEDGER_ROW}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 sm:flex-col sm:items-start">
        {/* The product opens its deep dive; the row is named by it. */}
        <ProductLink name={impact.product} id={id} className={cn(INLINE_LINK, "w-fit text-body font-semibold text-foreground")} />
        <ImpactLabel effect={impact.effect} severity={impact.severity} stacked />
      </div>
      <p className="text-body text-foreground">
        {impact.rationale}
        <Cites cites={impact.cites} />
      </p>
    </li>
  );
}

/** Impact on JFrog: one row per affected product, in the analysis's order, each product linking to its page. 13 products wrap like 3. */
export function ImpactList({ impacts }: { impacts: ProductImpact[] }) {
  return (
    <ul className="flex flex-col divide-y">
      {impacts.map((impact, i) => (
        // a product may repeat in a stored analysis, so the key is its place too
        <ImpactRow key={`${i}-${impact.product}`} impact={impact} />
      ))}
    </ul>
  );
}

/** Risks first, then opportunities, each led by its kind as icon + word. */
export function RiskList({ items }: { items: RiskOpportunity[] }) {
  const ordered = [...items.filter((i) => i.kind === "risk"), ...items.filter((i) => i.kind !== "risk")];
  return (
    <ul className="flex flex-col divide-y">
      {ordered.map((item, i) => (
        <li key={i} className={LEDGER_ROW}>
          <span className="text-meta sm:pt-0.5">
            <KindLabel kind={item.kind} />
          </span>
          <p className="text-body text-foreground">
            {item.text}
            <Cites cites={item.cites} />
          </p>
        </li>
      ))}
    </ul>
  );
}

/** Concrete indicators, dated where a source gives the day; dated ones first, soonest first. */
export function WatchList({ items }: { items: WatchItem[] }) {
  const dated = items.filter((i) => i.date).sort((a, b) => a.date!.localeCompare(b.date!));
  const ordered = [...dated, ...items.filter((i) => !i.date)];
  return (
    <ul className="flex flex-col divide-y">
      {ordered.map((item, i) => (
        <li key={i} className={LEDGER_ROW}>
          <span className="text-meta sm:pt-0.5">
            {item.date ? (
              <time dateTime={item.date} className="inline-flex items-center gap-1 font-medium text-foreground">
                <CalendarIcon aria-hidden className="size-3.5 text-muted-foreground" />
                {plainDay(item.date, "date")}
              </time>
            ) : (
              <span className="text-muted-foreground">No date given</span>
            )}
          </span>
          <p className="text-body text-foreground">
            {item.text}
            <Cites cites={item.cites} />
          </p>
        </li>
      ))}
    </ul>
  );
}

/** What is confirmed and what is reported only, over the grade spelled out in words. */
export function ConfidenceNote({ note, signal }: { note: string; signal: SignalDetail }) {
  const reliability = signal.reliability ? RELIABILITY[signal.reliability] : null;
  const credibility = signal.credibility ? CREDIBILITY[signal.credibility] : null;
  const words = [reliability ? `Sources ${reliability}` : null, credibility ? `information ${credibility}` : null].filter(Boolean).join("; ");
  return (
    <div className="flex flex-col gap-3">
      {note.trim() ? <p className="text-body text-foreground">{note}</p> : null}
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta text-muted-foreground">
        <EvidenceGrade grade={signal.grade} reliability={signal.reliability} credibility={signal.credibility} status={signal.status} />
        {words ? <span>{words}.</span> : null}
      </p>
    </div>
  );
}

/** The quotes a stage-A signal rests on, verified ones marked like a highlighter pen (shown when there is no full analysis). */
export function EvidenceQuotes({ evidence }: { evidence: SignalDetail["evidence"] }) {
  if (evidence.length === 0) return <p className="text-body text-muted-foreground">No quotes were extracted for this signal.</p>;
  return (
    <ul className="flex flex-col gap-4">
      {evidence.map((e, i) => (
        <li key={`${e.document_id}-${i}`} className="flex flex-col gap-1.5">
          <blockquote className="text-body">
            <span
              className={cn(
                "box-decoration-clone",
                e.verified ? "rounded-xs bg-primary-soft px-1 text-foreground" : "text-muted-foreground",
              )}
            >
              “{e.quote}”
            </span>
          </blockquote>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
            {e.verified ? (
              <span className="inline-flex items-center gap-1 font-medium text-primary-ink">
                <CircleCheckIcon aria-hidden className="size-3.5" />
                Verified in the source
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 font-medium">
                <CircleDashedIcon aria-hidden className="size-3.5" />
                Not verified
              </span>
            )}
            <span aria-hidden>·</span>
            <ExternalLink href={e.url} className="font-medium text-foreground">
              {e.source_name}
            </ExternalLink>
            <span className="truncate">{hostOf(e.url)}</span>
          </p>
        </li>
      ))}
    </ul>
  );
}
