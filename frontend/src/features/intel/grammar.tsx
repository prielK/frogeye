import { ChevronRightIcon } from "lucide-react";
import { useId, type ReactNode } from "react";
import { EmptyState } from "@/components/common/EmptyState";
import { SourceLink } from "@/components/signal/SourceLink";
import { SourceItem } from "@/features/analysis/CitedSources";
import { plainDay } from "@/lib/time";
import { cn } from "@/lib/utils";
import { citedSourcesOf, INTEL_KIND, type IntelSource } from "./sources";
import { keepDates } from "./words";

/*
 * The reading grammar both deep dives share, so the competitor's thirteen
 * sections and the product's eight read as one product:
 * - a section starts on a 1px ink rule with its head in the serif at `title` (the analysis page's
 *   long-read grammar), and its source and as-of date on the right;
 * - a block inside it is a `body` semibold head on a hairline;
 * - a section with nothing of its own says why in ONE quiet line under its rule (`QuietNote`,
 *   the API's note in `foreground` medium, how it fills muted), never a dashed box; the
 *   dashed panel (`NoteState`) is kept for a block that is empty inside a section that has data,
 *   as the kit's empty chart frames are;
 * - "How this is computed" is a quiet disclosure.
 */

type ProfileSectionProps = {
  /** Its anchor and head: one of the deep dive's sections, or a product deep dive's (the same grammar). */
  section: { id: string; label: string };
  /** Where the section's figures come from and how fresh they are. */
  meta?: ReactNode;
  children: ReactNode;
};

export function ProfileSection({ section, meta, children }: ProfileSectionProps) {
  const headId = useId();
  return (
    <section id={section.id} aria-labelledby={headId} className="flex min-w-0 scroll-mt-32 flex-col gap-6 border-t border-foreground pt-4 xl:scroll-mt-20">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        {/* The jump target: focused after a menu jump, so Tab continues inside the section. */}
        <h2 id={headId} tabIndex={-1} className="font-headline text-title text-balance outline-none">
          {section.label}
        </h2>
        {meta ? <p className="text-meta text-muted-foreground">{meta}</p> : null}
      </div>
      {children}
    </section>
  );
}

/** A block's head inside a section: `body` semibold on a hairline, quiet meta beside it. */
export function SubHead({ id, title, meta, first = false, className }: { id?: string; title: ReactNode; meta?: ReactNode; first?: boolean; className?: string }) {
  return (
    // The first block under a section head sits on the section's ink rule already: no second rule.
    <div className={cn("flex flex-wrap items-baseline gap-x-3 gap-y-0.5", first ? "pt-0" : "border-t pt-3", className)}>
      <h3 id={id} className="text-body font-semibold text-foreground">
        {title}
      </h3>
      {meta ? <span className="text-meta text-muted-foreground">{meta}</span> : null}
    </div>
  );
}

const capitalise = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/**
 * A note as a title and a hint: "Not enough evidence for a brief yet: 0 public signals…" reads as
 * the title "Not enough evidence for a brief yet" over the hint "0 public signals… ." A note
 * without a colon is all title.
 */
function splitNote(text: string): { title: string; hint?: string } {
  const at = text.indexOf(": ");
  if (at === -1) return { title: text };
  const rest = text.slice(at + 2).trim();
  return { title: text.slice(0, at), hint: /[.!?]$/.test(rest) ? capitalise(rest) : `${capitalise(rest)}.` };
}

const sentence = (text: string) => (/[.!?]$/.test(text) ? text : `${text}.`);

/**
 * A section with nothing of its own: one quiet line under its ink rule, the API's note in
 * `foreground` medium (what happened), then how it fills, muted. Several notes are several lines.
 */
export function QuietNote({ note, hint, className }: { note: string; hint?: ReactNode; className?: string }) {
  const { title, hint: rest } = splitNote(note);
  return (
    <p data-quiet-note className={cn("max-w-[72ch] text-body text-pretty text-muted-foreground", className)}>
      <span className="font-medium text-foreground">{keepDates(sentence(title))}</span>
      {rest ? <> {keepDates(rest)}</> : null}
      {hint ? <> {hint}</> : null}
    </p>
  );
}

/** A block with nothing to show inside a section that has data: the API's note as the title (what happened), and how it fills. */
export function NoteState({ note, hint, className }: { note: string; hint?: ReactNode; className?: string }) {
  const { title, hint: rest } = splitNote(note);
  const lines = [rest, hint].filter(Boolean);
  return (
    <EmptyState
      className={cn("max-w-none", className)}
      title={title}
      hint={
        lines.length ? (
          <>
            {lines.map((line, i) => (
              <span key={i} className="block">
                {line}
              </span>
            ))}
          </>
        ) : undefined
      }
    />
  );
}

/** "How this is computed": the API's explanation and the documented formula, one click away. */
export function HowComputed({ children }: { children: ReactNode }) {
  return (
    <details className="group/how max-w-[72ch]">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-sm text-meta font-medium text-muted-foreground transition-colors duration-150 ease-out-quart select-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon aria-hidden className="size-3.5 transition-transform duration-150 ease-out-quart group-open/how:rotate-90" />
        How this is computed
      </summary>
      <div className="mt-2 flex flex-col gap-2 border-l border-border pl-4 text-meta text-pretty text-muted-foreground">{children}</div>
    </details>
  );
}

/** A quiet line of meta, its parts apart by a centred dot. */
export function MetaLine({ parts, className }: { parts: ReactNode[]; className?: string }) {
  const shown = parts.filter((p) => p !== null && p !== undefined && p !== false && p !== "");
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-x-1.5", className)}>
      {shown.map((part, i) => (
        <span key={i} className="inline-flex items-center gap-x-1.5">
          {i > 0 ? <span aria-hidden>·</span> : null}
          {part}
        </span>
      ))}
    </span>
  );
}

/**
 * A brief's numbered sources, each the target its `[n]` markers jump to (`SourceItem`): a signal
 * opens its analysis (one hidden from readers is plain text), every other kind links out, with its
 * kind in words, outlet and day, and a fact's verified quote or a page change's summary. The
 * competitor and product deep dives both list their brief's sources with it.
 */
export function CitedSourceList({ sources }: { sources: IntelSource[] }) {
  const cited = citedSourcesOf(sources);
  return (
    <ol aria-label="Cited in the brief" className="flex flex-col divide-y">
      {sources.map((s, i) => (
        <SourceItem key={s.n} n={s.n}>
          <SourceLink source={cited[i]!} className="w-fit text-body font-medium text-foreground" />
          <p className="text-meta text-muted-foreground">
            <MetaLine parts={[INTEL_KIND[s.kind], s.outlet, s.day ? plainDay(s.day, "date") : null]} />
          </p>
          {s.excerpt ? <p className="max-w-[72ch] text-meta text-pretty text-muted-foreground">“{s.excerpt}”</p> : null}
        </SourceItem>
      ))}
    </ol>
  );
}
