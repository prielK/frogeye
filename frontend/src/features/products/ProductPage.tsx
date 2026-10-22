import { RotateCwIcon } from "lucide-react";
import { useCallback, useEffect, useId, useMemo } from "react";
import { Link, useParams } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { BackLink } from "@/components/layout/BackLink";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { CitationProvider } from "@/features/analysis/CitedSources";
import { useCapabilities } from "@/features/signals/useSignals";
import { ApiError } from "@/lib/api/client";
import { localToday } from "@/lib/days";
import { nameFromSlug, useCompetitorIndex, useCompetitors } from "@/lib/competitors";
import { RulesContext, useIntelReference, useRules, youngCollection } from "@/lib/intelReference";
import { useNow } from "@/lib/time";
import { dayMonth } from "@/features/intel/words";
import { citedSourcesOf } from "@/features/intel/sources";
import { PressureChip } from "./PressureChip";
import { ActionsSection } from "./sections/ActionsSection";
import { BottomLineSection } from "./sections/BottomLineSection";
import { CompetitorsSection } from "./sections/CompetitorsSection";
import { GapsSection } from "./sections/GapsSection";
import { HiringSection } from "./sections/HiringSection";
import { PressureSection } from "./sections/PressureSection";
import { SourcesSection } from "./sections/SourcesSection";
import { TimelineSection } from "./sections/TimelineSection";
import { emptyBaseline, useProduct, type ProductProfile } from "./useProducts";

/*
 * `/products/:slug`: who is pressing one JFrog product and how hard, where it stands, and what
 * each team should do, with a source for every claim, from one read
 * (`GET /products/{slug}`). One long page in the competitor deep dive's grammar: the header (the
 * product in the serif at `display`, its pressure as mark and words, what it does as the
 * standfirst), then eight sections on ink rules, from the cited bottom line to the sources. It
 * is the same page for every persona: nothing on it reads the reader's role. It covers
 * only the competitive landscape around the product, never a JFrog company figure.
 */

/** The way back to the list. */
function Back() {
  return <BackLink to="/products">All products</BackLink>;
}

function Header({ profile, titleId, today, young }: { profile: ProductProfile; titleId: string; today: string; young: boolean }) {
  const rules = useRules();
  const t = rules.trend;
  // The word compares threat only: the recent weeks against the baseline weeks before,
  // which a young deployment has not collected yet, or which name no threat.
  const since = profile.collected_since;
  const scope =
    profile.pressure === "none"
      ? `as of ${dayMonth(today)}`
      : !profile.pressure_baseline && young && since
        ? `FrogEye has collected since ${dayMonth(since)}, under the ${t.weeks} weeks the trend compares`
        : !profile.pressure_baseline
          ? `Threat in the last ${t.recent_weeks} weeks, ${emptyBaseline(rules)}, to ${dayMonth(today)}`
          : `Threat in the last ${t.recent_weeks} weeks against the ${t.baseline_weeks} before, to ${dayMonth(today)}`;
  return (
    <header className="flex flex-col gap-3 pb-10">
      <h1 id={titleId} className="font-headline text-display text-balance">
        {profile.name}
      </h1>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
        <PressureChip product={profile} collectedSince={since} young={young} />
        <span>{scope}</span>
      </p>
      {profile.description ? <p className="max-w-[65ch] text-body text-pretty text-muted-foreground">{profile.description}</p> : null}
    </header>
  );
}

function ProductView({ profile }: { profile: ProductProfile }) {
  const titleId = useId();
  const today = localToday(useNow(60_000));
  const index = useCompetitorIndex();
  const capabilities = useCapabilities();
  const nameOf = useCallback((slug: string) => index.get(slug)?.name ?? nameFromSlug(slug), [index]);
  const names = useMemo(() => new Map((capabilities.data ?? []).flatMap((c) => c.capabilities.map((k) => [k.slug, k.name] as const))), [capabilities.data]);
  const sources = useMemo(() => citedSourcesOf(profile.brief?.sources ?? []), [profile.brief]);
  // the page reads its collected-since day from one place, the profile.
  const young = youngCollection(profile.collected_since, today, useRules().trend);
  const props = { profile, today, nameOf, young };

  // Every arrival starts at the top of the page.
  useEffect(() => {
    document.scrollingElement?.scrollTo?.({ top: 0 });
  }, []);

  return (
    <CitationProvider sources={sources}>
      <title>{`${profile.name} · FrogEye`}</title>
      <article aria-labelledby={titleId} className="min-w-0 wrap-anywhere">
        <Back />
        <Header profile={profile} titleId={titleId} today={today} young={young} />
        <div className="@container flex min-w-0 flex-col gap-16">
          <BottomLineSection {...props} />
          <CompetitorsSection {...props} />
          <PressureSection {...props} />
          <GapsSection {...props} />
          <ActionsSection {...props} />
          <HiringSection {...props} names={names} />
          <TimelineSection {...props} />
          <SourcesSection {...props} />
        </div>
      </article>
    </CitationProvider>
  );
}

function PageSkeleton() {
  return (
    <div role="status" aria-label="Loading the product" className="min-w-0">
      <Skeleton className="mb-6 h-4 w-28" />
      <div className="flex flex-col gap-3 pb-10">
        <Skeleton className="h-9 w-56 max-w-full" />
        <Skeleton className="h-5 w-64 max-w-full" />
        <Skeleton className="h-4 w-[34rem] max-w-full" />
      </div>
      <div className="flex flex-col gap-16">
        {[0, 1].map((k) => (
          <div key={k} className="flex flex-col gap-4 border-t border-foreground pt-4">
            <Skeleton className="h-6 w-40" />
            <div className="grid gap-x-10 gap-y-3 md:grid-cols-[minmax(0,1fr)_16rem]">
              <div className="flex flex-col gap-3">
                {[100, 94, 97, 70].map((w, i) => (
                  <Skeleton key={i} className="h-4" style={{ width: `${w}%` }} />
                ))}
              </div>
              <Skeleton className="h-40 w-full" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ProductPage() {
  const { slug = "" } = useParams();
  const query = useProduct(slug);
  // The names the page reads (capabilities, competitors), fetched with the profile so they never change words on load.
  const capabilities = useCapabilities();
  const competitors = useCompetitors();
  // The rules its words quote.
  const reference = useIntelReference();

  if (query.error instanceof ApiError && query.error.status === 404) {
    return (
      <div className="min-w-0">
        <title>Product not found · FrogEye</title>
        <Back />
        <EmptyState
          title="This isn't a JFrog product FrogEye tracks"
          hint="FrogEye keeps a deep dive for each of JFrog's products, from Artifactory to the Platform."
          action={
            <Button asChild variant="outline" size="sm">
              <Link to="/products">See every product</Link>
            </Button>
          }
        />
      </div>
    );
  }
  if (query.isPending || capabilities.isPending || competitors.isPending || reference.isPending) return <PageSkeleton />;
  if ((query.isError && !query.data) || !reference.data) {
    return (
      <div className="min-w-0">
        <Back />
        <EmptyState
          tone="error"
          title="The product page didn't load."
          hint="FrogEye's API didn't answer. Check your connection, then try again."
          action={
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (query.isError) void query.refetch();
                if (reference.isError) void reference.refetch();
              }}
              loading={query.isRefetching || reference.isRefetching}
            >
              <RotateCwIcon />
              Try again
            </Button>
          }
        />
      </div>
    );
  }
  return (
    <RulesContext value={reference.data}>
      <ProductView key={slug} profile={query.data} />
    </RulesContext>
  );
}
