import { useState } from "react";
import { LineChart } from "@/components/charts/LineChart";
import { nameFromSlug, useCompetitorIndex } from "@/lib/competitors";
import { plural } from "@/lib/format";
import { useRules } from "@/lib/intelReference";
import { QuietNote, ProfileSection, SubHead } from "@/features/intel/grammar";
import { dayMonth, weekOf } from "@/features/intel/words";
import { measured } from "@/features/intel/series";
import { voiceSummary } from "../profileWords";
import { hasData, ledgerRows, notesFor, sectionOf, voiceSeries, type CompetitorProfile } from "../useCompetitorProfile";
import { FactLedger, Toggle } from "./shared";

/*
 * Market: how much of the conversation it gets against the three competitors closest to it (the
 * ones sharing the most JFrog products with it: the competitor and its top 3 peers, four
 * colours), weekly over the weeks the API serves (13, or those collected on a young
 * deployment), as articles or as public signals; a peer's missing week is a measured 0 once
 * it has appeared. Market share is shown only as a cited report states it, never
 * estimated.
 */

const SHARE_RULE = "Market share appears only when a cited report states it";

type Metric = "documents" | "signals";
const METRICS: { value: Metric; label: string }[] = [
  { value: "documents", label: "Articles" },
  { value: "signals", label: "Signals" },
];

export function MarketSection({ profile, today }: { profile: CompetitorProfile; today: string }) {
  const [metric, setMetric] = useState<Metric>("documents");
  const rules = useRules();
  const index = useCompetitorIndex();
  const nameOf = (slug: string) => index.get(slug)?.name ?? nameFromSlug(slug);
  const series = voiceSeries(profile, nameOf, metric);
  // a measured zero line is drawn when a peer has mentions; all zero is no data, an empty state.
  const drawn = measured(series);
  const { market_share, undated } = profile.market;
  const note = notesFor(profile, "market")[0] ?? "No market share figure has been reported in its sources yet";
  const peers = profile.market.peers.slice(0, 3).map(nameOf);
  // The weeks served: 13 once collection is old enough, else those collected.
  const weeks = plural(new Set(profile.market.share_of_voice.map((p) => p.week_start)).size || rules.trend.weeks, "week");
  // nothing counted and no share reported: the section is quiet lines, not an empty chart and a box.
  if (!hasData(profile, "market")) {
    return (
      <ProfileSection section={sectionOf("market")} meta={`Share of voice: ${weeks} to ${dayMonth(today)}`}>
        <div className="flex flex-col gap-2">
          <QuietNote note={`Nothing counted for it or its peers in ${weeks}`} hint={`Each week counts the articles linked to ${profile.competitor.name} and its closest peers, and their public signals.`} />
          <QuietNote note={note} hint={`${SHARE_RULE}.`} />
        </div>
      </ProfileSection>
    );
  }
  return (
    <ProfileSection section={sectionOf("market")} meta={`Share of voice: ${weeks} to ${dayMonth(today)}`}>
      <LineChart
        title="Share of voice"
        summary={drawn ? voiceSummary(series, metric) : ""}
        empty={{
          title: `Nothing counted for it or its peers in ${weeks}`,
          hint: `No ${metric === "documents" ? "relevant article" : "public signal"} names ${profile.competitor.name} or its closest peers yet; each week counts the articles linked to each company and its public signals.`,
        }}
        series={drawn ? series : []}
        maxSeries={4}
        xFormat={weekOf}
        actions={<Toggle label="Count" options={METRICS} value={metric} onChange={setMetric} />}
        note={`${metric === "documents" ? "Relevant articles linked to each company" : "Each company's public signals"}, per 7-day week. Peers: ${peers.join(", ")}, the competitors sharing the most JFrog products with ${profile.competitor.name}.`}
      />
      {market_share.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHead title="Market share" meta={SHARE_RULE} />
          <FactLedger label="Market share" rows={ledgerRows(market_share)} />
        </div>
      ) : (
        <QuietNote note={note} hint={`${SHARE_RULE}.`} />
      )}
      {undated.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHead title="Undated" meta="market share stated without a date" />
          <FactLedger label="Undated" rows={ledgerRows(undated)} />
        </div>
      ) : null}
    </ProfileSection>
  );
}
