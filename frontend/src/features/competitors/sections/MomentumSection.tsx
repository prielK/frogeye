import { useNavigate } from "react-router";
import { LineChart } from "@/components/charts/LineChart";
import { dayDate } from "@/components/charts/scales";
import { Swimlane } from "@/components/charts/Swimlane";
import { analysisHref } from "@/components/signal/param";
import { shiftDay } from "@/lib/days";
import { plural } from "@/lib/format";
import { ratio, useRules } from "@/lib/intelReference";
import { HowComputed, ProfileSection, QuietNote } from "@/features/intel/grammar";
import { dayMonth, lanesNote, weekOf } from "@/features/intel/words";
import { collectedLine, momentumFormula, momentumSummary, timelineSummary, youngWords } from "../profileWords";
import { hasData, notesFor, sectionOf, type CompetitorProfile } from "../useCompetitorProfile";

/*
 * Momentum: is it moving faster, and with what kind of moves? The weekly momentum (summed signal
 * importance per week, from the week collection began on a young deployment; its trend in
 * the summary), then every public move of the lookback in its lane (Launches, Pricing, Partners,
 * Money, People, Security, Market), dot size its importance; a dot opens its analysis. An
 * insufficient threat has no momentum series: the chart says so, never a flat line of
 * zeros. The windows and thresholds are the API's (GET /intel-reference).
 */

const LANES = ["Launches", "Pricing", "Partners", "Money", "People", "Security", "Market"].map((l) => ({ key: l, label: l }));

export function MomentumSection({ profile, today }: { profile: CompetitorProfile; today: string }) {
  const navigate = useNavigate();
  const rules = useRules();
  const days = rules.threat.lookback_days;
  const weeks = profile.momentum;
  const trend = profile.kpis.threat.momentum_trend;
  const young = youngWords(profile.collected_since, today, rules);
  const note = notesFor(profile, "momentum")[0];
  const events = profile.timeline.map((e) => ({
    key: String(e.signal_id),
    lane: e.lane,
    x: dayDate(e.day),
    title: e.title,
    size: e.importance / 100,
    detail: `Importance ${e.importance}`,
  }));
  const signals = new Set(profile.timeline.map((e) => e.signal_id)).size;
  // no public signal and no series: one quiet line, not an empty chart over an empty swimlane.
  if (!hasData(profile, "momentum")) {
    return (
      <ProfileSection section={sectionOf("momentum")} meta={`No public signals in ${plural(days, "day")}`}>
        <QuietNote
          note={note ?? `No public signals about it in the last ${plural(days, "day")}`}
          hint={`Momentum is measured once it has ${rules.threat.min_signals} public signals in ${plural(days, "day")} or a job board listed in full; its moves appear by kind as FrogEye files them.`}
        />
      </ProfileSection>
    );
  }
  return (
    <ProfileSection section={sectionOf("momentum")} meta={signals === 0 ? `No public signals in ${plural(days, "day")}` : `${plural(signals, "public signal")} in ${plural(days, "day")}, to ${dayMonth(today)}`}>
      <LineChart
        title="Weekly momentum"
        summary={weeks ? momentumSummary(weeks, trend, rules, young) : ""}
        empty={{
          title: "Insufficient data",
          hint: `Momentum is measured once it has ${rules.threat.min_signals} public signals in ${plural(days, "day")} or a job board listed in full.`,
        }}
        series={weeks ? [{ key: "momentum", label: "Momentum", points: weeks.map((w) => ({ x: dayDate(w.week_start), y: w.value })) }] : []}
        xFormat={weekOf}
        height={200}
        note="Each week: the summed importance (0–100 each) of its public signals in that week, 7-day weeks ending today."
      />
      <Swimlane
        title="Moves by kind"
        summary={events.length ? timelineSummary(profile.timeline, days) : ""}
        empty={{ title: note ?? `No public signals about it in the last ${plural(days, "day")}`, hint: "Its moves appear here as FrogEye files public signals about it: launches, pricing, partners, money, people, security and market news." }}
        lanes={LANES}
        events={events}
        domain={[dayDate(shiftDay(today, 1 - days)), dayDate(today)]}
        href={(e) => analysisHref(Number(e.key))}
        onNavigate={navigate}
        note={["Dot size is the signal's importance. Open a dot for its full analysis.", lanesNote(profile.collected_since, today, days)].filter(Boolean).join(" ")}
      />
      <HowComputed>
        <p>{momentumFormula(rules)}</p>
        {trend !== null ? (
          <p className="text-foreground">{`Its trend now: the last ${rules.trend.recent_weeks} weeks average ${ratio(trend)} the ${rules.trend.baseline_weeks} weeks before.`}</p>
        ) : young && profile.collected_since ? (
          <p className="text-foreground">{collectedLine(profile.collected_since, rules)}</p>
        ) : null}
      </HowComputed>
    </ProfileSection>
  );
}
