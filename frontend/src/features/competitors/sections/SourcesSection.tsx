import { HealthBadge } from "@/components/common/HealthBadge";
import { CitedSourceList, ProfileSection, SubHead } from "@/features/intel/grammar";
import { formatCalendarDay } from "@/lib/time";
import { sourceKindLabel } from "../profileWords";
import { sectionOf, type CompetitorProfile } from "../useCompetitorProfile";
import { Freshness } from "./shared";

/*
 * Sources: the page's evidence, last. The brief's numbered sources (the `[n]` markers above jump
 * here): a signal opens its analysis (one hidden from readers is plain text), every other kind
 * links out, with its kind, outlet and day, and a fact's verified quote or a page change's
 * summary. Then every collection source behind the page, with its health and last fetch, and the
 * notes about the sources themselves (each section says why it is empty under its own rule).
 */

export function SourcesSection({ profile }: { profile: CompetitorProfile }) {
  const { brief } = profile;
  // only the source-level notes; every other section says its own why under its rule.
  const own = profile.sources_freshness.notes.filter((n) => n.section === "sources");
  const sources = profile.sources_freshness.sources;
  return (
    <ProfileSection section={sectionOf("sources")} meta={`${sources.length} collection ${sources.length === 1 ? "source" : "sources"}${brief ? `, ${brief.sources.length} cited` : ""}`}>
      {brief && brief.sources.length > 0 ? (
        <div className="flex flex-col gap-1">
          <SubHead first title="Cited in the brief" meta={`version ${brief.version}, written ${formatCalendarDay(brief.created_at)}`} />
          <CitedSourceList sources={brief.sources} />
        </div>
      ) : null}
      <div className="flex flex-col gap-2">
        <SubHead first={!(brief && brief.sources.length > 0)} title="Collected from" meta="its sources, with their health and last fetch" />
        {own.map((n) => (
          <p key={n.text} className="text-body text-muted-foreground">
            {n.text}.
          </p>
        ))}
        {sources.length > 0 ? (
          <ul aria-label="Collected from" className="flex flex-col divide-y">
            {sources.map((s) => (
              <li key={`${s.kind}-${s.name}`} className="grid gap-x-6 gap-y-0.5 py-2.5 first:pt-1 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
                <span className="text-body text-foreground">{s.name}</span>
                <span className="flex flex-wrap items-center gap-x-3 text-meta text-muted-foreground">
                  <HealthBadge health={s.health} />
                  {/* The section metas' Freshness words ("fetched yesterday", lower case mid-line) */}
                  <Freshness name={sourceKindLabel(s.kind)} fetched={s.last_fetched_at} />
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </ProfileSection>
  );
}
