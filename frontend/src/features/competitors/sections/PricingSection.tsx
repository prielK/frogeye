import { ChevronRightIcon } from "lucide-react";
import { useId } from "react";
import { ExternalLink } from "@/components/signal/SourceLink";
import { Badge } from "@/components/ui/badge";
import { HealthBadge } from "@/components/common/HealthBadge";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";
import { QuietNote, ProfileSection } from "@/features/intel/grammar";
import { dayLabel } from "@/features/intel/words";
import { notesFor, sectionOf, type CompetitorProfile, type PageChange } from "../useCompetitorProfile";
import { Freshness } from "./shared";

/*
 * Pricing: every meaningful change seen on its watched pricing page (and packaging or pricing
 * changes judged on its other pages), newest first: the day, what changed in a sentence, and the
 * diff itself, removed lines and added lines told apart by their sign in a gutter (− and +) and
 * a tint, never colour alone. The newest diff is open; the rest are one click away. Long lines
 * wrap: a page's text never scrolls the page sideways.
 */

type Line = { kind: "added" | "removed" | "context"; text: string } | { kind: "hunk"; text: string };

function parseDiff(diff: string): Line[] {
  return diff.split("\n").flatMap((raw): Line[] => {
    if (raw.startsWith("@@")) {
      const m = /\+(\d+)(?:,(\d+))?/.exec(raw);
      const start = m ? Number(m[1]) : null;
      const count = m && m[2] !== undefined ? Number(m[2]) : 1;
      return [{ kind: "hunk", text: start === null ? "…" : count > 1 ? `Lines ${start}–${start + count - 1}` : `Line ${start}` }];
    }
    if (raw.startsWith("+")) return [{ kind: "added", text: raw.slice(1) }];
    if (raw.startsWith("-")) return [{ kind: "removed", text: raw.slice(1) }];
    if (raw === "" || raw.startsWith("\\")) return [];
    return [{ kind: "context", text: raw.startsWith(" ") ? raw.slice(1) : raw }];
  });
}

const PAGE_WORD: Record<string, string> = { pricing: "pricing page", about: "about page", careers: "careers page", product: "product page", docs: "docs page" };
const CHANGE_WORD: Record<string, string> = { pricing: "Pricing change", packaging: "Packaging change", product: "Product change", messaging: "Messaging change" };

function DiffViewer({ change, open }: { change: PageChange; open: boolean }) {
  const lines = parseDiff(change.diff ?? "");
  const added = lines.filter((l) => l.kind === "added").length;
  const removed = lines.filter((l) => l.kind === "removed").length;
  return (
    <details open={open} className="group/diff">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-sm text-meta font-medium text-muted-foreground transition-colors duration-150 select-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon aria-hidden className="size-3.5 transition-transform duration-150 ease-out-quart group-open/diff:rotate-90" />
        <span className="group-open/diff:hidden">Show what changed</span>
        <span className="hidden group-open/diff:inline">Hide what changed</span>
        <span aria-hidden>·</span>
        <span>{`+${added} −${removed} lines`}</span>
      </summary>
      <div role="group" aria-label={`Changed lines, ${dayLabel(change.day)}`} className="mt-2 max-h-96 overflow-y-auto rounded-md border bg-card py-1 text-meta">
        {lines.map((line, i) =>
          line.kind === "hunk" ? (
            <div key={i} className="mx-0 my-1 border-y bg-muted/60 px-3 py-0.5 text-muted-foreground first:mt-0 first:border-t-0">
              {line.text}
            </div>
          ) : (
            <div
              key={i}
              className={cn(
                "grid grid-cols-[1.75rem_minmax(0,1fr)] py-px pr-3",
                line.kind === "added" && "bg-primary-soft",
                line.kind === "removed" && "bg-secondary",
              )}
            >
              <span aria-hidden className={cn("text-center font-semibold select-none", line.kind === "added" ? "text-primary-ink" : "text-muted-foreground")}>
                {line.kind === "added" ? "+" : line.kind === "removed" ? "−" : ""}
              </span>
              <span>
                {line.kind !== "context" ? <span className="sr-only">{line.kind === "added" ? "Added: " : "Removed: "}</span> : null}
                <span data-diff={line.kind} className={cn("whitespace-pre-wrap wrap-anywhere", line.kind === "removed" ? "text-muted-foreground" : "text-foreground")}>
                  {line.text}
                </span>
              </span>
            </div>
          ),
        )}
      </div>
    </details>
  );
}

function ChangeEntry({ change, open }: { change: PageChange; open: boolean }) {
  const titleId = useId();
  const page = PAGE_WORD[change.page_kind ?? ""] ?? "page";
  return (
    <article aria-labelledby={titleId} className="grid gap-x-6 gap-y-1.5 py-4 first:pt-1 sm:grid-cols-[7.5rem_minmax(0,1fr)]">
      <time dateTime={change.day} className="text-meta text-muted-foreground sm:pt-0.5">
        {dayLabel(change.day)}
      </time>
      <div className="flex min-w-0 flex-col gap-2">
        <h3 id={titleId} className="max-w-[72ch] text-body font-medium text-pretty text-foreground">
          {change.summary ?? `Its ${page} changed`}
        </h3>
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
          {change.change_kind ? <Badge variant="outline">{CHANGE_WORD[change.change_kind] ?? change.change_kind}</Badge> : null}
          <ExternalLink href={change.url} className="font-medium text-foreground">
            {`View the ${page}`}
          </ExternalLink>
        </p>
        {change.diff ? <DiffViewer change={change} open={open} /> : <p className="text-meta text-muted-foreground">No diff was stored for this change.</p>}
      </div>
    </article>
  );
}

export function PricingSection({ profile }: { profile: CompetitorProfile }) {
  const watched = profile.sources_freshness.sources.find((s) => s.kind === "page_watch");
  const note = notesFor(profile, "pricing")[0] ?? "No pricing page is watched for this competitor";
  return (
    <ProfileSection
      section={sectionOf("pricing")}
      meta={
        watched ? (
          <span className="inline-flex flex-wrap items-center gap-x-2">
            <Freshness name={watched.name} fetched={watched.last_fetched_at} verb="checked" />
            {watched.health !== "ok" ? <HealthBadge health={watched.health} /> : null}
          </span>
        ) : null
      }
    >
      {profile.pricing.length === 0 ? (
        <QuietNote note={note} hint="A watched page is read once a day; a change that matters to pricing or packaging is listed here with its diff." />
      ) : (
        <>
          <p className="text-body text-muted-foreground">{`${plural(profile.pricing.length, "pricing or packaging change")} seen, newest first.`}</p>
          <div className="flex flex-col divide-y">
            {profile.pricing.map((change, i) => (
              <ChangeEntry key={change.snapshot_id} change={change} open={i === 0} />
            ))}
          </div>
        </>
      )}
    </ProfileSection>
  );
}
