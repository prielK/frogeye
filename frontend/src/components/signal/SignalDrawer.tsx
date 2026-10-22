import { RotateCwIcon } from "lucide-react";
import { useId, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { Num } from "@/components/common/Num";
import { RelativeTime } from "@/components/common/RelativeTime";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { plural } from "@/lib/format";
import { ROLES, type Edition } from "@/lib/roles";
import { analysisActionSets, quickActions, stageAActionSets } from "./actions";
import { AnalysisStateNote } from "./AnalysisStateNote";
import { Classification } from "./Classification";
import { CompanyChips } from "./CompanyChips";
import { EvidenceGrade } from "./EvidenceGrade";
import { analysisHref } from "./param";
import { readableAnalysis, sourcesOf, useSignal, type SignalCard, type SignalDetail } from "./query";
import { SourceLink, SourceMeta } from "./SourceLink";

/** How many sources the quick look lists before pointing to the page. */
const QUICK_SOURCES = 3;

/** A quick-look section: its head in the serif at `title` on an ink rule, as on the analysis page. */
function DrawerSection({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
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

/** The analysis summary (a stale one under its quiet note); otherwise the stage-A fact and impact under their marker. */
function Gist({ signal }: { signal: SignalDetail }) {
  const analysis = readableAnalysis(signal);
  if (analysis?.summary.text.trim()) {
    return (
      <div className="flex flex-col gap-2.5">
        <AnalysisStateNote state={signal.analysis_state} />
        <p className="text-body text-foreground">{analysis.summary.text}</p>
      </div>
    );
  }
  const fact = signal.fact?.trim();
  const impact = signal.impact?.trim();
  return (
    <div className="flex flex-col gap-3">
      <AnalysisStateNote state={signal.analysis_state} compact />
      {fact ? <p className="text-body text-foreground">{fact}</p> : null}
      {impact ? <p className="text-body text-muted-foreground">{impact}</p> : null}
      {!fact && !impact ? <p className="text-body text-muted-foreground">The analysis for this signal hasn't been written yet.</p> : null}
    </div>
  );
}

/** Up to three of this edition's actions; the master shows each role's first, labelled with the role. */
function WhatToDo({ signal, edition }: { signal: SignalDetail; edition: Edition }) {
  const analysis = readableAnalysis(signal);
  const sets = analysis ? analysisActionSets(analysis.recommended_actions) : stageAActionSets(signal.role_actions);
  const actions = quickActions(sets, edition);
  if (actions.length === 0) return null;
  return (
    <DrawerSection title="What to do">
      <ul className="flex flex-col gap-2 pl-[1.125rem] list-disc marker:text-muted-foreground">
        {actions.map((a, i) => (
          <li key={i} className="text-body text-foreground">
            {edition === "master" ? <span className="font-semibold">{ROLES[a.role].label}: </span> : null}
            {a.text}
          </li>
        ))}
      </ul>
    </DrawerSection>
  );
}

function Sources({ signal }: { signal: SignalDetail }) {
  const sources = sourcesOf(signal);
  const more = sources.length - QUICK_SOURCES;
  return (
    <DrawerSection title="Sources" count={sources.length}>
      {sources.length === 0 ? (
        <p className="text-body text-muted-foreground">No source documents are linked.</p>
      ) : (
        <ol className="flex flex-col divide-y">
          {sources.slice(0, QUICK_SOURCES).map((s) => (
            <li key={s.n} className="flex flex-col gap-0.5 py-2.5 first:pt-0.5">
              <SourceLink source={s} className="w-fit text-body font-medium text-foreground" />
              <SourceMeta source={s} />
            </li>
          ))}
        </ol>
      )}
      {more > 0 ? <p className="text-meta text-muted-foreground">{plural(more, "more source")} in the full analysis.</p> : null}
    </DrawerSection>
  );
}

function Header({ card }: { card: SignalCard }) {
  return (
    <>
      {/* The title leads; its classification follows it (a headline never wears a label above it). */}
      <SheetTitle className="pr-8 font-headline text-title text-balance">{card.title}</SheetTitle>
      <SheetDescription className="sr-only">A quick look at the signal: its summary, what to do and its sources.</SheetDescription>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-meta text-muted-foreground">
        <Classification signal={card} />
        <EvidenceGrade grade={card.grade} reliability={card.reliability} credibility={card.credibility} status={card.status} />
        <span>
          Importance <Num value={card.importance} className="font-semibold text-foreground" />
        </span>
        <span>
          First seen <RelativeTime iso={card.first_seen_at} />
        </span>
      </div>
      <CompanyChips slugs={card.competitors} max={6} />
    </>
  );
}

/**
 * "Open full analysis →". From a drawer that opening pushed
 * onto the history, the page takes that entry's place, so Back from the page returns to where
 * the drawer was opened, not to the drawer.
 */
function FullAnalysisLink({ id }: { id: number }) {
  const location = useLocation();
  const pushed = Boolean((location.state as { drawer?: boolean } | null)?.drawer);
  return (
    <Link
      to={analysisHref(id)}
      replace={pushed}
      className="w-fit rounded-xs text-body font-semibold text-primary-ink decoration-1 underline-offset-[0.22em] hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card focus-visible:outline-none"
    >
      Open full analysis →
    </Link>
  );
}

type SignalDrawerProps = {
  /** The signal to show; null keeps the drawer closed. */
  signalId: number | null;
  /** Whose actions to show (master shows each role's first). */
  edition: Edition;
  onClose: () => void;
};

/**
 * One signal at a glance, one step behind whatever page opened it (PRODUCT.md, so-what first):
 * the summary (or the stage-A fact when there is no analysis yet), up to three of the
 * reader's actions, the first sources, and "Open full analysis →" to `/signals/:id`, where
 * everything else lives. Controlled: the app renders it once, through SignalDrawerHost.
 */
export function SignalDrawer({ signalId, edition, onClose }: SignalDrawerProps) {
  // While the sheet animates closed (signalId null) it keeps showing the last signal, never
  // a flash of skeletons.
  const [shown, setShown] = useState(signalId);
  if (signalId !== null && signalId !== shown) setShown(signalId);
  const query = useSignal(shown);
  const detail = query.data && query.data.id === shown ? query.data : null;
  const missing = query.error instanceof ApiError && query.error.status === 404;
  const panel = useRef<HTMLDivElement>(null);

  return (
    <Sheet open={signalId !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <SheetContent
        ref={panel}
        tabIndex={-1}
        side="right"
        className="w-full gap-0 overflow-y-auto p-0 sm:max-w-[38rem]"
        // Focus the panel, not the close button: reading starts at the top, and Tab moves on from there.
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          panel.current?.focus();
        }}
      >
        <SheetHeader className="gap-3 border-b px-6 pt-6 pb-5">
          {detail ? (
            <Header card={detail} />
          ) : (
            <>
              <SheetTitle className="sr-only">Signal</SheetTitle>
              <SheetDescription className="sr-only">A quick look at the signal: its summary, what to do and its sources.</SheetDescription>
              <Skeleton className="h-3 w-40" />
              <Skeleton className="h-6 w-4/5" />
            </>
          )}
        </SheetHeader>

        {detail ? (
          <>
            <div className="flex flex-1 flex-col gap-8 px-6 pt-6 pb-8">
              <Gist signal={detail} />
              <WhatToDo signal={detail} edition={edition} />
              <Sources signal={detail} />
            </div>
            {/* The way into everything else stays in reach while the quick look scrolls. */}
            <div className="sticky bottom-0 border-t bg-card px-6 py-4">
              <FullAnalysisLink id={detail.id} />
            </div>
          </>
        ) : missing ? (
          <div className="flex flex-col gap-1.5 px-6 py-6">
            <p className="text-body font-medium">This signal isn't available.</p>
            <p className="text-body text-muted-foreground">It may have been withdrawn after an analyst's review, or the link is out of date.</p>
          </div>
        ) : query.isError ? (
          <div role="alert" className="flex flex-col items-start gap-3 px-6 py-6">
            <p className="text-body text-threat">The signal's details didn't load. Check your connection, then try again.</p>
            <Button variant="outline" size="sm" onClick={() => query.refetch()} loading={query.isRefetching}>
              <RotateCwIcon />
              Try again
            </Button>
          </div>
        ) : (
          <div role="status" aria-label="Loading signal" className="flex flex-col gap-4 px-6 py-6">
            {[90, 100, 70, 95, 60].map((w, i) => (
              <Skeleton key={i} className="h-4" style={{ width: `${w}%` }} />
            ))}
            <Skeleton className="mt-4 h-24 w-full" />
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
