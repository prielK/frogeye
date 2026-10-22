import { plural } from "@/lib/format";
import { QuietNote, ProfileSection, SubHead } from "@/features/intel/grammar";
import { ledgerRows, notesFor, sectionOf, type CompetitorProfile } from "../useCompetitorProfile";
import { FactLedger, SignalLinks } from "./shared";

/*
 * People & partners: leadership changes, acquisitions, partnerships and offices its sources
 * state, each with its sources and date, undated ones apart, then the public signals about
 * its people and partners, each opening its analysis.
 */

export function PeoplePartnersSection({ profile }: { profile: CompetitorProfile }) {
  const { facts, undated, signals } = profile.people_partners;
  const note = notesFor(profile, "people_partners")[0] ?? "No leadership changes, acquisitions, partnerships or offices have been reported in its sources yet";
  const count = facts.length + undated.length;
  return (
    <ProfileSection
      section={sectionOf("people_partners")}
      meta={count + signals.length > 0 ? [count ? plural(count, "reported fact") : null, signals.length ? plural(signals.length, "signal") : null].filter(Boolean).join(" · ") : null}
    >
      {count + signals.length === 0 ? (
        <QuietNote note={note} hint="They appear when an article or the company's own pages report one, quoted and checked, or when FrogEye files a signal about it." />
      ) : null}
      {facts.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHead first title="Leadership, acquisitions, partners and offices" meta="newest first, every source listed" />
          <FactLedger label="People and partners" rows={ledgerRows(facts)} />
        </div>
      ) : null}
      {undated.length > 0 ? (
        <div className="flex flex-col gap-2">
          <SubHead title="Undated" meta="reported without a date" />
          <FactLedger label="Undated" rows={ledgerRows(undated)} />
        </div>
      ) : null}
      {signals.length > 0 ? (
        <div className="flex flex-col gap-1">
          <SubHead title="Signals" meta="leadership, acquisition and partnership news, newest first" />
          <SignalLinks label="People and partner signals" signals={signals} exclude={profile.competitor.slug} />
        </div>
      ) : null}
    </ProfileSection>
  );
}
