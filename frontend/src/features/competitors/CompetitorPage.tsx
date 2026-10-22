import { RotateCwIcon } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { BackLink } from "@/components/layout/BackLink";
import { ExternalLink } from "@/components/signal/SourceLink";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { CitationProvider } from "@/features/analysis/CitedSources";
import { useCapabilities } from "@/features/signals/useSignals";
import { ApiError } from "@/lib/api/client";
import { useCompetitors } from "@/lib/competitors";
import { hostOf } from "@/lib/format";
import { localToday } from "@/lib/days";
import { RulesContext, useIntelReference } from "@/lib/intelReference";
import { useNow } from "@/lib/time";
import { cn } from "@/lib/utils";
import { citedSourcesOf } from "@/features/intel/sources";
import { SectionJump, SectionRail } from "./SectionMenu";
import { CommunitySection } from "./sections/CommunitySection";
import { CustomersSection } from "./sections/CustomersSection";
import { HeadcountSection } from "./sections/HeadcountSection";
import { HiringSection } from "./sections/HiringSection";
import { LatestSignalsSection } from "./sections/LatestSignalsSection";
import { MarketSection } from "./sections/MarketSection";
import { MomentumSection } from "./sections/MomentumSection";
import { MoneySection } from "./sections/MoneySection";
import { OverviewSection } from "./sections/OverviewSection";
import { PeoplePartnersSection } from "./sections/PeoplePartnersSection";
import { PricingSection } from "./sections/PricingSection";
import { ProductsSection } from "./sections/ProductsSection";
import { SourcesSection } from "./sections/SourcesSection";
import { useActiveSection } from "./useActiveSection";
import { RELATIONSHIP } from "./useCompetitorIntel";
import { hasData, SECTION_IDS, SECTIONS, useCompetitorProfile, type CompetitorProfile, type SectionId } from "./useCompetitorProfile";

/*
 * `/competitors/:slug`: everything relevant to JFrog about one competitor, with a source for every
 * number (layout A, the user's pick). One long page read top to bottom: who they are, then
 * thirteen sections under their exact labels, from the cited bottom line to the sources and
 * their freshness. A sticky section menu follows the reader (a rail from `xl`, a select under the
 * top bar below it) and keeps the section in the address. The page is the same for every
 * persona: nothing on it reads the reader's role. A section with nothing to show
 * says why, in the API's coverage note, and never shows a 0 that was not measured.
 */

/** The way back to the list. */
function Back() {
  return <BackLink to="/competitors">All competitors</BackLink>;
}

function Header({ profile, titleId }: { profile: CompetitorProfile; titleId: string }) {
  const c = profile.competitor;
  const facts = [`Tier ${c.tier}`, RELATIONSHIP[c.relationship] ?? c.relationship, c.ticker ? `Listed as ${c.ticker}` : null].filter(Boolean);
  return (
    <header className="flex flex-col gap-3 pb-8">
      <h1 id={titleId} className="font-headline text-display text-balance">
        {c.name}
      </h1>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
        {facts.map((f, i) => (
          <span key={f} className="inline-flex items-center gap-x-2">
            {i > 0 ? <span aria-hidden>·</span> : null}
            {f}
          </span>
        ))}
        {c.website ? (
          <span className="inline-flex items-center gap-x-2">
            <span aria-hidden>·</span>
            <ExternalLink href={c.website} className="font-medium text-foreground">
              {hostOf(c.website)}
            </ExternalLink>
          </span>
        ) : null}
      </p>
      {c.description ? <p className="max-w-[65ch] text-body text-pretty text-muted-foreground">{c.description}</p> : null}
      {/* Its seeded products as chips (its header); their overlap is under Products vs JFrog. */}
      {profile.products.length > 0 ? (
        <ul aria-label="Its products" className="flex flex-wrap gap-1.5 pt-1">
          {profile.products.map((p) => (
            <li key={p.product} title={p.category} className="inline-flex h-6 items-center rounded-sm border border-input bg-card px-2 text-meta font-medium text-foreground">
              {p.product}
            </li>
          ))}
        </ul>
      ) : null}
    </header>
  );
}

function ProfileView({ profile }: { profile: CompetitorProfile }) {
  const titleId = useId();
  const navigate = useNavigate();
  const location = useLocation();
  const today = localToday(useNow(60_000));
  const { active, mark } = useActiveSection(SECTION_IDS);
  const sources = useMemo(() => citedSourcesOf(profile.brief?.sources ?? []), [profile.brief]);
  const present = useMemo(() => Object.fromEntries(SECTIONS.map((s) => [s.id, hasData(profile, s.key)])) as Record<SectionId, boolean>, [profile]);

  const jump = useCallback(
    (id: SectionId) => {
      const target = document.getElementById(id);
      if (!target) return;
      const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      target.scrollIntoView({ block: "start", behavior: still ? "auto" : "smooth" });
      target.querySelector<HTMLElement>("h2")?.focus({ preventScroll: true });
      mark(id);
      navigate({ pathname: location.pathname, search: location.search, hash: id }, { replace: true, preventScrollReset: true });
    },
    [location.pathname, location.search, mark, navigate],
  );

  // A link to a section (`#pricing`) opens the page there; any other arrival starts at the top.
  const arrival = useRef(location.hash.slice(1));
  useEffect(() => {
    const id = arrival.current;
    if ((SECTION_IDS as readonly string[]).includes(id)) document.getElementById(id)?.scrollIntoView({ block: "start" });
    else document.scrollingElement?.scrollTo?.({ top: 0 });
  }, []);

  const name = profile.competitor.name;
  return (
    <CitationProvider sources={sources}>
      <title>{`${name} · FrogEye`}</title>
      <article aria-labelledby={titleId} className="min-w-0 wrap-anywhere">
        <Back />
        <Header profile={profile} titleId={titleId} />
        <SectionJump name={name} active={active} present={present} onJump={jump} />
        <div className="grid gap-x-12 xl:grid-cols-[minmax(0,1fr)_12.5rem]">
          <div className="@container flex min-w-0 flex-col gap-16">
            <OverviewSection profile={profile} today={today} />
            <ProductsSection profile={profile} />
            <MomentumSection profile={profile} today={today} />
            <HiringSection profile={profile} today={today} />
            <HeadcountSection profile={profile} />
            <MoneySection profile={profile} today={today} />
            <MarketSection profile={profile} today={today} />
            <CustomersSection profile={profile} />
            <PricingSection profile={profile} />
            <CommunitySection profile={profile} />
            <PeoplePartnersSection profile={profile} />
            <LatestSignalsSection profile={profile} />
            <SourcesSection profile={profile} />
          </div>
          <aside data-rail className="max-xl:hidden">
            <div className="sticky top-20">
              <SectionRail name={name} active={active} present={present} onJump={jump} />
            </div>
          </aside>
        </div>
      </article>
    </CitationProvider>
  );
}

function PageSkeleton() {
  return (
    <div role="status" aria-label="Loading the profile" className="min-w-0">
      <Skeleton className="mb-6 h-4 w-28" />
      <div className="flex flex-col gap-3 pb-8">
        <Skeleton className="h-9 w-64 max-w-full" />
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-4 w-[34rem] max-w-full" />
      </div>
      <div className="grid gap-x-12 xl:grid-cols-[minmax(0,1fr)_12.5rem]">
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
        <div className="flex flex-col gap-2 max-xl:hidden">
          {SECTIONS.map((s) => (
            <Skeleton key={s.id} className={cn("h-4", s.label.length > 12 ? "w-32" : "w-24")} />
          ))}
        </div>
      </div>
    </div>
  );
}

export function CompetitorPage() {
  const { slug = "" } = useParams();
  const query = useCompetitorProfile(slug);
  // The capability names the hiring tags read, fetched with the profile so tags never change words on load.
  const capabilities = useCapabilities();
  // The rules its formulas, trend words and weekly windows quote.
  const reference = useIntelReference();
  // the competitor names its chips, legend and peers read, so no name changes on load (as the product page).
  const competitors = useCompetitors();

  if (query.error instanceof ApiError && query.error.status === 404) {
    return (
      <div className="min-w-0">
        <title>Competitor not found · FrogEye</title>
        <Back />
        <EmptyState
          title="This competitor isn't tracked"
          hint="FrogEye keeps a profile for every competitor in its seed; JFrog itself has none, since FrogEye covers the market, not JFrog's own news."
          action={
            <Button asChild variant="outline" size="sm">
              <Link to="/competitors">All competitors</Link>
            </Button>
          }
        />
      </div>
    );
  }
  if (query.isPending || capabilities.isPending || reference.isPending || competitors.isPending) return <PageSkeleton />;
  if ((query.isError && !query.data) || !reference.data) {
    return (
      <div className="min-w-0">
        <Back />
        <EmptyState
          tone="error"
          title="The profile didn't load."
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
      <ProfileView key={slug} profile={query.data} />
    </RulesContext>
  );
}
