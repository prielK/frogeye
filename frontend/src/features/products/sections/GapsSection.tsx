import { Cites } from "@/features/analysis/CitedSources";
import { ProfileSection } from "@/features/intel/grammar";
import { formatCalendarDay } from "@/lib/time";
import { COMES_WITH_BRIEF, QUIET, section, type SectionProps } from "./shared";

/* Gaps and parity: the brief's statements as a hairline ledger, each cited; without a brief, one quiet line. */

export function GapsSection({ profile }: SectionProps) {
  const { brief, name } = profile;
  return (
    <ProfileSection section={section("gaps")} meta={brief ? `From the brief of ${formatCalendarDay(brief.created_at)}` : undefined}>
      {!brief ? (
        <p className={QUIET}>{COMES_WITH_BRIEF}</p>
      ) : brief.gaps_parity.length === 0 ? (
        <p className={QUIET}>{`The brief names no gap or parity point for ${name}.`}</p>
      ) : (
        <ul aria-label="Gaps and parity" className="flex max-w-[72ch] flex-col divide-y">
          {brief.gaps_parity.map((item, i) => (
            <li key={i} className="py-3 text-body text-pretty text-foreground first:pt-0 last:pb-0">
              {item.text}
              <Cites cites={item.cites} />
            </li>
          ))}
        </ul>
      )}
    </ProfileSection>
  );
}
