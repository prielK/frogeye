import { useState } from "react";
import { useNavigate } from "react-router";
import { dayDate } from "@/components/charts/scales";
import { Swimlane } from "@/components/charts/Swimlane";
import { analysisHref } from "@/components/signal/param";
import { Button } from "@/components/ui/button";
import { ProfileSection, SubHead } from "@/features/intel/grammar";
import { dayMonth, lanesNote } from "@/features/intel/words";
import { SignalRow } from "@/features/signals/SignalRow";
import { shiftDay } from "@/lib/days";
import { plural } from "@/lib/format";
import { useRules } from "@/lib/intelReference";
import { laneKeys, movesSummary, signalCount } from "../useProducts";
import { section, type SectionProps } from "./shared";

/* Signals timeline: every move naming it in its competitor's lane, each opening its analysis, then the newest signals. */

const NEWEST = 8;

export function TimelineSection({ profile, today, nameOf }: SectionProps) {
  const navigate = useNavigate();
  const days = useRules().threat.lookback_days;
  const [all, setAll] = useState(false);
  const name = profile.name;
  const lanes = laneKeys(profile).map((key) => ({ key, label: nameOf(key) }));
  const events = profile.timeline.map((e) => ({
    key: `${e.signal_id}:${e.competitor_slug}`,
    lane: e.competitor_slug,
    x: dayDate(e.day),
    title: e.title,
    size: e.importance / 100,
    detail: `Importance ${e.importance} · ${e.lane}`,
  }));
  const n = signalCount(profile.timeline);
  const signals = all ? profile.signals : profile.signals.slice(0, NEWEST);
  return (
    <ProfileSection section={section("timeline")} meta={n === 0 ? `No public signals in ${plural(days, "day")}` : `${plural(n, "public signal")} in ${plural(days, "day")}, to ${dayMonth(today)}`}>
      <Swimlane
        title="Moves by competitor"
        summary={events.length ? movesSummary(profile.timeline, name, nameOf, days) : ""}
        empty={{
          title: `No public signal names ${name} in the last ${plural(days, "day")}`,
          hint: `Moves appear here, each in its competitor's lane, as FrogEye files public signals whose analysis names ${name}.`,
        }}
        lanes={lanes}
        events={events}
        domain={[dayDate(shiftDay(today, 1 - days)), dayDate(today)]}
        href={(e) => analysisHref(Number(e.key.split(":")[0]))}
        onNavigate={navigate}
        note={["Dot size is the signal's importance; a move about two competitors sits in both lanes. Open a dot for its full analysis.", lanesNote(profile.collected_since, today, days)].filter(Boolean).join(" ")}
      />
      {profile.signals.length > 0 ? (
        <div className="flex flex-col gap-1">
          <SubHead title="Newest signals" meta={`${plural(profile.signals.length, "public signal")} naming it, newest first`} />
          <ul aria-label="Newest signals" className="flex flex-col divide-y">
            {signals.map((s) => (
              <li key={s.id}>
                <SignalRow signal={s} href={analysisHref(s.id)} />
              </li>
            ))}
          </ul>
          {!all && profile.signals.length > NEWEST ? (
            <Button variant="outline" size="sm" className="mt-2 w-fit" onClick={() => setAll(true)}>
              {`Show all ${profile.signals.length} signals`}
            </Button>
          ) : null}
        </div>
      ) : null}
    </ProfileSection>
  );
}
