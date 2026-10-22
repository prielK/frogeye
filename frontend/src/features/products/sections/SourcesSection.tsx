import { CitedSourceList, ProfileSection, SubHead } from "@/features/intel/grammar";
import { plural } from "@/lib/format";
import { formatCalendarDay } from "@/lib/time";
import { QUIET, section, type SectionProps } from "./shared";

/* Sources: the brief's numbered sources, the targets of every [n], then how the page is built. */

export function SourcesSection({ profile }: SectionProps) {
  const { brief, name } = profile;
  return (
    <ProfileSection section={section("sources")} meta={brief ? `${plural(brief.sources.length, "source")} cited` : undefined}>
      {brief && brief.sources.length > 0 ? (
        <div className="flex flex-col gap-1">
          <SubHead first title="Cited in the brief" meta={`version ${brief.version}, written ${formatCalendarDay(brief.created_at)}`} />
          <CitedSourceList sources={brief.sources} />
        </div>
      ) : (
        <p className={QUIET}>{brief ? "This brief cites no source." : `No brief has been written for ${name} yet, so nothing is cited.`}</p>
      )}
      <div className="flex flex-col gap-2">
        <SubHead title="How this page is built" />
        <p className="max-w-[72ch] text-body text-pretty text-muted-foreground">
          {`Everything here comes from FrogEye's analyses of public signals about JFrog's competitors and from their public job boards: the competitive landscape around ${name}. It shows no JFrog company figures.`}
        </p>
      </div>
    </ProfileSection>
  );
}
