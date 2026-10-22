import { ArrowLeftIcon, RotateCwIcon } from "lucide-react";
import { useEffect, useId, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { BACK_LINK, BackLink } from "@/components/layout/BackLink";
import { Num } from "@/components/common/Num";
import { INLINE_LINK } from "@/components/common/links";
import { ProductLink } from "@/components/common/ProductLink";
import { RelativeTime } from "@/components/common/RelativeTime";
import { analysisActionSets, stageAActionSets } from "@/components/signal/actions";
import { AnalysisStateNote } from "@/components/signal/AnalysisStateNote";
import { Classification } from "@/components/signal/Classification";
import { CompanyChips } from "@/components/signal/CompanyChips";
import { EvidenceGrade } from "@/components/signal/EvidenceGrade";
import { analysisHref } from "@/components/signal/param";
import { readableAnalysis, sourcesOf, useSignal, type SignalAnalysis, type SignalDetail } from "@/components/signal/query";
import { ScoreBreakdown } from "@/components/signal/ScoreBreakdown";
import { StanceLabel } from "@/components/signal/StanceLabel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { plural } from "@/lib/format";
import { useHasInAppHistory } from "@/lib/history";
import { editionFor, type Edition } from "@/lib/roles";
import { cn } from "@/lib/utils";
import {
  AnalysisSection,
  CitedParagraphs,
  ConfidenceNote,
  EvidenceQuotes,
  ImpactLabel,
  ImpactList,
  RiskList,
  WatchList,
} from "./AnalysisSections";
import { CitationProvider, Cites, SourcesList } from "./CitedSources";
import { RoleActions } from "./RoleActions";

/*
 * `/signals/:id`: one story's full analysis, read like an editorial long-read. The header
 * spans the article (serif `display` title, the trust line, companies); beneath it a ~72ch
 * analysis column (the summary as its standfirst, then each section on an ink rule, ending
 * in the numbered Sources) and, from `xl`, an 18rem side column of quieter facts (products,
 * tags, related signals, why it ranks). On narrower screens the side column follows the
 * Sources. A stale analysis reads in full under a quiet "being refreshed" note; a thin
 * or not-yet-analysed signal reads from its stage-A fact and impact under a marker, never
 * blank.
 */


/**
 * Back to wherever the reader came from inside the app. A page that is the tab's first app
 * entry (opened from outside, or a signed-out deep link that went through sign-in) has nothing
 * in the app to go back to, so it offers the Explorer instead, and says so.
 */
function Back() {
  const navigate = useNavigate();
  if (useHasInAppHistory()) {
    return (
      <button type="button" onClick={() => navigate(-1)} className={BACK_LINK}>
        <ArrowLeftIcon aria-hidden className="size-3.5" />
        Back
      </button>
    );
  }
  return <BackLink to="/signals">All signals</BackLink>;
}

function Header({ signal, titleId, articles }: { signal: SignalDetail; titleId: string; articles: number }) {
  return (
    <header className="flex flex-col gap-4 pb-10">
      {/* The title takes the analysis column's measure, so its right edge lines up with the reading column. */}
      <h1 id={titleId} className="max-w-[38rem] font-headline text-display">
        {signal.title}
      </h1>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-meta text-muted-foreground">
        <Classification signal={signal} />
        <span className="inline-flex flex-wrap items-center gap-x-4 gap-y-2">
          <EvidenceGrade grade={signal.grade} reliability={signal.reliability} credibility={signal.credibility} status={signal.status} />
          <span>
            Importance <Num value={signal.importance} className="font-semibold text-foreground" />
          </span>
          <span>
            First seen <RelativeTime iso={signal.first_seen_at} />
          </span>
          {/* The story's own articles; the JFrog pages and signals it cites are listed under Sources */}
          <span>{plural(articles, "article")}</span>
        </span>
      </div>
      <CompanyChips slugs={signal.competitors} max={8} />
    </header>
  );
}

/**
 * The full stage-B analysis, in the spec's order; an empty section is left out, Confidence and
 * Sources always close it. A stale one (new coverage since) reads in full under a quiet note.
 */
function FullAnalysis({ signal, analysis, edition }: { signal: SignalDetail; analysis: SignalAnalysis; edition: Edition }) {
  const sets = analysisActionSets(analysis.recommended_actions);
  return (
    <>
      <div className="flex flex-col gap-3">
        <AnalysisStateNote state={signal.analysis_state} />
        <p className="text-body text-foreground">
          {analysis.summary.text}
          <Cites cites={analysis.summary.cites} />
        </p>
      </div>
      {analysis.what_happened.length > 0 ? (
        <AnalysisSection title="What happened">
          <CitedParagraphs items={analysis.what_happened} />
        </AnalysisSection>
      ) : null}
      {analysis.background.length > 0 ? (
        <AnalysisSection title="Background">
          <CitedParagraphs items={analysis.background} />
        </AnalysisSection>
      ) : null}
      {analysis.what_changed.length > 0 ? (
        <AnalysisSection title="What changed">
          <CitedParagraphs items={analysis.what_changed} />
        </AnalysisSection>
      ) : null}
      {analysis.jfrog_impact.length > 0 ? (
        <AnalysisSection title="Impact on JFrog">
          <ImpactList impacts={analysis.jfrog_impact} />
        </AnalysisSection>
      ) : null}
      {analysis.competitive_implications.length > 0 ? (
        <AnalysisSection title="Competitive implications">
          <CitedParagraphs items={analysis.competitive_implications} />
        </AnalysisSection>
      ) : null}
      {sets.length > 0 ? (
        <AnalysisSection title="Recommended actions">
          <RoleActions sets={sets} edition={edition} />
        </AnalysisSection>
      ) : null}
      {analysis.risks_opportunities.length > 0 ? (
        <AnalysisSection title="Risks and opportunities">
          <RiskList items={analysis.risks_opportunities} />
        </AnalysisSection>
      ) : null}
      {analysis.what_to_watch.length > 0 ? (
        <AnalysisSection title="What to watch">
          <WatchList items={analysis.what_to_watch} />
        </AnalysisSection>
      ) : null}
      <AnalysisSection title="Confidence">
        <ConfidenceNote note={analysis.confidence_note} signal={signal} />
      </AnalysisSection>
      <AnalysisSection title="Sources">
        <SourcesList sources={sourcesOf(signal)} />
      </AnalysisSection>
    </>
  );
}

/** No analysis to read (thin, or not written yet): the stage-A first read under its marker. */
function FirstRead({ signal, edition }: { signal: SignalDetail; edition: Edition }) {
  const sets = stageAActionSets(signal.role_actions);
  return (
    <>
      <div className="flex flex-col gap-5">
        <AnalysisStateNote state={signal.analysis_state} />
        {signal.fact?.trim() ? (
          <p className="text-body text-foreground">{signal.fact}</p>
        ) : (
          <p className="text-body text-muted-foreground">The analysis for this signal hasn't been written yet.</p>
        )}
      </div>
      {signal.impact?.trim() ? (
        <AnalysisSection title="Impact on JFrog">
          <p className="text-body text-foreground">{signal.impact}</p>
        </AnalysisSection>
      ) : null}
      {sets.length > 0 ? (
        <AnalysisSection title="Recommended actions">
          <RoleActions sets={sets} edition={edition} />
        </AnalysisSection>
      ) : null}
      <AnalysisSection title="Evidence">
        <EvidenceQuotes evidence={signal.evidence} />
      </AnalysisSection>
      <AnalysisSection title="Sources">
        <SourcesList sources={sourcesOf(signal)} />
      </AnalysisSection>
    </>
  );
}

/** A side-column block: a quiet `body` semibold head on a hairline, so the column never competes with the analysis. */
function SideSection({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3 border-t pt-3">
      <h2 id={id} className="text-body font-semibold text-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

const SEVERITY_RANK = { high: 0, medium: 1, low: 2 } as const;

function SideColumn({ signal, analysis }: { signal: SignalDetail; analysis: SignalAnalysis | null }) {
  const products = [...(analysis?.jfrog_impact ?? [])].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
  return (
    <aside aria-label="About this signal" className="flex min-w-0 flex-col gap-8">
      {products.length > 0 ? (
        <SideSection title="JFrog products">
          <ul className="flex flex-col gap-2">
            {products.map((p, i) => (
              // a product may repeat in a stored analysis, so the key is its place too
              <li key={`${i}-${p.product}`} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                <ProductLink name={p.product} className={cn(INLINE_LINK, "text-body text-foreground")} />
                <ImpactLabel effect={p.effect} severity={p.severity} />
              </li>
            ))}
          </ul>
        </SideSection>
      ) : null}
      {signal.capabilities.length > 0 ? (
        <SideSection title="Capabilities">
          <ul className="flex flex-wrap gap-1.5">
            {signal.capabilities.map((c) => (
              <li key={c.slug}>
                <Badge variant="outline" className="whitespace-normal">
                  {c.name}
                </Badge>
              </li>
            ))}
          </ul>
        </SideSection>
      ) : null}
      {signal.kits.length > 0 ? (
        <SideSection title="Key intelligence topics">
          <ul className="flex flex-col gap-1.5">
            {signal.kits.map((k) => (
              <li key={k.slug} className="text-body text-foreground">
                {k.title}
              </li>
            ))}
          </ul>
        </SideSection>
      ) : null}
      {signal.related.length > 0 ? (
        <SideSection title="Related signals">
          <ul className="flex flex-col divide-y">
            {signal.related.map((r) => (
              <li key={r.id} className="flex flex-col gap-1.5 py-3 first:pt-0.5">
                <Link
                  to={analysisHref(r.id)}
                  className={cn(INLINE_LINK, "w-fit text-body font-medium text-foreground")}
                >
                  {r.title}
                </Link>
                <span className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta text-muted-foreground">
                  <StanceLabel stance={r.stance} />
                  <EvidenceGrade grade={r.grade} reliability={r.reliability} credibility={r.credibility} status={r.status} />
                  <CompanyChips slugs={r.competitors} max={2} />
                  <RelativeTime iso={r.first_seen_at} />
                </span>
              </li>
            ))}
          </ul>
        </SideSection>
      ) : null}
      <SideSection title="Why ranked here">
        <ScoreBreakdown breakdown={signal.score_breakdown} />
      </SideSection>
    </aside>
  );
}

const ARTICLE = "max-w-[59.5rem] wrap-anywhere";
const COLUMNS = "grid gap-x-14 gap-y-14 xl:grid-cols-[minmax(0,38rem)_18rem]";

function AnalysisView({ signal, edition }: { signal: SignalDetail; edition: Edition }) {
  const titleId = useId();
  const analysis = readableAnalysis(signal);
  const sources = sourcesOf(signal);
  const articles = sources.filter((s) => s.kind === "document").length;
  return (
    <CitationProvider sources={sources}>
      <title>{`${signal.title} · FrogEye`}</title>
      <article aria-labelledby={titleId} className={ARTICLE}>
        <Back />
        <Header signal={signal} titleId={titleId} articles={articles} />
        <div className={COLUMNS}>
          <div className="flex min-w-0 flex-col gap-11">
            {analysis ? (
              <FullAnalysis signal={signal} analysis={analysis} edition={edition} />
            ) : (
              <FirstRead signal={signal} edition={edition} />
            )}
          </div>
          <SideColumn signal={signal} analysis={analysis} />
        </div>
      </article>
    </CitationProvider>
  );
}

function PageSkeleton() {
  return (
    <div role="status" aria-label="Loading the analysis" className={ARTICLE}>
      <Skeleton className="mb-6 h-4 w-24" />
      <div className="flex flex-col gap-3.5 pb-10">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-9 w-[28rem] max-w-full" />
        <Skeleton className="h-9 w-[20rem] max-w-full" />
        <Skeleton className="mt-1 h-5 w-80 max-w-full" />
      </div>
      <div className={COLUMNS}>
        <div className="flex flex-col gap-4">
          {[100, 96, 88, 100, 70].map((w, i) => (
            <Skeleton key={i} className="h-4" style={{ width: `${w}%` }} />
          ))}
          <Skeleton className="mt-8 h-6 w-40" />
          {[100, 92, 97, 60].map((w, i) => (
            <Skeleton key={i} className="h-4" style={{ width: `${w}%` }} />
          ))}
        </div>
        <div className="flex flex-col gap-3">
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    </div>
  );
}

export function SignalAnalysisPage() {
  const { id } = useParams();
  const signalId = id && /^\d{1,9}$/.test(id) ? Number(id) : null;
  const query = useSignal(signalId);
  const { user } = useAuth();
  // Every reader reads in their own edition: their role's actions open first.
  const edition: Edition = editionFor(user?.role);

  // A new story starts at its top, not at the scroll position of the list it was opened from.
  useEffect(() => {
    document.scrollingElement?.scrollTo?.({ top: 0 });
  }, [signalId]);

  if (signalId === null || (query.error instanceof ApiError && query.error.status === 404)) {
    return (
      <div className={ARTICLE}>
        <title>Signal not available · FrogEye</title>
        <Back />
        <EmptyState
          title="This signal isn't available."
          hint="It may have been withdrawn after an analyst's review, or the link is out of date."
          action={
            <Button asChild variant="outline" size="sm">
              <Link to="/signals">See every signal</Link>
            </Button>
          }
        />
      </div>
    );
  }
  if (query.isPending) return <PageSkeleton />;
  // a failed background refresh (a run finishing mid-read invalidates every detail)
  // keeps the page on screen; only a first load that failed shows the error.
  if (query.isError && !query.data) {
    return (
      <div className={ARTICLE}>
        <Back />
        <EmptyState
          tone="error"
          title="The analysis didn't load."
          hint="FrogEye's API didn't answer. Check your connection, then try again."
          action={
            <Button variant="outline" size="sm" onClick={() => query.refetch()} loading={query.isRefetching}>
              <RotateCwIcon />
              Try again
            </Button>
          }
        />
      </div>
    );
  }
  return <AnalysisView key={query.data.id} signal={query.data} edition={edition} />;
}
