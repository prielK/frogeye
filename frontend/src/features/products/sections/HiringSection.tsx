import { BarList } from "@/components/charts/BarList";
import { LineChart } from "@/components/charts/LineChart";
import { ProfileSection } from "@/features/intel/grammar";
import { measured } from "@/features/intel/series";
import { capabilityName, dayMonth } from "@/features/intel/words";
import { plural } from "@/lib/format";
import { areaNote, areaSeries, areaSummary, areaToday, areaTodayNote, joinWords } from "../useProducts";
import { section, type SectionProps } from "./shared";

/*
 * Competitor hiring in its area: one line per competitor hiring for the product's
 * capabilities, each from its own board's first snapshot; a board no longer listed in full is not
 * counted, and the footnote says so.
 */

export function HiringSection({ profile, nameOf, names }: SectionProps & { names: Map<string, string> }) {
  const area = profile.hiring_in_area;
  const name = profile.name;
  const caps = area.capabilities.map((slug) => capabilityName(slug, names));
  const series = areaSeries(area, nameOf);
  const today = areaToday(area);
  const read = area.boards.filter((b) => !b.stale);
  const stale = area.boards.filter((b) => b.stale);
  const boards = read.length;
  // boards are found by job-board discovery, an operator's command; only then are they read daily.
  const empty =
    caps.length === 0
      ? { title: "No capability is mapped to this product's area", hint: `Hiring here counts competitor postings tagged with the capabilities FrogEye maps to ${name}; none is mapped yet.` }
      : boards === 0
        ? {
            title: "No competitor job board has been read yet",
            hint: "FrogEye reads competitors' public boards on Greenhouse, Lever and Ashby once job-board discovery has found them; hiring in this area fills in after the first is read.",
          }
        : { title: "No open role names its capabilities", hint: `None of the ${plural(boards, "competitor job board")} FrogEye reads lists an open posting tagged ${joinWords(caps, "or")}.` };
  const drawn = measured(series) ? series : [];
  // One snapshot (every board first read today): no change over time to draw yet, so
  // today's open roles by competitor, as bars; the line starts with the next daily snapshot.
  const oneDay = area.total.length === 1;
  // a board not listed in full for days is left out of the count, said by name.
  const staleNote = stale.length
    ? ` ${joinWords(stale.map((b) => `${nameOf(b.competitor_slug)}'s`))} ${stale.length === 1 ? "board has" : "boards have"} not been readable since ${joinWords(stale.map((b) => dayMonth(b.last_listed)))}, so ${stale.length === 1 ? "it is" : "they are"} not counted.`
    : "";

  return (
    <ProfileSection section={section("hiring")} meta={boards ? `${plural(boards, "public job board")} read` : "No job board read yet"}>
      {oneDay ? (
        <BarList
          title="Open roles in its area"
          summary={drawn.length && today ? areaSummary(today, nameOf) : ""}
          empty={empty}
          items={drawn.length && today ? today.competitors.map((c) => ({ key: c.slug, label: nameOf(c.slug), value: c.open })) : []}
          unit="open roles"
          note={`${areaTodayNote(caps)}${staleNote}`}
        />
      ) : (
        <LineChart
          title="Open roles in its area"
          summary={drawn.length && today ? areaSummary(today, nameOf) : ""}
          empty={empty}
          series={drawn}
          area={false}
          height={220}
          note={`${areaNote(series, caps, nameOf, area.total[0]?.day)}${staleNote}`}
        />
      )}
    </ProfileSection>
  );
}
