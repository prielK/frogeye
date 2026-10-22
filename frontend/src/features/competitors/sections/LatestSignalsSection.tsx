import { Link } from "react-router";
import { INLINE_LINK } from "@/components/common/links";
import { cn } from "@/lib/utils";
import { QuietNote, ProfileSection } from "@/features/intel/grammar";
import { notesFor, sectionOf, type CompetitorProfile } from "../useCompetitorProfile";
import { SignalLinks } from "./shared";

/*
 * Latest signals: its 20 newest public signals in the Explorer's row grammar, each one link to
 * its full analysis, the same list for every reader.
 */

export function LatestSignalsSection({ profile }: { profile: CompetitorProfile }) {
  const signals = profile.latest_signals;
  const note = notesFor(profile, "latest_signals")[0] ?? "No public signals about it yet";
  return (
    <ProfileSection
      section={sectionOf("latest_signals")}
      meta={
        signals.length ? (
          <>
            {`The ${signals.length} newest · `}
            <Link to={`/signals?competitor=${profile.competitor.slug}`} className={cn(INLINE_LINK, "font-medium text-foreground")}>
              All in Signals
            </Link>
          </>
        ) : null
      }
    >
      {signals.length === 0 ? (
        <QuietNote note={note} hint="A signal is filed when a public source reports a move of its: a launch, a price, a partner, money, people or security news." />
      ) : (
        <SignalLinks label="Latest signals" signals={signals} exclude={profile.competitor.slug} />
      )}
    </ProfileSection>
  );
}
