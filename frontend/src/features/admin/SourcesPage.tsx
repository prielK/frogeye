import { CircleXIcon, FlaskConicalIcon, RotateCwIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { CompetitorChip } from "@/components/common/CompetitorChip";
import { EmptyState } from "@/components/common/EmptyState";
import { Num } from "@/components/common/Num";
import { HealthBadge } from "@/components/common/HealthBadge";
import { RelativeTime } from "@/components/common/RelativeTime";
import { SearchInput } from "@/components/common/SearchInput";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Competitor, SourceOut } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { nameFromSlug, useCompetitorIndex } from "@/lib/competitors";
import { formatCount, plural } from "@/lib/format";
import { cn } from "@/lib/utils";
import { TestFetchDialog } from "./TestFetchDialog";
import { kindLabel, kindRank, RELIABILITY, useSources, useTestSource, useToggleSource } from "./useSources";

type HealthFilter = "all" | "down" | "degraded" | "ok";
const SEVERITY: Record<string, number> = { down: 0, degraded: 1, ok: 2 };

type Counts = { total: number; down: number; degraded: number; ok: number; paused: number };

function countHealth(sources: SourceOut[]): Counts {
  const counts: Counts = { total: sources.length, down: 0, degraded: 0, ok: 0, paused: 0 };
  for (const s of sources) {
    if (s.health === "down" || s.health === "degraded" || s.health === "ok") counts[s.health] += 1;
    if (!s.enabled) counts.paused += 1;
  }
  return counts;
}

type Group = { kind: string; sources: SourceOut[]; counts: Counts };

/** Kinds in a fixed order; inside each, problems first, then by name. */
function groupByKind(sources: SourceOut[]): Group[] {
  const byKind = new Map<string, SourceOut[]>();
  for (const s of sources) byKind.set(s.kind, [...(byKind.get(s.kind) ?? []), s]);
  return [...byKind.entries()]
    .sort(([a], [b]) => kindRank(a) - kindRank(b) || a.localeCompare(b))
    .map(([kind, list]) => ({
      kind,
      sources: list.sort(
        (a, b) => (SEVERITY[a.health] ?? 0) - (SEVERITY[b.health] ?? 0) || a.name.localeCompare(b.name),
      ),
      counts: countHealth(list),
    }));
}

/** The page's lead: what needs attention, in one sentence. */
function summarize(c: Counts): string {
  const paused = c.paused ? ` ${plural(c.paused, "source is", "sources are")} paused.` : "";
  if (c.down === 0 && c.degraded === 0) {
    return c.total === 1 ? `The one source is healthy.${paused}` : `All ${plural(c.total, "source")} are healthy.${paused}`;
  }
  const verb = (n: number) => (n === 1 ? "is" : "are");
  const first = c.down || c.degraded;
  const lead = `${formatCount(first)} of ${plural(c.total, "source")} ${verb(first)} ${c.down ? "down" : "degraded"}`;
  const also = c.down && c.degraded ? ` and ${formatCount(c.degraded)} degraded` : "";
  return `${lead}${also}.${paused}`;
}

function HealthFilterGroup({ value, counts, onChange }: { value: HealthFilter; counts: Counts; onChange: (v: HealthFilter) => void }) {
  const options: { value: HealthFilter; label: string; count: number }[] = [
    { value: "all", label: "All", count: counts.total },
    { value: "down", label: "Down", count: counts.down },
    { value: "degraded", label: "Degraded", count: counts.degraded },
    { value: "ok", label: "OK", count: counts.ok },
  ];
  return (
    <div role="group" aria-label="Show sources by health" className="inline-flex h-9 items-center rounded-md border border-input bg-card p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "inline-flex h-full items-center gap-1.5 rounded-sm px-2.5 text-body font-medium text-muted-foreground",
            "transition-colors duration-150 ease-out-quart hover:text-foreground",
            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
            "aria-pressed:bg-secondary aria-pressed:text-foreground",
          )}
        >
          {o.label}
          <Num value={o.count} className={cn("text-meta", o.count === 0 ? "text-muted-foreground/70" : "text-muted-foreground")} />
        </button>
      ))}
    </div>
  );
}

function GroupHeader({ group, colSpan }: { group: Group; colSpan: number }) {
  const { counts } = group;
  return (
    <TableRow className="border-b hover:bg-transparent">
      <th role="rowheader" scope="rowgroup" colSpan={colSpan} className="px-3 pt-7 pb-2 text-left align-bottom font-normal">
        <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-body font-semibold text-foreground">{kindLabel(group.kind)}</span>
          <span className="text-meta text-muted-foreground">{plural(counts.total, "source")}</span>
          {counts.down ? (
            <span className="inline-flex items-center gap-1 text-meta font-medium text-threat">
              <CircleXIcon aria-hidden className="size-3.5 self-center" />
              {counts.down} down
            </span>
          ) : null}
          {counts.degraded ? (
            <span className="inline-flex items-center gap-1 text-meta font-medium text-warning">
              <TriangleAlertIcon aria-hidden className="size-3.5 self-center" />
              {counts.degraded} degraded
            </span>
          ) : null}
          {counts.paused ? <span className="text-meta text-muted-foreground">{counts.paused} paused</span> : null}
        </span>
      </th>
    </TableRow>
  );
}

function Reliability({ grade }: { grade: string }) {
  const meaning = RELIABILITY[grade] ?? "Unknown grade";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex size-5 items-center justify-center rounded-sm border text-meta font-semibold text-foreground">
          {grade}
          <span className="sr-only">: {meaning}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent>
        {grade} · {meaning}
      </TooltipContent>
    </Tooltip>
  );
}

function LastError({ source }: { source: SourceOut }) {
  if (!source.last_error) return <span className="text-muted-foreground">—</span>;
  const streak = source.consecutive_failures;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          // Focusable so keyboard readers can open the full error; only failing rows have one.
          tabIndex={0}
          className="block truncate rounded-xs text-meta text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {streak > 1 ? <span className="font-medium text-foreground">{streak}× </span> : null}
          {source.last_error}
        </span>
      </TooltipTrigger>
      <TooltipContent align="end" className="max-w-96 text-left">
        {streak > 0 ? <span className="font-medium">Failed {plural(streak, "run")} in a row. </span> : null}
        {source.last_error}
      </TooltipContent>
    </Tooltip>
  );
}

type RowProps = {
  source: SourceOut;
  competitor: Competitor | undefined;
  canManage: boolean;
  onToggle: (source: SourceOut, enabled: boolean) => void;
  onTest: (source: SourceOut) => void;
};

function SourceRow({ source, competitor, canManage, onToggle, onTest }: RowProps) {
  const paused = !source.enabled;
  return (
    <TableRow data-paused={paused || undefined}>
      {canManage ? (
        <TableCell className="pr-0">
          <Switch
            size="sm"
            // A column of 100+ switches stays calm: "on" is ink, not brand green.
            className="data-[state=checked]:bg-muted-foreground data-[state=checked]:hover:bg-foreground"
            checked={source.enabled}
            onCheckedChange={(enabled) => onToggle(source, enabled)}
            aria-label={`Collect from ${source.name}`}
          />
        </TableCell>
      ) : null}
      <TableCell className="max-w-0">
        <span className="flex min-w-0 items-center gap-2">
          <span title={source.name} className={cn("truncate font-medium", paused ? "text-muted-foreground" : "text-foreground")}>
            {source.name}
          </span>
          {paused ? <Badge variant="outline">Paused</Badge> : null}
        </span>
      </TableCell>
      <TableCell className="max-w-0">
        {source.competitor_slug ? (
          <CompetitorChip
            slug={source.competitor_slug}
            name={competitor?.name ?? nameFromSlug(source.competitor_slug)}
            self={competitor?.is_self}
          />
        ) : (
          <span className="text-meta text-muted-foreground">Industry-wide</span>
        )}
      </TableCell>
      <TableCell>
        <Reliability grade={source.reliability} />
      </TableCell>
      <TableCell>
        <HealthBadge health={source.health} />
      </TableCell>
      <TableCell className={cn("text-right", source.items_7d === 0 && "text-muted-foreground")}>
        <Num value={source.items_7d} />
      </TableCell>
      <TableCell className="text-meta text-muted-foreground">
        <RelativeTime iso={source.last_fetched_at} emptyLabel="Never fetched" />
      </TableCell>
      <TableCell className="max-w-0">
        <LastError source={source} />
      </TableCell>
      {canManage ? (
        <TableCell className="pl-0 text-right">
          <Button
            variant="ghost"
            size="xs"
            onClick={() => onTest(source)}
            aria-label={`Test ${source.name}`}
            className="text-muted-foreground hover:text-foreground"
          >
            <FlaskConicalIcon />
            Test
          </Button>
        </TableCell>
      ) : null}
    </TableRow>
  );
}

function SourcesSkeleton() {
  return (
    <div role="status" aria-label="Loading sources" className="mt-6 flex flex-col">
      <Skeleton className="mb-3 h-4 w-44" />
      {Array.from({ length: 9 }, (_, i) => (
        <div key={i} className="flex items-center gap-6 border-b py-3">
          <Skeleton className={cn("h-4", i % 3 === 0 ? "w-56" : "w-40")} />
          <Skeleton className="h-5 w-24 rounded-full" />
          <Skeleton className="size-5" />
          <Skeleton className="h-4 w-12" />
          <Skeleton className="ml-auto h-4 w-16" />
        </div>
      ))}
    </div>
  );
}

export function SourcesPage() {
  const { user } = useAuth();
  const canManage = user?.role === "analyst";
  const sources = useSources();
  const competitors = useCompetitorIndex();
  const toggle = useToggleSource();
  const test = useTestSource();

  const [health, setHealth] = useState<HealthFilter>("all");
  const [query, setQuery] = useState("");
  const [testing, setTesting] = useState<SourceOut | null>(null);

  const all = useMemo(() => sources.data ?? [], [sources.data]);
  const counts = useMemo(() => countHealth(all), [all]);
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const visible = all.filter(
      (s) => (health === "all" || s.health === health) && (!q || s.name.toLowerCase().includes(q) || s.url.toLowerCase().includes(q)),
    );
    return groupByKind(visible);
  }, [all, health, query]);

  const columns = canManage ? 9 : 7;
  const startTest = (source: SourceOut) => {
    setTesting(source);
    test.mutate(source.id);
  };

  let body;
  if (sources.isPending) {
    body = <SourcesSkeleton />;
  } else if (sources.isError) {
    body = (
      <EmptyState
        tone="error"
        title="Sources didn't load."
        hint="FrogEye's API didn't answer. Check your connection, then try again."
        action={
          <Button variant="outline" size="sm" onClick={() => sources.refetch()} loading={sources.isRefetching}>
            <RotateCwIcon />
            Try again
          </Button>
        }
      />
    );
  } else if (all.length === 0) {
    body = (
      <EmptyState
        title="No sources yet."
        hint="Sources come from the seed list that ships with FrogEye. Load it with make seed, then refresh this page."
      />
    );
  } else {
    body = (
      <>
        <div className="flex flex-wrap items-center gap-2">
          <HealthFilterGroup value={health} counts={counts} onChange={setHealth} />
          <SearchInput
            value={query}
            onChange={setQuery}
            label="Filter sources by name or URL"
            placeholder="Filter by name or URL"
            className="w-full sm:ml-auto sm:w-64"
          />
        </div>

        {groups.length === 0 ? (
          <EmptyState
            className="mt-6"
            title="No sources match."
            hint="Try another health state, or clear the name filter."
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setHealth("all");
                  setQuery("");
                }}
              >
                <XIcon />
                Show all sources
              </Button>
            }
          />
        ) : (
          <Table className="mt-2 min-w-[58rem] table-fixed">
            <colgroup>
              {canManage ? <col className="w-12" /> : null}
              <col />
              <col className="w-48" />
              <col className="w-14" />
              <col className="w-28" />
              <col className="w-24" />
              <col className="w-24" />
              <col className="w-48" />
              {canManage ? <col className="w-20" /> : null}
            </colgroup>
            <TableHeader className="[&_tr]:border-b-0">
              <TableRow className="hover:bg-transparent">
                {canManage ? (
                  <TableHead className="pr-0">
                    <span className="sr-only">Collecting</span>
                  </TableHead>
                ) : null}
                <TableHead>Name</TableHead>
                <TableHead>Company</TableHead>
                <TableHead>
                  <abbr title="Admiralty source-reliability grade, A (best) to F" className="no-underline">
                    Grade
                  </abbr>
                </TableHead>
                <TableHead>Health</TableHead>
                <TableHead className="text-right">7-day items</TableHead>
                <TableHead>Last fetched</TableHead>
                <TableHead>Last error</TableHead>
                {canManage ? (
                  <TableHead className="pl-0">
                    <span className="sr-only">Test</span>
                  </TableHead>
                ) : null}
              </TableRow>
            </TableHeader>
            {groups.map((group) => (
              <TableBody key={group.kind} className="[&_tr:last-child]:border-b">
                <GroupHeader group={group} colSpan={columns} />
                {group.sources.map((source) => (
                  <SourceRow
                    key={source.id}
                    source={source}
                    competitor={source.competitor_slug ? competitors.get(source.competitor_slug) : undefined}
                    canManage={canManage}
                    onToggle={(s, enabled) => toggle.mutate({ id: s.id, enabled, name: s.name })}
                    onTest={startTest}
                  />
                ))}
              </TableBody>
            ))}
          </Table>
        )}
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Sources"
        description={sources.data && all.length > 0 ? summarize(counts) : "Where FrogEye collects from, and how healthy each source is."}
      />
      {body}
      {canManage ? (
        <TestFetchDialog
          source={testing}
          state={{
            pending: test.isPending,
            result: test.variables === testing?.id ? test.data : undefined,
            error: test.variables === testing?.id ? test.error : null,
          }}
          onRetry={() => testing && test.mutate(testing.id)}
          onOpenChange={(open) => {
            if (!open) {
              setTesting(null);
              test.reset();
            }
          }}
        />
      ) : null}
    </>
  );
}
