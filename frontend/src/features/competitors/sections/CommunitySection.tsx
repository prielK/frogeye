import { useState } from "react";
import { LineChart } from "@/components/charts/LineChart";
import { dayDate } from "@/components/charts/scales";
import { ExternalLink } from "@/components/signal/SourceLink";
import { shiftDay } from "@/lib/days";
import { formatCount, plural } from "@/lib/format";
import { MetaLine, ProfileSection, QuietNote } from "@/features/intel/grammar";
import { dayMonth, weekOf } from "@/features/intel/words";
import { measured } from "@/features/intel/series";
import { hasData, notesFor, sectionOf, type CompetitorProfile } from "../useCompetitorProfile";
import { Freshness, Toggle } from "./shared";

/*
 * Community: its open-source pull (GitHub stars or forks of the repositories FrogEye tracks, a
 * line per repository, the top 4 named and the rest as "Other") and its developer conversation
 * (Hacker News items about it per week). Each figure carries its source and its day: the
 * collection sources and their last fetch in the section's meta, the last reading's day in the
 * summary, every repository linked to GitHub. Stars are levels, so a day with no reading is left
 * out of the line, never read as zero; a series where nothing was counted is an empty state.
 */

type Metric = "stars" | "forks";
const METRICS: { value: Metric; label: string }[] = [
  { value: "stars", label: "Stars" },
  { value: "forks", label: "Forks" },
];

const repoUrl = (repo: string) => `https://github.com/${repo}`;

/** The stars summary's own span (not an API rule): its change is against the newest reading at least this many days before the last, and it names that day. */
const CHANGE_SPAN_DAYS = 30;

/** "sonatype/nexus-public leads with 3,620 stars on 20 Oct, +42 since 15 Sept." The change names its own span. */
function repoSummary(repos: CompetitorProfile["community"]["repos"], metric: Metric): string {
  const latest = repos
    .map((r) => {
      const points = r[metric];
      const last = points.at(-1);
      if (!last) return null;
      // The newest reading at least 30 days before the last one; its day is said, so the span is exact.
      const before = [...points].reverse().find((p) => p.day <= shiftDay(last.day, -CHANGE_SPAN_DAYS));
      return { repo: r.repo, last, before };
    })
    .filter((r): r is NonNullable<typeof r> => r !== null);
  if (latest.length === 0) return "";
  const top = latest.reduce((a, b) => (b.last.value > a.last.value ? b : a));
  const delta = top.before ? top.last.value - top.before.value : null;
  const change =
    delta === null || !top.before
      ? ""
      : delta === 0
        ? `, no change since ${dayMonth(top.before.day)}`
        : `, ${delta > 0 ? "+" : "−"}${formatCount(Math.abs(delta))} since ${dayMonth(top.before.day)}`;
  const others = latest.length > 1 ? ` (${latest.length} repositories tracked)` : "";
  return `${top.repo} leads with ${formatCount(top.last.value)} ${metric} on ${dayMonth(top.last.day)}${change}${others}.`;
}

export function CommunitySection({ profile }: { profile: CompetitorProfile }) {
  const [metric, setMetric] = useState<Metric>("stars");
  const { repos, mentions } = profile.community;
  const notes = notesFor(profile, "community");
  const repoNote = notes.find((n) => n.includes("GitHub")) ?? "No GitHub repositories are tracked for this competitor";
  const hnNote = notes.find((n) => n.includes("Hacker News")) ?? "No Hacker News search runs for this competitor";
  const series = repos.map((r) => ({ key: r.repo, label: r.repo, points: r[metric].map((p) => ({ x: dayDate(p.day), y: p.value })) }));
  const hasRepos = measured(series);
  // tracked repositories with nothing counted for this metric (every fork 0) are a
  // measured nothing, said as such; the toggle stays, so the other count is one click away.
  const tracked = repos.length > 0;
  const noun = metric === "stars" ? "star" : "fork";
  const hn = [{ key: "hn", label: "Mentions", points: mentions.map((m) => ({ x: dayDate(m.week_start), y: m.count })) }];
  const hasMentions = measured(hn);
  const total = mentions.reduce((s, m) => s + m.count, 0);
  const last = mentions.at(-1);
  const sources = profile.sources_freshness.sources;
  const github = sources.find((s) => s.kind === "github_repo_stats");
  const news = sources.find((s) => s.kind === "hn");
  const meta =
    github || news ? (
      <MetaLine
        className="gap-x-3"
        parts={[
          github ? <Freshness key="gh" name={github.name} fetched={github.last_fetched_at} /> : null,
          news ? <Freshness key="hn" name={news.name} fetched={news.last_fetched_at} /> : null,
        ]}
      />
    ) : (
      "GitHub and Hacker News"
    );
  // nothing of its own (no counts, no mentions): one quiet line per reason, not two empty charts.
  if (!hasData(profile, "community")) {
    return (
      <ProfileSection section={sectionOf("community")} meta={meta}>
        <div className="flex flex-col gap-2">
          <QuietNote
            note={tracked ? `No ${noun}s recorded` : repoNote}
            hint={tracked ? `FrogEye reads its ${repos.length === 1 ? "repository" : `${repos.length} repositories`} once a day.` : "Repositories come from FrogEye's seed; their stars and forks are read once a day."}
          />
          <QuietNote
            note={mentions.length ? `No Hacker News mentions in ${plural(mentions.length, "week")}` : hnNote}
            hint={mentions.length ? "Its Hacker News search runs every day; relevant items about it are counted per week." : "Its Hacker News search runs every day once it is set up for this competitor."}
          />
        </div>
      </ProfileSection>
    );
  }
  return (
    <ProfileSection section={sectionOf("community")} meta={meta}>
      <div className="flex flex-col gap-10">
        {/* One repository: the change is the point, so its axis starts near the lowest reading. Several start
            from zero, so a smaller one never sits on the floor as if it had none. */}
        <LineChart
          title={metric === "stars" ? "GitHub stars" : "GitHub forks"}
          summary={hasRepos ? repoSummary(repos, metric) : ""}
          empty={
            tracked
              ? { title: `No ${noun}s recorded`, hint: `FrogEye reads its ${repos.length === 1 ? "repository" : `${repos.length} repositories`} once a day; none has a ${noun} recorded yet.` }
              : { title: repoNote, hint: "Repositories come from FrogEye's seed; their stars and forks are read once a day." }
          }
          series={hasRepos ? series : []}
          zero={series.length > 1}
          height={200}
          actions={tracked ? <Toggle label="Count" options={METRICS} value={metric} onChange={setMetric} /> : undefined}
          note={
            <>
              {series.length > 1 ? "Read once a day. " : "Read once a day; the axis starts near the lowest reading, so the change shows. "}
              Repositories:{" "}
              {repos.map((r, i) => (
                <span key={r.repo}>
                  {i > 0 ? ", " : null}
                  <ExternalLink href={repoUrl(r.repo)} arrow={false} className="font-medium text-foreground">
                    {r.repo}
                  </ExternalLink>
                </span>
              ))}
              .
            </>
          }
        />
        <LineChart
          title="Hacker News mentions"
          summary={hasMentions && last ? `${formatCount(total)} in ${plural(mentions.length, "week")} to ${dayMonth(shiftDay(last.week_start, 6))}; ${formatCount(last.count)} this week.` : ""}
          empty={
            mentions.length
              ? { title: `No Hacker News mentions in ${plural(mentions.length, "week")}`, hint: "Its Hacker News search runs every day; relevant items about it are counted per week." }
              : { title: hnNote, hint: "Its Hacker News search runs every day once it is set up for this competitor." }
          }
          series={hasMentions ? hn : []}
          xFormat={weekOf}
          height={200}
          note="Relevant Hacker News items about it, per 7-day week."
        />
      </div>
    </ProfileSection>
  );
}
