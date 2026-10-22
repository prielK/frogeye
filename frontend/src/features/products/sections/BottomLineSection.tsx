import type { ReactNode } from "react";
import { EmptyState } from "@/components/common/EmptyState";
import { Cites } from "@/features/analysis/CitedSources";
import { Figure, SUB, VALUE } from "@/features/intel/Figure";
import { ProfileSection } from "@/features/intel/grammar";
import { dayLabel, dayMonth, evidenceLine } from "@/features/intel/words";
import { formatCount, plural } from "@/lib/format";
import { useRules } from "@/lib/intelReference";
import { formatCalendarDay } from "@/lib/time";
import { areaToday, noBrief, pressureFigure, signalCount, threatMix } from "../useProducts";
import { QUIET, section, type SectionProps } from "./shared";

/* Bottom line: the brief's cited sentences beside a ruled column of key figures, never stat tiles. */

function KeyFigures({ profile, today, nameOf, young }: SectionProps) {
  const rules = useRules();
  const days = rules.threat.lookback_days;
  const comps = profile.competitors;
  const live = comps.filter((c) => c.evidence > 0).length;
  const mix = threatMix(comps);
  const signals = signalCount(profile.timeline);
  const latest = profile.timeline.reduce<string | null>((day, e) => (day === null || e.day > day ? e.day : day), null);
  const area = profile.hiring_in_area;
  const now = areaToday(area);
  const boards = area.boards.filter((b) => !b.stale).length;
  const pressure = pressureFigure(profile.pressure_series, rules, profile.pressure_baseline, young, profile.collected_since);
  // a young deployment's pressure spans the weeks since collection began.
  const span = young && profile.collected_since ? `since ${dayMonth(profile.collected_since)}` : `${plural(rules.trend.weeks, "week")} to ${dayMonth(today)}`;

  let hiring: ReactNode;
  if (area.capabilities.length === 0) hiring = <span className={QUIET}>No capability mapped yet</span>;
  else if (boards === 0 || !now) {
    hiring = (
      <>
        <span className={QUIET}>No job board read yet</span>
        <span className={SUB}>Competitors' public boards on Greenhouse, Lever and Ashby</span>
      </>
    );
  } else if (now.open === 0) {
    hiring = (
      <>
        <span className={QUIET}>No open role names its capabilities</span>
        <span className={SUB}>{`on ${plural(boards, "competitor job board")}, today`}</span>
      </>
    );
  } else {
    const [only] = now.competitors;
    hiring = (
      <>
        <span className={VALUE}>{plural(now.open, "open role")}</span>
        <span className={SUB}>{now.competitors.length === 1 ? `on ${nameOf(only!.slug)}'s job board, today` : `across ${formatCount(now.competitors.length)} competitors' job boards, today`}</span>
      </>
    );
  }

  return (
    <ul aria-label="Key figures" className="grid content-start gap-x-6 gap-y-4 @[28rem]:grid-cols-2 @[50rem]:grid-cols-1">
      <Figure label="Threat pressure">
        <span className={pressure.measured ? VALUE : QUIET}>{pressure.value}</span>
        <span className={SUB}>{pressure.sub}</span>
        <span className={SUB}>{`Severity-weighted, ${span}`}</span>
      </Figure>
      <Figure label="Competitors">
        {comps.length === 0 ? (
          <>
            <span className={QUIET}>None mapped yet</span>
            <span className={SUB}>No seeded product or public signal ties a competitor to it</span>
          </>
        ) : (
          <>
            <span className={live ? VALUE : QUIET}>{live ? `${formatCount(live)} with recent evidence` : "None with recent evidence"}</span>
            <span className={SUB}>{[mix, `${formatCount(comps.length)} tracked`].filter(Boolean).join(" · ")}</span>
          </>
        )}
      </Figure>
      <Figure label="Public signals">
        {signals > 0 ? (
          <>
            <span className={VALUE}>{`${formatCount(signals)} in ${plural(days, "day")}`}</span>
            {latest ? <span className={SUB}>{`the latest on ${dayMonth(latest)}`}</span> : null}
          </>
        ) : (
          <>
            <span className={QUIET}>{`None in ${plural(days, "day")}`}</span>
            <span className={SUB}>Public signals whose analysis names it</span>
          </>
        )}
      </Figure>
      <Figure label="Hiring in its area">{hiring}</Figure>
    </ul>
  );
}

export function BottomLineSection(props: SectionProps) {
  const { profile, today } = props;
  const { brief } = profile;
  const from = brief ? evidenceLine(brief.evidence) : null;
  const missing = noBrief(profile, useRules());
  return (
    <ProfileSection section={section("bottom-line")} meta={brief ? `Written ${formatCalendarDay(brief.created_at)} · version ${brief.version}` : `Page as of ${dayLabel(today)}`}>
      {/* Wide: the brief beside the key figures; narrow: the brief, then the figures. */}
      <div className="grid gap-x-10 gap-y-8 @[50rem]:grid-cols-[minmax(0,1fr)_17.5rem]">
        {brief && brief.bottom_line.length > 0 ? (
          <div className="flex min-w-0 flex-col gap-4">
            <div className="flex max-w-[65ch] flex-col gap-3">
              {brief.bottom_line.map((line, i) => (
                <p key={i} className="text-body text-pretty text-foreground">
                  {line.text}
                  <Cites cites={line.cites} />
                </p>
              ))}
            </div>
            {from ? <p className="max-w-[65ch] text-meta text-muted-foreground">{`Drawn ${from}; each claim is numbered to its source under Sources.`}</p> : null}
          </div>
        ) : brief ? (
          // a brief with no bottom line says so, never that no brief was written.
          <EmptyState
            className="max-w-none self-start"
            title="The brief has no bottom line"
            hint={`Version ${brief.version} states none; its gaps and parity and each team's actions, when it has them, follow below.`}
          />
        ) : (
          // the ONE note for everything that comes with the brief.
          <EmptyState
            className="max-w-none self-start"
            title={missing.title}
            hint={
              <>
                <span className="block">{missing.hint}</span>
                <span className="block">Its bottom line, gaps and parity and each team's actions appear here once one is written.</span>
              </>
            }
          />
        )}
        <KeyFigures {...props} />
      </div>
    </ProfileSection>
  );
}
