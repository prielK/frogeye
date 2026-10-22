import { dayDate } from "@/components/charts/scales";
import { StepLine, type Step } from "@/components/charts/StepLine";
import { plural } from "@/lib/format";
import { QuietNote, ProfileSection, SubHead } from "@/features/intel/grammar";
import { dayLabel } from "@/features/intel/words";
import { formatMoney, fundingOf } from "../useCompetitorIntel";
import { sourceName } from "../profileWords";
import { ledgerRows, notesFor, sectionOf, type CompetitorProfile, type FundingRound } from "../useCompetitorProfile";
import { FactLedger } from "./shared";

/*
 * Money: the dated funding rounds as columns under the cumulative total's step line, on one axis
 * (one currency: a round in another one is listed, not drawn). An undisclosed round is a dashed
 * ring and makes the total a lower bound ("≥ $X disclosed"); a round whose sources disagree is a
 * hollow ring. Undated rounds never get a place on the time axis: they are listed
 * under "Undated", and the key figure says how many it includes. Valuations, revenue and
 * ARR follow as reported figures, each with its sources.
 */

const roundRow = (r: FundingRound) => ({ kind: "funding_round" as const, label: r.label, value: r.amount, unit: null, currency: r.currency, as_of: r.as_of, sources_disagree: r.sources_disagree, sources: r.sources });

/**
 * "≥ $100M disclosed across 3 dated rounds (1 more undisclosed), ≥ $154M with the 1 undated round;
 * the latest, a $45M Series C on 12 Mar 2024." The chart's end label is the dated rounds' total, so
 * the summary names the all-in total beside it.
 */
function summarize(rounds: FundingRound[], currency: string | null, raised: CompetitorProfile["kpis"]["funding_total"]): string {
  const stated = rounds.filter((r) => r.amount !== null);
  const undisclosed = rounds.length - stated.length;
  const total = stated.reduce((s, r) => s + r.amount!, 0);
  const latest = rounds.at(-1)!;
  const last = `the latest, ${latest.amount !== null ? `a ${formatMoney(latest.amount, latest.currency)}` : "an undisclosed"} ${latest.label} on ${dayLabel(latest.as_of)}`;
  if (stated.length === 0) return `${plural(rounds.length, "dated round")}, none with a disclosed amount; ${last}.`;
  const sum = undisclosed > 0 ? `≥ ${formatMoney(total, currency)} disclosed` : `${formatMoney(total, currency)} raised`;
  const more = undisclosed > 0 ? ` (${undisclosed} more undisclosed)` : "";
  const all = raised && raised.undated_rounds > 0 && raised.currency === currency ? `, ${raised.excluded_rounds > 0 ? "≥ " : ""}${formatMoney(raised.amount, currency)} with the ${plural(raised.undated_rounds, "undated round")}` : "";
  return `${sum} across ${plural(stated.length, "dated round")}${more}${all}; ${last}.`;
}

export function MoneySection({ profile, today }: { profile: CompetitorProfile; today: string }) {
  const { funding_rounds, valuations, revenue, undated } = profile.money;
  const total = profile.kpis.funding_total;
  const currency = total?.currency ?? funding_rounds.find((r) => r.currency)?.currency ?? "USD";
  const drawn = funding_rounds.filter((r) => r.amount === null || r.currency === currency || r.currency === null);
  const elsewhere = funding_rounds.length - drawn.length;
  const steps: Step[] = drawn.map((r, i) => ({
    key: `${r.label}-${r.as_of}-${i}`,
    x: dayDate(r.as_of),
    amount: r.amount,
    label: r.label,
    disputed: r.sources_disagree,
    detail: r.sources[0] ? `${sourceName(r.sources[0])}${r.sources.length > 1 ? ` and ${plural(r.sources.length - 1, "more source")}` : ""}` : undefined,
  }));
  const figures = [...valuations, ...revenue];
  const note = notesFor(profile, "money")[0] ?? "No funding, valuation or revenue figures have been reported in its sources yet";
  if (funding_rounds.length + figures.length + undated.length === 0) {
    return (
      <ProfileSection section={sectionOf("money")}>
        <QuietNote note={note} hint="Rounds, valuations, revenue and ARR appear when a source states them; each is quoted and checked against its source." />
      </ProfileSection>
    );
  }
  const scope = total ? fundingOf(total) : null;
  const undatedRounds = undated.filter((f) => f.kind === "funding_round");
  const footnote = [
    undatedRounds.length ? `${plural(undatedRounds.length, "undated round")} ${undatedRounds.length === 1 ? "is" : "are"} listed below, never on the time axis.` : null,
    elsewhere ? `${plural(elsewhere, "round")} in another currency ${elsewhere === 1 ? "is" : "are"} listed, not drawn.` : null,
    "A dashed ring is a round whose amount was not disclosed; a hollow ring, one whose sources disagree.",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <ProfileSection section={sectionOf("money")} meta={scope ? `${scope.bound ? `${scope.amount} disclosed` : scope.amount} in all · ${scope.scope}` : "Reported rounds, valuations and revenue"}>
      <StepLine
        title="Funding raised"
        summary={drawn.length ? summarize(drawn, currency, total) : ""}
        empty={{ title: "No dated funding round", hint: "Undated rounds are listed below; they never get a place on the time axis." }}
        steps={steps}
        valueFormat={(n) => formatMoney(n, currency)}
        until={dayDate(today)}
        note={footnote}
      />
      {funding_rounds.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHead title="Funding rounds" meta="newest first, every source listed" />
          <FactLedger label="Funding rounds" rows={ledgerRows(funding_rounds.map(roundRow))} />
        </div>
      ) : null}
      {figures.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHead title="Valuation, revenue and ARR" />
          <FactLedger label="Valuation, revenue and ARR" rows={ledgerRows(figures)} />
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
