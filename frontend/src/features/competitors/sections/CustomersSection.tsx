import { plural } from "@/lib/format";
import { QuietNote, ProfileSection, SubHead } from "@/features/intel/grammar";
import { ledgerRows, notesFor, sectionOf, type CompetitorProfile } from "../useCompetitorProfile";
import { FactLedger, SignalLinks } from "./shared";

/*
 * Customers: named customers and customer counts its sources state, each with its sources and
 * date, undated ones apart, then the customer wins and losses FrogEye has filed as signals,
 * each opening its analysis.
 */

export function CustomersSection({ profile }: { profile: CompetitorProfile }) {
  const { facts, undated, signals } = profile.customers;
  const note = notesFor(profile, "customers")[0] ?? "No customers or customer wins and losses have been reported in its sources yet";
  const count = facts.length + undated.length;
  return (
    <ProfileSection section={sectionOf("customers")} meta={count + signals.length > 0 ? [count ? plural(count, "reported fact") : null, signals.length ? plural(signals.length, "signal") : null].filter(Boolean).join(" · ") : null}>
      {count + signals.length === 0 ? (
        <QuietNote note={note} hint="A customer appears when an article, a case study or the company's own pages name one; a win or a loss, when FrogEye files it as a signal." />
      ) : null}
      {facts.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHead first title="Customers and customer counts" meta="newest first, every source listed" />
          <FactLedger label="Customers" rows={ledgerRows(facts)} />
        </div>
      ) : null}
      {undated.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHead title="Undated" meta="named without a date" />
          <FactLedger label="Undated" rows={ledgerRows(undated)} />
        </div>
      ) : null}
      {signals.length > 0 ? (
        <div className="flex flex-col gap-1">
          <SubHead title="Wins and losses" meta="public signals, newest first" />
          <SignalLinks label="Customer signals" signals={signals} exclude={profile.competitor.slug} />
        </div>
      ) : null}
    </ProfileSection>
  );
}
