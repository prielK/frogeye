import { DotTimeline, type TimelineEvent, type TimelinePoint } from "@/components/charts/DotTimeline";
import { dayDate } from "@/components/charts/scales";
import { formatCount, plural } from "@/lib/format";
import { QuietNote, ProfileSection, SubHead } from "@/features/intel/grammar";
import { dayLabel } from "@/features/intel/words";
import { factValue, sourceDay, sourceName } from "../profileWords";
import { ledgerRows, notesFor, sectionOf, type CompetitorProfile, type HeadcountPoint } from "../useCompetitorProfile";
import { FactLedger } from "./shared";

/*
 * Headcount: reported figures only, a dot each and never a line between them (nobody counted the
 * months in between); two sources for one month are two dots, each with its source, a disputed
 * figure drawn hollow. Layoffs sit on the time axis only (their number counts
 * the cut, not the staff). Every figure is listed beneath with its sources and their dates;
 * undated figures get a list of their own and never a place on the axis.
 */

const REPORTED_ONLY = "Reported figures only — no line is drawn between them";

const source = (p: HeadcountPoint) => {
  const s = p.sources[0];
  return s ? [sourceName(s), sourceDay(s)].filter(Boolean).join(", ") : undefined;
};

/** "1,300 employees on 10 Jun 2026 (Sonatype about page); another source states 1,250 that month. 1 layoff reported: 10% on 20 Jan 2025." */
function summarize(points: HeadcountPoint[]): string {
  const figures = points.filter((p) => !p.layoff && p.value !== null);
  const layoffs = points.filter((p) => p.layoff);
  const parts: string[] = [];
  if (figures.length) {
    const latest = figures.reduce((a, b) => (b.as_of >= a.as_of ? b : a));
    const month = latest.as_of.slice(0, 7);
    const others = figures.filter((p) => p !== latest && p.as_of.startsWith(month) && p.value !== latest.value);
    const who = latest.sources[0] ? ` (${sourceName(latest.sources[0])})` : "";
    parts.push(
      `${formatCount(latest.value!)} employees on ${dayLabel(latest.as_of)}${who}${others.length ? `; another source states ${others.map((o) => formatCount(o.value!)).join(" and ")} that month` : ""}.`,
    );
  }
  if (layoffs.length) {
    const last = layoffs.reduce((a, b) => (b.as_of >= a.as_of ? b : a));
    const size = factValue(last);
    parts.push(`${plural(layoffs.length, "layoff")} reported${size ? `, the latest ${size} on ${dayLabel(last.as_of)}` : `, the latest on ${dayLabel(last.as_of)}`}.`);
  }
  return parts.join(" ");
}

/** "4 reported figures and 1 layoff": a layoff is not a headcount figure. */
function countLine(points: HeadcountPoint[]): string {
  const layoffs = points.filter((p) => p.layoff).length;
  const figures = plural(points.length - layoffs, "reported figure");
  return layoffs ? `${figures} and ${plural(layoffs, "layoff")}` : figures;
}

export function HeadcountSection({ profile }: { profile: CompetitorProfile }) {
  const { points, undated } = profile.headcount;
  const note = notesFor(profile, "headcount")[0] ?? "No headcount or layoff figures have been reported in its sources yet";
  const dots: TimelinePoint[] = points
    .filter((p) => !p.layoff && p.value !== null)
    .map((p, i) => ({ key: `${p.as_of}-${p.value}-${i}`, x: dayDate(p.as_of), y: p.value!, disputed: p.sources_disagree, detail: source(p) }));
  const events: TimelineEvent[] = points
    .filter((p) => p.layoff)
    .map((p, i) => ({ key: `layoff-${p.as_of}-${i}`, x: dayDate(p.as_of), label: factValue(p) ? `Layoff of ${factValue(p)}` : "Layoff, size not stated", detail: source(p) }));
  const newest = [...points].sort((a, b) => b.as_of.localeCompare(a.as_of))[0];
  if (points.length + undated.length === 0) {
    return (
      <ProfileSection section={sectionOf("headcount")}>
        <QuietNote note={note} hint="A figure appears when an article or the company's own pages state one; each is quoted and checked against its source, and never estimated." />
      </ProfileSection>
    );
  }
  return (
    <ProfileSection section={sectionOf("headcount")} meta={newest ? `${countLine(points)}, the newest of ${dayLabel(newest.as_of)}` : `${plural(undated.length, "undated figure")}`}>
      <DotTimeline
        title="Reported headcount"
        summary={summarize(points) || "Only undated figures so far: they are listed below, never placed on the time axis."}
        empty={{ title: "No dated figure yet", hint: "Undated figures are listed below; they never get a place on the time axis." }}
        points={dots}
        events={events}
        valueLabel="Reported headcount"
        eventLabel="Layoff reported"
        note={REPORTED_ONLY}
      />
      {points.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHead title="Reported figures" meta="newest first, every source listed" />
          <FactLedger label="Reported figures" rows={ledgerRows(points)} />
        </div>
      ) : null}
      {undated.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHead title="Undated" meta="no source dates these, so they are not on the axis" />
          <FactLedger label="Undated" rows={ledgerRows(undated)} />
        </div>
      ) : null}
    </ProfileSection>
  );
}
