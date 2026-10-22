import { DivergingBars } from "@/components/charts/DivergingBars";
import { dayDate } from "@/components/charts/scales";
import { HowComputed, ProfileSection } from "@/features/intel/grammar";
import { dayMonth, weekOf, weightWords } from "@/features/intel/words";
import { plural } from "@/lib/format";
import { useRules } from "@/lib/intelReference";
import { pressureFormula, pressureLine, pressureSummary } from "../useProducts";
import { section, type SectionProps } from "./shared";

/*
 * Pressure over time: threat below one baseline and opportunity above it, week by week. On a young
 * deployment the weeks start where collection began; a product whose baseline weeks name no
 * threat has no baseline either, and the summary says which.
 */

export function PressureSection({ profile, today, young }: SectionProps) {
  const rules = useRules();
  const weeks = profile.pressure_series;
  const name = profile.name;
  const baseline = profile.pressure_baseline;
  const since = profile.collected_since;
  const fresh = young && since !== null;
  const meta = fresh ? `${plural(weeks.length, "week")} collected, to ${dayMonth(today)}` : `${plural(rules.trend.weeks, "week")} to ${dayMonth(today)}`;
  const quiet = fresh ? `since ${dayMonth(since)}` : `in ${plural(rules.trend.weeks, "week")}`;
  return (
    <ProfileSection section={section("pressure")} meta={meta}>
      <DivergingBars
        title="Weekly pressure"
        summary={weeks.length ? pressureSummary(weeks, profile.pressure, rules, baseline, young) : ""}
        empty={{
          title: `No competitive pressure ${quiet}`,
          hint: `No public signal's analysis names ${name} as a threat or an opportunity ${fresh ? `since ${dayMonth(since)}, when FrogEye began collecting` : `in the last ${plural(rules.trend.weeks, "week")}`}.`,
        }}
        rows={weeks.map((w) => ({ key: w.week_start, x: dayDate(w.week_start), above: w.opportunity, below: w.threat }))}
        sides={{ above: { label: "Opportunity", color: "opportunity" }, below: { label: "Threat", color: "threat" } }}
        xFormat={weekOf}
        note={`Each week: the severity-weighted (${weightWords(rules.severity_weights)}) threat and opportunity that the analyses of public signals name for ${name}; a signal counts once.`}
      />
      <HowComputed>
        <p>{pressureFormula(rules)}</p>
        {weeks.some((w) => w.threat > 0) ? <p className="text-foreground">{`Its weeks now: ${pressureLine(weeks, rules, baseline, young, since)}.`}</p> : null}
      </HowComputed>
    </ProfileSection>
  );
}
