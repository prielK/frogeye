import { ChevronLeftIcon, ChevronRightIcon, InfoIcon } from "lucide-react";
import { useId, useMemo, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { analysisHref } from "@/components/signal/param";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { useCompetitorIndex } from "@/lib/competitors";
import { EDITIONS, editionFor, editionLabel, type Edition } from "@/lib/roles";
import { dayKey, formatClock, plainDay, useNow } from "@/lib/time";
import { cn } from "@/lib/utils";
import { BriefingFooter, BriefingSection } from "./BriefingSection";
import {
  BriefingLoadError,
  BriefingSkeleton,
  Failed,
  LastFullBriefing,
  LlmOffNote,
  NoBriefing,
  NoBriefingForDate,
  NotABriefingDate,
  Skipped,
} from "./BriefingStates";
import { FunnelStrip } from "./FunnelStrip";
import { TodayAtAGlance } from "./Glance";
import { TakeawayCard } from "./TakeawayCard";
import {
  briefingHref,
  FOCUS_RING,
  indexSignals,
  isBriefingDate,
  neighbours,
  useBriefing,
  useBriefingList,
  useLlmDisabled,
  type BriefingOut,
  type BriefingSummary,
} from "./useBriefing";

/* ---- masthead ---- */

function DateNav({ date, list, isLatest }: { date: string; list: BriefingSummary[] | undefined; isLatest: boolean }) {
  const { older, newer } = neighbours(list, date);
  const step = (target: BriefingSummary | null, direction: "older" | "newer") => {
    const Icon = direction === "older" ? ChevronLeftIcon : ChevronRightIcon;
    const word = direction === "older" ? "Earlier" : "Later";
    if (!target) {
      return (
        <Button variant="ghost" size="sm" disabled className="text-muted-foreground">
          {direction === "older" ? <Icon /> : null}
          {word}
          {direction === "newer" ? <Icon /> : null}
        </Button>
      );
    }
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button asChild variant="ghost" size="sm" className="text-muted-foreground hover:text-foreground">
            <Link
              to={briefingHref(direction === "newer" && list?.[0]?.date === target.date ? null : target.date)}
              aria-label={`${direction === "older" ? "Previous" : "Next"} briefing, ${plainDay(target.date, "dateline")}`}
            >
              {direction === "older" ? <Icon /> : null}
              {plainDay(target.date, "short")}
              {direction === "newer" ? <Icon /> : null}
            </Link>
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-72">{target.headline}</TooltipContent>
      </Tooltip>
    );
  };
  return (
    <nav aria-label="Briefing dates" className="flex items-center gap-0.5">
      {step(older, "older")}
      {step(newer, "newer")}
      {!isLatest ? (
        <Button asChild variant="outline" size="sm" className="ml-1.5">
          <Link to={briefingHref(null)}>Latest</Link>
        </Button>
      ) : null}
    </nav>
  );
}

/** What the Briefing is, in one quiet line under its name (exact copy). */
const EXPLANATION = "Today's most important competitor and market moves, written for your role.";

/**
 * The nameplate: the publication's name beside the reader's edition (the only one they read)
 * and what that edition leads with, what the Briefing is, and the day, with the date navigation
 * beside them.
 */
function Nameplate({ date, edition, publishedAt, children }: { date?: string; edition: Edition; publishedAt?: string | null; children?: ReactNode }) {
  const focus = EDITIONS.find((e) => e.value === edition)?.focus;
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 pb-4">
      <title>{date ? `Morning Briefing · ${plainDay(date, "short")} · FrogEye` : "Morning Briefing · FrogEye"}</title>
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="font-headline text-title leading-none tracking-[-0.01em]">Morning Briefing</h1>
          <p className="text-body">
            <span className="font-semibold text-foreground">{`${editionLabel(edition)} edition`}</span>
            {focus ? <span className="text-muted-foreground">{` · ${focus}`}</span> : null}
          </p>
        </div>
        <p className="text-body text-muted-foreground">{EXPLANATION}</p>
        {date ? (
          <p className="text-meta text-muted-foreground">
            <time dateTime={date}>{plainDay(date, "dateline")}</time>
            {publishedAt ? ` · published ${formatClock(Date.parse(publishedAt))}` : null}
          </p>
        ) : null}
      </div>
      {children}
    </header>
  );
}

/** The day's headline, its lead, and how much reading it saved. */
function Lede({ briefing }: { briefing: BriefingOut }) {
  const { headline, lead } = briefing.content;
  const long = headline.length > 90;
  return (
    <div className="flex flex-col gap-4 pt-7 pb-9">
      {/* One display size at every width; a long headline gets a wider measure, not a smaller size. */}
      <h2 className={cn("font-headline text-display text-balance", long ? "max-w-[40ch]" : "max-w-[30ch]")}>{headline}</h2>
      {lead.trim() ? <p className="max-w-[62ch] text-body text-foreground">{lead}</p> : null}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 pt-1">
        <FunnelStrip
          funnel={briefing.funnel}
          inBriefing={briefing.signals.length}
          takeaways={Math.min(3, briefing.content.top_takeaways.length)}
        />
        {briefing.agent_run_id !== null ? (
          <Link
            to={`/admin/agents/${briefing.agent_run_id}`}
            className={cn("rounded-sm text-meta font-medium text-primary-ink underline-offset-4 hover:underline", FOCUS_RING)}
          >
            View agent run
          </Link>
        ) : null}
      </div>
    </div>
  );
}

/* ---- the edition's body ---- */

/** Every story opens its full analysis page, in the reader's own edition. */
const hrefFor = (id: number) => analysisHref(id);

function TopTakeaways({ briefing }: { briefing: BriefingOut }) {
  const id = useId();
  const index = useCompetitorIndex();
  const signals = useMemo(() => indexSignals(briefing.signals), [briefing.signals]);
  const isSelf = (slug: string) => Boolean(index.get(slug)?.is_self);
  const [lead, ...rest] = briefing.content.top_takeaways.slice(0, 3);
  if (!lead) return null;
  const pair = rest.length > 1;
  return (
    <section aria-labelledby={id} className="border-t border-foreground pt-7">
      <h2 id={id} className="sr-only">
        Top takeaways
      </h2>
      <TakeawayCard takeaway={lead} signals={signals} variant="lead" edition={briefing.edition} hrefFor={hrefFor} isSelf={isSelf} />
      {rest.length > 0 ? (
        // Two abreast from 2xl, split by a column rule; stacked below it, parted by a hairline.
        <div className={cn("mt-10 grid border-t pt-8", pair && "2xl:grid-cols-2")}>
          {rest.map((t, i) => (
            <div
              key={`${t.title}-${i}`}
              className={cn("min-w-0", pair && (i === 0 ? "pb-10 2xl:pr-8 2xl:pb-0" : "border-t pt-8 2xl:border-t-0 2xl:border-l 2xl:pt-0 2xl:pl-8"))}
            >
              <TakeawayCard takeaway={t} signals={signals} variant="secondary" edition={briefing.edition} hrefFor={hrefFor} />
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

/**
 * The sections, minus every story the takeaways already tell: a story appears once per
 * briefing. Current briefings never repeat one; older ones
 * repeated each takeaway in its section, and read without the repeat. Within the sections the
 * first mention wins.
 */
function unrepeated(briefing: BriefingOut) {
  const told = new Set(briefing.content.top_takeaways.slice(0, 3).flatMap((t) => t.signal_ids));
  return briefing.content.sections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => {
        if (told.has(item.signal_id)) return false;
        told.add(item.signal_id);
        return true;
      }),
    }))
    .filter((section) => section.items.length > 0);
}

function Published({ briefing }: { briefing: BriefingOut }) {
  const signals = useMemo(() => indexSignals(briefing.signals), [briefing.signals]);
  const sections = useMemo(() => unrepeated(briefing), [briefing]);
  // the stories already told on the page, so a watch note never repeats their headline
  const told = useMemo(
    () =>
      new Set([
        ...briefing.content.top_takeaways.slice(0, 3).flatMap((t) => t.signal_ids),
        ...sections.flatMap((s) => s.items.map((i) => i.signal_id)),
      ]),
    [briefing, sections],
  );
  return (
    <>
      <TopTakeaways briefing={briefing} />
      {sections.length > 0 ? (
        <div className="mt-14 flex flex-col gap-12">
          {sections.map((section) => (
            <BriefingSection key={section.theme} section={section} signals={signals} hrefFor={hrefFor} />
          ))}
        </div>
      ) : null}
      <div className="mt-14">
        <BriefingFooter
          watchlist={briefing.content.watchlist}
          quiet={briefing.content.quiet_competitors}
          signals={signals}
          told={told}
          hrefFor={hrefFor}
        />
      </div>
      <p className="mt-12 border-t pt-4 text-meta text-muted-foreground">
        Compiled by FrogEye's agents from {briefing.signals.length} {briefing.signals.length === 1 ? "signal" : "signals"}
        {briefing.version > 1 ? `, revision ${briefing.version}` : ""}. Open any story for its full analysis: every source, and why
        it ranks where it does.
      </p>
    </>
  );
}

/** The reader's edition was not filed for this day, so the master stands in for it. */
function FallbackNote({ data, today }: { data: BriefingOut; today: string }) {
  const label = `${editionLabel(data.requested_edition)} edition`;
  const text = data.date === today ? `Today's ${label} wasn't filed` : `The ${label} wasn't filed for ${plainDay(data.date, "short")}`;
  return (
    <p className="mt-5 flex max-w-[62ch] items-start gap-2 text-meta text-muted-foreground">
      <InfoIcon aria-hidden className="mt-px size-4 shrink-0" />
      <span>{`${text}; showing the master.`}</span>
    </p>
  );
}

/**
 * A filed briefing in the reader's own edition: its lede and body, or its state. A written day
 * lays out in two columns from `xl`: the stories, and a sticky right rail with today at a
 * glance; below `xl` the glance is a strip between the lede and the stories.
 */
function FiledEdition({
  data,
  today,
  isLatest,
  isAnalyst,
  llmOff,
  dimmed,
  list,
}: {
  data: BriefingOut;
  today: string;
  isLatest: boolean;
  isAnalyst: boolean;
  llmOff: boolean;
  dimmed: boolean;
  list: BriefingSummary[] | undefined;
}) {
  const written = data.status === "published" || data.status === "quiet";
  // master stands in for a missing edition. Skipped and failed days file master only, so no note there.
  const fellBack = written && data.edition !== data.requested_edition;
  const published = data.status === "published";
  return (
    <div
      aria-busy={dimmed || undefined}
      className={cn(
        "transition-opacity duration-200 ease-out-quart",
        dimmed && "opacity-55",
        published && "xl:grid xl:grid-cols-[minmax(0,1fr)_18rem] xl:gap-x-12 2xl:grid-cols-[minmax(0,1fr)_21rem] 2xl:gap-x-16",
      )}
    >
      <div className="min-w-0 xl:col-start-1 xl:row-start-1">
        {fellBack ? <FallbackNote data={data} today={today} /> : null}
        {llmOff && isLatest ? <LlmOffNote isAnalyst={isAnalyst} /> : null}
        <Lede briefing={data} />
      </div>
      {published ? (
        <>
          <div className="mb-12 xl:col-start-2 xl:row-span-2 xl:row-start-1 xl:mb-0 xl:pt-7">
            {/* Sticky, and scrollable on its own when it is taller than the window, so its last list is never out of reach. */}
            <TodayAtAGlance
              briefing={data}
              className="xl:sticky xl:top-[5.5rem] xl:max-h-[calc(100dvh-7rem)] xl:overflow-y-auto xl:overscroll-contain xl:pr-1 xl:[scrollbar-width:thin]"
            />
          </div>
          <div className="min-w-0 xl:col-start-1 xl:row-start-2">
            <Published briefing={data} />
          </div>
        </>
      ) : (
        <div className="flex flex-col gap-12">
          {data.status === "skipped_budget" ? <Skipped isAnalyst={isAnalyst} isLatest={isLatest} /> : null}
          {data.status === "failed" ? <Failed briefing={data} isAnalyst={isAnalyst} /> : null}
          <LastFullBriefing date={data.date} list={list} />
          {data.status === "quiet" ? (
            <BriefingFooter
              watchlist={data.content.watchlist}
              quiet={data.content.quiet_competitors}
              signals={indexSignals(data.signals)}
              hrefFor={hrefFor}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}

/* ---- the page ---- */

/** The page's frame: `data-wide` lifts the shell's 76rem cap for this page only (AppShell). */
function Page({ children }: { children: ReactNode }) {
  return <article data-wide>{children}</article>;
}

function BriefingView({ date, edition }: { date: string | undefined; edition: Edition }) {
  const { user } = useAuth();
  const isAnalyst = user?.role === "analyst";

  const today = dayKey(new Date(useNow()).toISOString());
  const briefing = useBriefing(date, edition);
  const list = useBriefingList(edition);
  const llmOff = useLlmDisabled();

  const data = briefing.data;
  const missing = briefing.error instanceof ApiError && briefing.error.status === 404;

  if (briefing.isPending || !data) {
    let body: ReactNode = <BriefingSkeleton />;
    if (missing) body = date ? <NoBriefingForDate date={date} /> : <NoBriefing isAnalyst={isAnalyst} llmOff={llmOff} edition={edition} />;
    else if (briefing.isError) body = <BriefingLoadError onRetry={() => briefing.refetch()} retrying={briefing.isRefetching} />;
    return (
      <Page>
        <Nameplate date={date ?? today} edition={edition} />
        {body}
      </Page>
    );
  }

  const isLatest = !date || list.data?.[0]?.date === data.date;
  return (
    <Page>
      <Nameplate date={data.date} edition={edition} publishedAt={data.published_at}>
        <DateNav date={data.date} list={list.data} isLatest={isLatest} />
      </Nameplate>
      <FiledEdition
        data={data}
        today={today}
        isLatest={isLatest}
        isAnalyst={isAnalyst}
        llmOff={llmOff}
        dimmed={briefing.isPlaceholderData}
        list={list.data}
      />
    </Page>
  );
}

/**
 * `/briefing` (the latest) and `/briefing/:date`, in the reader's own edition only (the API
 * serves it; analysts read the master). Every story opens its full analysis page, `/signals/:id`.
 */
export function BriefingPage() {
  const { date } = useParams();
  const { user } = useAuth();
  const edition = editionFor(user?.role);
  if (date !== undefined && !isBriefingDate(date)) {
    return (
      <Page>
        <Nameplate edition={edition} />
        <NotABriefingDate />
      </Page>
    );
  }
  return <BriefingView date={date} edition={edition} />;
}
