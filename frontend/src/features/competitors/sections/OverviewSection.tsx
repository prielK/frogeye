import { ArrowDownIcon, ArrowUpIcon } from "lucide-react";
import { dayDate } from "@/components/charts/scales";
import { Sparkline } from "@/components/charts/Sparkline";
import { Cites } from "@/features/analysis/CitedSources";
import { formatCount, formatPercent, plural } from "@/lib/format";
import { ratio, trendWord, useRules, youngCollection } from "@/lib/intelReference";
import { formatCalendarDay } from "@/lib/time";
import { cn } from "@/lib/utils";
import { HowComputed, NoteState, ProfileSection } from "@/features/intel/grammar";
import { dayLabel, dayMonth, evidenceLine, keepDates, weekOf } from "@/features/intel/words";
import { Figure, SUB, VALUE } from "@/features/intel/Figure";
import { ThreatChip } from "@/features/intel/ThreatChip";
import { changeIn, factValue, momentumLabel, noChangeYet, threatFormula } from "../profileWords";
import { BOARD_PENDING, formatMoney, formatOverlap, fundingOf, NO_BOARD } from "../useCompetitorIntel";
import { notesFor, sectionOf, type CompetitorProfile } from "../useCompetitorProfile";
import { Disagree, Freshness, SourceName } from "./shared";

/*
 * Overview: the so-what first. The brief's bottom line for JFrog leads, every sentence numbered to
 * its sources, dated and versioned; beside it, a ruled column of key figures, each with
 * where it comes from and when: the threat (shape and word, its score and drivers), the
 * 13-week momentum, open roles, the newest reported headcount and the total raised with its scope. Under them, how the threat is computed. Nothing unmeasured reads as a number.
 */

function ThreatFigure({ threat, today }: { threat: CompetitorProfile["kpis"]["threat"]; today: string }) {
  const days = useRules().threat.lookback_days;
  const drivers =
    threat.level === "insufficient"
      ? "Too little evidence to score yet"
      : [
          threat.overlap !== null ? `overlap ${formatOverlap(threat.overlap)}` : null,
          threat.momentum_trend !== null ? `trend ${ratio(threat.momentum_trend)}` : "no trend baseline",
          threat.hiring_trend !== null ? `hiring ${threat.hiring_trend >= 0 ? "+" : "−"}${formatPercent(Math.abs(threat.hiring_trend))}` : null,
        ]
          .filter(Boolean)
          .join(" · ");
  return (
    <Figure label="Threat to JFrog">
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <ThreatChip level={threat.level} />
        {threat.score !== null ? <span className={SUB}>score {Number(threat.score.toFixed(1))}</span> : null}
      </span>
      <span className={SUB}>{drivers}</span>
      <span className={SUB}>{`${plural(days, "day")} of public signals, to ${dayMonth(today)}`}</span>
    </Figure>
  );
}

function MomentumFigure({ profile, today }: { profile: CompetitorProfile; today: string }) {
  const rules = useRules();
  const weeks = profile.momentum;
  const trend = profile.kpis.threat.momentum_trend;
  // on a young deployment the trend has no baseline because collection began too recently.
  const since = profile.collected_since;
  const none = since && youngCollection(since, today, rules.trend) ? `no baseline yet: collected since ${dayMonth(since)}` : "no baseline yet";
  return (
    <Figure label="Momentum">
      <span className="flex items-center gap-2.5">
        <Sparkline label={momentumLabel(weeks, rules.trend.weeks)} values={weeks ? weeks.map((w) => ({ x: dayDate(w.week_start), y: w.value })) : null} xFormat={weekOf} />
        {weeks ? <span className={VALUE}>{formatCount(Math.round(weeks.at(-1)?.value ?? 0))}</span> : null}
      </span>
      {weeks ? <span className={SUB}>{trend === null ? none : `${ratio(trend)} · ${trendWord(trend, rules.trend)}`}</span> : null}
      <span className={SUB}>Signal importance a week, to {dayMonth(today)}</span>
    </Figure>
  );
}

function RolesFigure({ profile, today }: { profile: CompetitorProfile; today: string }) {
  const days = useRules().threat.hiring_history_days;
  const { kpis } = profile;
  const board = profile.sources_freshness.sources.find((s) => s.kind === "job_board");
  const stale = profile.hiring.not_readable_since;
  const state = kpis.open_roles !== null ? "measured" : stale ? "stale" : kpis.has_board ? "pending" : "none";
  if (state !== "measured") {
    // discovery is an operator's command (a miss kept 30 days); a stale board says since when.
    const sub = state === "stale" ? `Last listed in full on ${dayMonth(stale!)}` : state === "pending" ? "Open roles appear after its first complete listing" : "A board added later is found the next time job-board discovery runs";
    return (
      <Figure label="Open roles">
        {/* The API's own coverage note says why (D30); the board state is the fallback. */}
        <span className="text-body text-pretty text-muted-foreground">{keepDates(notesFor(profile, "hiring")[0] ?? (state === "pending" ? BOARD_PENDING : NO_BOARD))}</span>
        <span className={SUB}>{keepDates(sub)}</span>
      </Figure>
    );
  }
  const delta = kpis.delta_30d;
  const Arrow = delta !== null && delta < 0 ? ArrowDownIcon : ArrowUpIcon;
  return (
    <Figure label="Open roles">
      <span className={VALUE}>{formatCount(kpis.open_roles!)}</span>
      {delta === null ? (
        // a board read for under the change's window has no change, never its count read as growth.
        <span className={SUB}>{noChangeYet(profile.hiring.board_since, today, days)}</span>
      ) : delta === 0 ? (
        <span className={SUB}>{`No change in ${plural(days, "day")}`}</span>
      ) : (
        <span className={cn(SUB, "inline-flex items-center gap-0.5")}>
          <Arrow aria-hidden className="size-3 shrink-0" />
          {changeIn(delta, days)}
        </span>
      )}
      <span className={SUB}>
        {/* The counts are as of the board's last complete listing */}
        <Freshness name={board?.name ?? "Its public job board"} fetched={profile.hiring.as_of ?? board?.last_fetched_at ?? null} />
      </span>
    </Figure>
  );
}

function HeadcountFigure({ fact }: { fact: CompetitorProfile["kpis"]["latest_headcount"] }) {
  if (!fact || fact.value === null) {
    return (
      <Figure label="Headcount">
        <span className="text-body text-muted-foreground">Not reported</span>
        <span className={SUB}>No source has stated a headcount yet</span>
      </Figure>
    );
  }
  return (
    <Figure label="Headcount">
      <span className="flex flex-wrap items-baseline gap-x-1.5">
        <span className={VALUE}>{formatCount(fact.value)}</span>
        <span className={SUB}>{fact.unit ?? "employees"}</span>
      </span>
      <span className={SUB}>{fact.as_of ? `as of ${dayLabel(fact.as_of)}` : "No date given"}</span>
      {fact.sources_disagree ? <Disagree /> : null}
      <span className={cn(SUB, "flex flex-col gap-0.5")}>
        {fact.sources.map((s) => (
          <span key={s.fact_id} className="inline-flex flex-wrap items-baseline gap-x-1">
            <SourceName source={s} />
            {s.value !== null && s.value !== fact.value ? <span>states {factValue({ ...s, unit: null })}</span> : null}
          </span>
        ))}
      </span>
    </Figure>
  );
}

function FundingFigure({ kpis }: { kpis: CompetitorProfile["kpis"] }) {
  const total = kpis.funding_total;
  const last = kpis.last_round;
  const lastLine = last ? (
    <span className={cn(SUB, "flex flex-wrap items-baseline gap-x-1.5")}>
      <span>{`Last: ${last.label}, ${last.amount !== null ? formatMoney(last.amount, last.currency) : "amount not disclosed"}, ${dayLabel(last.as_of)}`}</span>
      {last.sources[0] ? <SourceName source={last.sources[0]} /> : null}
    </span>
  ) : null;
  if (!total) {
    return (
      <Figure label="Total raised">
        <span className="text-body text-muted-foreground">{last ? "Amounts not disclosed" : "Not reported"}</span>
        {lastLine ?? <span className={SUB}>No funding round in its sources yet</span>}
      </Figure>
    );
  }
  const { amount, scope, bound } = fundingOf(total);
  return (
    <Figure label="Total raised">
      <span className={VALUE}>{bound ? `${amount} disclosed` : amount}</span>
      <span className={SUB}>{scope}</span>
      {lastLine}
    </Figure>
  );
}

function BottomLine({ brief }: { brief: NonNullable<CompetitorProfile["brief"]> }) {
  const from = evidenceLine(brief.evidence);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <h3 className="text-body font-semibold text-foreground">Bottom line for JFrog</h3>
        <span className="text-meta text-muted-foreground">{`Written ${formatCalendarDay(brief.created_at)} · version ${brief.version}`}</span>
      </div>
      <div className="flex max-w-[65ch] flex-col gap-3">
        {brief.bottom_line.map((line, i) => (
          <p key={i} className="text-body text-pretty text-foreground">
            {line.text}
            <Cites cites={line.cites} />
          </p>
        ))}
      </div>
      {from ? <p className="max-w-[65ch] text-meta text-muted-foreground">{`Drawn ${from}; each claim is numbered to its source under Sources.`}</p> : null}
      {brief.watch.length > 0 ? (
        <div className="flex flex-col gap-2 pt-1">
          <h3 className="text-body font-semibold text-foreground">What to watch</h3>
          <ul className="flex max-w-[65ch] flex-col gap-2">
            {brief.watch.map((item, i) => (
              <li key={i} className="grid grid-cols-[0.875rem_minmax(0,1fr)] gap-x-2">
                <span aria-hidden className="mt-[0.5625rem] size-1.5 rounded-full bg-muted-foreground" />
                <p className="text-body text-pretty text-foreground">
                  {item.text}
                  <Cites cites={item.cites} />
                </p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function OverviewSection({ profile, today }: { profile: CompetitorProfile; today: string }) {
  const rules = useRules();
  const { brief, kpis } = profile;
  const note = notesFor(profile, "overview")[0];
  return (
    <ProfileSection section={sectionOf("overview")} meta={`Profile as of ${dayLabel(today)}`}>
      {/* Wide: the brief, then how the threat is computed, beside the key figures. Narrow: brief, figures, how. */}
      <div className="grid gap-x-10 gap-y-8 @[50rem]:grid-cols-[minmax(0,1fr)_17.5rem] @[50rem]:grid-rows-[auto_1fr]">
        {brief ? (
          <BottomLine brief={brief} />
        ) : (
          <NoteState
            note={note ?? "No brief has been written yet: briefs are written weekly"}
            className="self-start"
          />
        )}
        <ul aria-label="Key figures" className="grid grid-cols-2 content-start gap-x-6 gap-y-4 @[36rem]:grid-cols-3 @[50rem]:col-start-2 @[50rem]:row-span-2 @[50rem]:row-start-1 @[50rem]:grid-cols-1">
          <ThreatFigure threat={kpis.threat} today={today} />
          <MomentumFigure profile={profile} today={today} />
          <RolesFigure profile={profile} today={today} />
          <HeadcountFigure fact={kpis.latest_headcount} />
          <FundingFigure kpis={kpis} />
        </ul>
        <div className="@[50rem]:col-start-1 @[50rem]:row-start-2">
          <HowComputed>
            <p className="text-foreground">{kpis.threat.explanation}</p>
            <p>{threatFormula(rules)}</p>
          </HowComputed>
        </div>
      </div>
    </ProfileSection>
  );
}
