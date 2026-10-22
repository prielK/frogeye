import { RotateCwIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { BarList, type BarItem } from "@/components/charts/BarList";
import { LineChart } from "@/components/charts/LineChart";
import { dayDate } from "@/components/charts/scales";
import { RelativeTime } from "@/components/common/RelativeTime";
import { ExternalLink } from "@/components/signal/SourceLink";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ProfileSection, QuietNote, SubHead } from "@/features/intel/grammar";
import { capabilityName, dayLabel, dayMonth } from "@/features/intel/words";
import { useCapabilities } from "@/features/signals/useSignals";
import { formatCount, plural } from "@/lib/format";
import { useRules } from "@/lib/intelReference";
import { openRolesSummary, postingMeta } from "../profileWords";
import { BOARD_PENDING, NO_BOARD } from "../useCompetitorIntel";
import { deltasOver, lastDays, notesFor, sectionOf, usePostings, WINDOWS, type CompetitorProfile, type Posting, type PostingFilter, type Window } from "../useCompetitorProfile";
import { Freshness, Toggle } from "./shared";

/*
 * People & hiring, from its public job board (Greenhouse, Lever or Ashby): open roles over time
 * (a 7/30/90-day window), where they hire (department, seniority or location; the top 8 and
 * "Other", each with its 30-day change as a drawn arrow and a sign), which capabilities their
 * postings name (the product direction a hiring plan gives away), and the newest postings, each
 * linking to the posting, with every open role one click away. A capability or a department bar
 * opens the open roles it counts (`?tag=` or `?department=` in the address).
 *
 * What the board's state allows, said in words:
 * - no board found: when job-board discovery last checked, and that a board added later is found
 *   the next time it runs (discovery is an operator's command, a miss kept 30 days);
 * - a board found, never listed in full: pending;
 * - a board no longer listed in full (stale): its history to its last complete listing, no
 *   current count;
 * - measured: everything, with how many open roles have been read for product areas so far.
 */

type Dimension = "department" | "seniority" | "location";
const DIMENSIONS: { value: Dimension; label: string }[] = [
  { value: "department", label: "Department" },
  { value: "seniority", label: "Seniority" },
  { value: "location", label: "Location" },
];

/** "Engineering has the most open roles (48 of 142); Engineering grew most, +5 in 30 days." */
function mixSummary(items: BarItem[], noun: string, days: number): string {
  const span = plural(days, "day");
  const total = items.reduce((s, i) => s + i.value, 0);
  const top = items.reduce((a, b) => (b.value > a.value ? b : a));
  const growing = items.filter((i) => (i.delta ?? 0) > 0);
  const fastest = growing.length ? growing.reduce((a, b) => ((b.delta ?? 0) > (a.delta ?? 0) ? b : a)) : null;
  const lead = `${top.label} has the most open roles (${formatCount(top.value)} of ${formatCount(total)})`;
  // No 30-day change known (a board read for under 30 days): claim none, not even "none grew".
  if (items.every((i) => i.delta === null || i.delta === undefined)) return `${lead}.`;
  return fastest ? `${lead}; ${fastest.label === top.label ? "it also grew most" : `${fastest.label} grew most`}, +${formatCount(fastest.delta!)} in ${span}.` : `${lead}; no ${noun} grew in ${span}.`;
}

/** "Container registry is named in the most open roles (21); Model scanning grew fastest, +6 in 30 days." */
function tagsSummary(items: BarItem[], days: number): string {
  const top = items.reduce((a, b) => (b.value > a.value ? b : a));
  const growing = items.filter((i) => (i.delta ?? 0) > 0);
  const fastest = growing.length ? growing.reduce((a, b) => ((b.delta ?? 0) > (a.delta ?? 0) ? b : a)) : null;
  const lead = `${top.label} is named in the most open roles (${formatCount(top.value)})`;
  return fastest ? `${lead}; ${fastest.label === top.label ? "it also grew fastest" : `${fastest.label} grew fastest`}, +${formatCount(fastest.delta!)} in ${plural(days, "day")}.` : `${lead}.`;
}

/** What the tags are a share of: "Tags read on 128 of 142 open roles so far; 14 not tagged yet." Null when every open role has been read. */
function coverageLine(coverage: CompetitorProfile["hiring"]["tag_coverage"]): string | null {
  if (!coverage) return null;
  const read = coverage.tagged + coverage.untagged;
  const total = read + coverage.pending + coverage.gave_up;
  const waiting = coverage.pending + coverage.gave_up;
  if (waiting === 0 || total === 0) return null;
  return `Tags read on ${formatCount(read)} of ${plural(total, "open role")} so far; ${formatCount(waiting)} not tagged yet.`;
}

/** A posting's tagging state, when it has no tags to show. */
const TAG_STATE: Record<Posting["tag_state"], string | null> = { tagged: null, untagged: "No product area named", pending: "Not tagged yet", gave_up: "Tagging failed" };

function PostingRow({ posting, names }: { posting: Posting; names: Map<string, string> }) {
  const state = posting.tags.length === 0 ? TAG_STATE[posting.tag_state] : null;
  return (
    <li className="flex flex-col gap-1 py-3 first:pt-1">
      {posting.url ? (
        <ExternalLink href={posting.url} className="w-fit text-body font-medium text-foreground">
          {posting.title}
        </ExternalLink>
      ) : (
        <span className="text-body font-medium text-foreground">{posting.title}</span>
      )}
      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-meta text-muted-foreground">
        <span>{postingMeta(posting)}</span>
        <span aria-hidden>·</span>
        <span>
          First seen <RelativeTime iso={posting.first_seen_at} />
        </span>
        {state ? (
          <>
            <span aria-hidden>·</span>
            <span>{state}</span>
          </>
        ) : null}
      </p>
      {posting.tags.length > 0 ? (
        <ul aria-label="Capabilities" className="flex flex-wrap gap-1.5 pt-0.5">
          {posting.tags.slice(0, 4).map((t) => (
            <li key={t}>
              <Badge variant="outline">{capabilityName(t, names)}</Badge>
            </li>
          ))}
          {posting.tags.length > 4 ? <li className="text-meta text-muted-foreground">+{posting.tags.length - 4}</li> : null}
        </ul>
      ) : null}
    </li>
  );
}

/** The filter in the address: a shared link opens the same open roles. */
function useFilter(): [PostingFilter, () => void] {
  const [params, setParams] = useSearchParams();
  const tag = params.get("tag") || undefined;
  const department = (params.get("department") || undefined) as PostingFilter["department"];
  const clear = () =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("tag");
        next.delete("department");
        return next;
      },
      { preventScrollReset: true },
    );
  return [{ tag, department }, clear];
}

function Postings({ profile, names }: { profile: CompetitorProfile; names: Map<string, string> }) {
  const [all, setAll] = useState(false);
  const [filter, clear] = useFilter();
  const filtered = Boolean(filter.tag || filter.department);
  const pages = usePostings(profile.competitor.slug, all || filtered, filter);
  const items = (all || filtered) && pages.data ? pages.data.pages.flatMap((p) => p.items) : filtered ? [] : profile.hiring.recent;
  const open = profile.hiring.open_now ?? 0;
  const what = filter.tag ? `tagged ${capabilityName(filter.tag, names)}` : filter.department ? `in ${filter.department}` : null;
  const label = filtered ? "Filtered open roles" : all ? "Open roles" : "Newest open roles";
  const head = useRef<HTMLDivElement>(null);
  // A bar opens its roles here: bring them into view, and focus the head so Tab continues in them.
  const key = `${filter.tag ?? ""}|${filter.department ?? ""}`;
  useEffect(() => {
    if (!filtered) return;
    head.current?.scrollIntoView({ block: "start", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    head.current?.querySelector<HTMLElement>("h3")?.focus({ preventScroll: true });
  }, [key, filtered]);
  return (
    <div className="flex scroll-mt-32 flex-col gap-3 xl:scroll-mt-20" ref={head}>
      <SubHead
        title={filtered ? `Open roles ${what}` : label}
        meta={
          filtered
            ? pages.data
              ? `${formatCount(items.length)}${pages.hasNextPage ? "+" : ""} shown, newest first`
              : undefined
            : all
              ? `${formatCount(items.length)} of ${formatCount(open)} shown, newest first`
              : `the ${formatCount(items.length)} newest of ${formatCount(open)}`
        }
      />
      {filtered ? (
        <Button variant="ghost" size="xs" className="-mt-1 -ml-1.5 w-fit text-muted-foreground" onClick={clear}>
          <XIcon />
          Show all open roles
        </Button>
      ) : null}
      {filtered && pages.isPending ? (
        <p className="text-meta text-muted-foreground">Loading the open roles…</p>
      ) : items.length === 0 && !pages.isError ? (
        <p className="text-body text-muted-foreground">{filtered ? `No open role ${what} is listed on its board right now.` : "No open posting is listed on its board right now."}</p>
      ) : (
        <ul aria-label={label} className="flex flex-col divide-y">
          {items.map((p) => (
            <PostingRow key={p.id} posting={p} names={names} />
          ))}
        </ul>
      )}
      {pages.isError ? (
        <p role="alert" className="flex flex-wrap items-center gap-2 text-meta text-threat">
          The open roles didn't load.
          <Button variant="outline" size="xs" onClick={() => pages.refetch()}>
            <RotateCwIcon />
            Try again
          </Button>
        </p>
      ) : null}
      {!all && !filtered && open > items.length ? (
        <Button variant="outline" size="sm" className="w-fit" onClick={() => setAll(true)}>
          {`Show all ${formatCount(open)} open roles`}
        </Button>
      ) : null}
      {(all || filtered) && pages.hasNextPage ? (
        <Button variant="outline" size="sm" className="w-fit" loading={pages.isFetchingNextPage} onClick={() => pages.fetchNextPage()}>
          Show more open roles
        </Button>
      ) : null}
      {all && !filtered && pages.isPending ? <p className="text-meta text-muted-foreground">Loading every open role…</p> : null}
    </div>
  );
}

function useNames() {
  const capabilities = useCapabilities();
  return useMemo(() => new Map((capabilities.data ?? []).flatMap((c) => c.capabilities.map((k) => [k.slug, k.name] as const))), [capabilities.data]);
}

function Measured({ profile, today }: { profile: CompetitorProfile; today: string }) {
  const hiring = profile.hiring;
  // The API's change window (its 30-day changes, `delta_30d`), from GET /intel-reference.
  const days = useRules().threat.hiring_history_days;
  const [span, setSpan] = useState<Window>(90);
  const [dimension, setDimension] = useState<Dimension>("department");
  const names = useNames();
  const navigate = useNavigate();
  const [filter] = useFilter();
  // a bar opens the open roles it counts, through the address, so the link can be shared.
  const open = (href: string) => navigate(href, { preventScrollReset: true });
  const filterHref = (key: "tag" | "department", value: string) => `?${new URLSearchParams({ [key]: value })}#hiring`;

  const total = lastDays(hiring.series.total, span).map((p) => ({ x: dayDate(p.day), y: p.count }));
  const breakdown = { department: hiring.by_department, seniority: hiring.by_seniority, location: hiring.by_location }[dimension];
  // no change per key either while the board has less history than the change's window.
  const deltas = deltasOver(breakdown, hiring.series[dimension], hiring.series.total, hiring.delta_30d !== null, days);
  // The API filters by department only, so only that breakdown's bars open roles.
  const mix: BarItem[] = breakdown.map((b) => ({
    key: b.key,
    label: b.key,
    value: b.count,
    delta: deltas.get(b.key) ?? null,
    href: dimension === "department" ? filterHref("department", b.key) : undefined,
    current: dimension === "department" && filter.department === b.key,
  }));
  const tags: BarItem[] = hiring.tags.map((t) => ({ key: t.slug, label: capabilityName(t.slug, names), value: t.open, delta: t.delta_30d, href: filterHref("tag", t.slug), current: filter.tag === t.slug }));
  const word = DIMENSIONS.find((d) => d.value === dimension)!.label.toLowerCase();
  // A fetched board with no open role is a measured nothing, not missing data.
  const none = hiring.open_now === 0 ? "No open roles on its board right now" : null;
  const since = hiring.board_since;
  const summary = openRolesSummary(hiring.open_now!, hiring.delta_30d, since, today, days);
  // One snapshot (a board first listed today): no line yet, and no window to choose.
  const oneSnapshot = hiring.series.total.length < 2;
  const coverage = coverageLine(hiring.tag_coverage);

  return (
    <>
      {/* The change is the point here (the 30-day change, a 7-day window): the axis starts near the lowest count, no wash under the line. */}
      <LineChart
        title="Open roles"
        summary={summary}
        empty={
          oneSnapshot && hiring.series.total.length === 1
            ? { title: "One snapshot so far", hint: `${summary}. The line starts with the next daily snapshot.` }
            : { title: "No daily counts yet", hint: "Open roles are counted from each daily snapshot of the board." }
        }
        series={oneSnapshot ? [] : [{ key: "open", label: "Open roles", points: total }]}
        zero={false}
        area={false}
        actions={oneSnapshot ? undefined : <Toggle label="Window" options={WINDOWS.map((w) => ({ value: w, label: `${w} days` }))} value={span} onChange={setSpan} />}
        height={200}
        note={`Counted from each day's complete listing of the board${since ? `, from its first on ${dayLabel(since)}` : ""}; the axis starts near the lowest count, so the change shows. A ${days}-day change compares the latest listing with the one ${plural(days, "day")} before, so a board read for under ${plural(days, "day")} has none yet.`}
      />
      <div className="flex flex-col gap-10">
        <BarList
          title={`Open roles by ${word}`}
          summary={mix.length ? mixSummary(mix, word, days) : ""}
          empty={{ title: none ?? `No ${word} recorded for its open roles` }}
          items={mix}
          unit="open roles"
          deltaLabel={`in ${plural(days, "day")}`}
          onNavigate={open}
          actions={<Toggle label="Break down by" options={DIMENSIONS} value={dimension} onChange={setDimension} />}
          note={
            dimension === "location"
              ? "The 8 locations with the most open roles; the rest are Other."
              : dimension === "department"
                ? "The 8 largest; the rest fold into Other. Open a department for its open roles."
                : "The 8 largest; the rest fold into Other."
          }
        />
        <BarList
          title="Capabilities in its open roles"
          summary={tags.length ? tagsSummary(tags, days) : ""}
          empty={none ? { title: none } : { title: "No open role names a capability yet", hint: "Postings are tagged with the capability taxonomy as FrogEye collects them." }}
          items={tags}
          unit="open roles"
          deltaLabel={`in ${plural(days, "day")}`}
          onNavigate={open}
          note={[coverage, "A posting counts once for each capability it names. Open a capability for the roles that name it."].filter(Boolean).join(" ")}
        />
      </div>
      <Postings profile={profile} names={names} />
    </>
  );
}

/** A board no longer listed in full: its history to its last complete listing, and no current count. */
function Stale({ profile, note }: { profile: CompetitorProfile; note: string }) {
  const hiring = profile.hiring;
  const last = hiring.not_readable_since!;
  const points = hiring.series.total.map((p) => ({ x: dayDate(p.day), y: p.count }));
  const lastCount = hiring.series.total.at(-1)?.count;
  return (
    <>
      <QuietNote note={note} hint="FrogEye tries its board once a day; its counts return with the next complete listing." />
      <LineChart
        title="Open roles"
        summary={lastCount !== undefined ? `Its last complete listing, on ${dayMonth(last)}, had ${plural(lastCount, "open role")}; nothing since is counted.` : ""}
        empty={{ title: "No complete listing in the window", hint: `Its last complete listing was on ${dayLabel(last)}.` }}
        series={points.length > 1 ? [{ key: "open", label: "Open roles", points }] : []}
        zero={false}
        area={false}
        height={200}
        note={`Counted from each day's complete listing of the board, to its last on ${dayLabel(last)}; the days since are left out, never carried flat.`}
      />
    </>
  );
}

export function HiringSection({ profile, today }: { profile: CompetitorProfile; today: string }) {
  const board = profile.sources_freshness.sources.find((s) => s.kind === "job_board");
  const hiring = profile.hiring;
  const state = hiring.open_now !== null ? "measured" : hiring.not_readable_since ? "stale" : hiring.has_board ? "pending" : "none";
  const note = notesFor(profile, "hiring")[0];
  return (
    <ProfileSection section={sectionOf("hiring")} meta={board ? <Freshness name={board.name} fetched={board.last_fetched_at} /> : state === "none" ? null : "Its public job board"}>
      {state === "measured" ? (
        <Measured profile={profile} today={today} />
      ) : state === "stale" ? (
        <Stale profile={profile} note={note ?? `Job board not readable since ${dayLabel(hiring.not_readable_since!)}`} />
      ) : state === "pending" ? (
        <QuietNote note={note ?? BOARD_PENDING} hint="FrogEye reads the board once a day; open roles, where they are and the newest postings appear after its first complete listing." />
      ) : (
        // discovery is an operator's command, and a miss is kept 30 days; the API's note says when it last checked.
        <QuietNote note={note ?? NO_BOARD} hint="A board added later is found the next time job-board discovery runs." />
      )}
    </ProfileSection>
  );
}
