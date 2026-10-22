import { ArrowDownIcon, ArrowUpIcon, RotateCwIcon, XIcon } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { SectionHead } from "@/components/layout/SectionHead";
import { localToday } from "@/lib/days";
import { firstBaselineDay, RulesContext, useIntelReference, useRules, youngCollection } from "@/lib/intelReference";
import { plainDay, useNow } from "@/lib/time";
import { cn } from "@/lib/utils";
import { dayMonth } from "@/features/intel/words";
import { CompetitorTable } from "./CompetitorTable";
import { LandscapeChart } from "./LandscapeChart";
import { ThisWeek } from "./ThisWeek";
import {
  applyFilters,
  categoriesOf,
  clearFilters,
  FIRST_DIR,
  hasFilters,
  knownView,
  leadSentence,
  orderWords,
  parseView,
  productsOf,
  SORT_LABEL,
  sortRows,
  tiersOf,
  useCompetitorOverview,
  viewParams,
  type CompetitorSummary,
  type SortKey,
  type View,
} from "./useCompetitorIntel";

/*
 * The competitor list (`/competitors`): who matters to JFrog and who is moving,
 * the same for every persona. It leads with what changed (the week's biggest moves), then
 * one filter row scopes the two views below it alike: the landscape (overlap against momentum)
 * and the table of every competitor. Filters and order live in the address, so a shared link
 * opens the same view; the strip above them is the whole market's week and is not filtered.
 */

const ALL = "all";

type FacetProps = {
  label: string;
  value: string | undefined;
  any?: string;
  onChange: (value: string | undefined) => void;
  className?: string;
  children: ReactNode;
};

/** One filter as a select (the Signals Explorer's facet): unset reads muted, so what narrows the view stands out. */
function Facet({ label, value, any, onChange, className, children }: FacetProps) {
  const set = value !== undefined;
  return (
    <Select value={set ? value : ALL} onValueChange={(v) => onChange(v === ALL ? undefined : v)}>
      <SelectTrigger aria-label={label} className={cn("min-w-0 flex-1 basis-40 sm:flex-none", className, !set && any && "text-muted-foreground")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent position="popper" className="max-h-80">
        {any ? <SelectItem value={ALL}>{any}</SelectItem> : null}
        {children}
      </SelectContent>
    </Select>
  );
}

function Filters({ rows, view, onChange }: { rows: CompetitorSummary[]; view: View; onChange: (next: View) => void }) {
  const tiers = tiersOf(rows);
  const products = productsOf(rows, useRules().jfrog_products);
  const categories = categoriesOf(rows);
  return (
    <div role="search" aria-label="Filter competitors" className="flex flex-wrap items-center gap-2">
      <Facet label="Tier" value={view.tier === undefined ? undefined : String(view.tier)} any="All tiers" onChange={(v) => onChange({ ...view, tier: v ? Number(v) : undefined })} className="sm:w-36">
        {tiers.map((t) => (
          <SelectItem key={t} value={String(t)}>
            Tier {t}
          </SelectItem>
        ))}
      </Facet>
      <Facet label="JFrog product" value={view.product} any="All JFrog products" onChange={(v) => onChange({ ...view, product: v })} className="sm:w-52">
        {products.map((p) => (
          <SelectItem key={p.slug} value={p.slug}>
            {p.name}
          </SelectItem>
        ))}
      </Facet>
      {/* The categories their seeded products are in (the overview serves them). */}
      <Facet label="Category" value={view.category} any="All categories" onChange={(v) => onChange({ ...view, category: v })} className="sm:w-60">
        {categories.map((c) => (
          <SelectItem key={c.slug} value={c.slug}>
            {c.name}
          </SelectItem>
        ))}
      </Facet>
      {/* The table's column heads sort it from `xl`; below, where the rows reflow, these do. */}
      <div className="flex min-w-0 flex-1 basis-64 items-center gap-2 sm:flex-none xl:hidden">
        <Facet label="Sort by" value={view.sort} onChange={(v) => v && onChange({ ...view, sort: v as SortKey, dir: FIRST_DIR[v as SortKey] })} className="sm:w-44">
          {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => (
            <SelectItem key={k} value={k}>
              {k === "name" ? "Sort by name" : `Sort by ${SORT_LABEL[k].toLowerCase()}`}
            </SelectItem>
          ))}
        </Facet>
        <Button
          variant="outline"
          aria-label={`Order: ${orderWords(view.sort, view.dir)}`}
          onClick={() => onChange({ ...view, dir: view.dir === "asc" ? "desc" : "asc" })}
          className="shrink-0 px-3 font-normal"
        >
          {view.dir === "asc" ? <ArrowUpIcon /> : <ArrowDownIcon />}
          <span className="first-letter:uppercase">{orderWords(view.sort, view.dir)}</span>
        </Button>
      </div>
      {hasFilters(view) ? (
        <Button variant="ghost" size="sm" onClick={() => onChange(clearFilters(view))}>
          <XIcon />
          Clear filters
        </Button>
      ) : null}
      <p className="hidden text-meta text-muted-foreground sm:block md:ml-2">Narrows the landscape and the table below.</p>
    </div>
  );
}

/**
 * Said once under the table: on a young deployment every trend reads "no baseline yet"
 * because collection began under the trend's weeks ago, not for want of signals; when the first
 * can read. Nothing on an older deployment.
 */
function YoungNote({ today }: { today: string }) {
  const rules = useRules();
  const since = rules.collected_since;
  if (!since || !youngCollection(since, today, rules.trend)) return null;
  return (
    <p className="mt-3 max-w-[72ch] text-meta text-pretty text-muted-foreground">
      {`Momentum trends compare ${rules.trend.weeks} collected weeks. FrogEye has collected since ${dayMonth(since)}, so every trend reads “no baseline yet” until ${plainDay(firstBaselineDay(since, rules.trend), "date")} at the earliest.`}
    </p>
  );
}

const ORDER_WORDS: Record<SortKey, string> = { threat: "threat", momentum: "momentum", open_roles: "open roles", name: "name" };

function orderLine(view: View): string {
  const reverse = view.dir !== FIRST_DIR[view.sort];
  if (view.sort === "name") return reverse ? "by name, Z to A" : "by name";
  return reverse ? `lowest ${ORDER_WORDS[view.sort]} first` : `highest ${ORDER_WORDS[view.sort]} first`;
}

function PageSkeleton() {
  return (
    <div role="status" aria-label="Loading competitors" className="flex flex-col gap-10">
      <div className="flex flex-col gap-3 border-t border-foreground pt-3">
        <Skeleton className="h-4 w-24" />
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="flex items-center gap-5 border-b py-3">
            <Skeleton className="h-6 w-28 rounded-full" />
            <Skeleton className={cn("h-5", i % 2 ? "w-2/5" : "w-3/5")} />
          </div>
        ))}
      </div>
      <Skeleton className="h-80 w-full" />
      <div className="flex flex-col">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="flex items-center gap-8 border-b py-4">
            <Skeleton className="h-9 w-40" />
            <Skeleton className="h-5 w-20" />
            <Skeleton className="h-6 w-24" />
            <Skeleton className="ml-auto h-4 w-56" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function CompetitorsPage() {
  const query = useCompetitorOverview();
  // The rules its trend words and formulas quote, read with the overview.
  const reference = useIntelReference();
  const retry = () => {
    if (query.isError) void query.refetch();
    if (reference.isError) void reference.refetch();
  };
  const [params, setParams] = useSearchParams();
  const today = localToday(useNow(60_000));
  const tableHead = useId();
  const setView = useCallback((next: View) => setParams((current) => viewParams(next, current), { replace: true }), [setParams]);

  const all = useMemo(() => query.data ?? [], [query.data]);
  // A tier or product the data cannot match reads as "All", and leaves the address.
  const view = useMemo(() => (query.data ? knownView(parseView(params), query.data) : parseView(params)), [params, query.data]);
  useEffect(() => {
    if (!query.data) return;
    const asked = parseView(params);
    const known = knownView(asked, query.data);
    if (known !== asked) setView(known);
  }, [params, query.data, setView]);
  const filtered = useMemo(() => applyFilters(all, view), [all, view]);
  const trend = reference.data?.trend;
  const sorted = useMemo(() => (trend ? sortRows(filtered, view.sort, view.dir, trend) : filtered), [filtered, view.sort, view.dir, trend]);
  const tiers = useMemo(() => tiersOf(all), [all]);

  const onSort = (column: SortKey) =>
    setView(view.sort === column ? { ...view, dir: view.dir === "asc" ? "desc" : "asc" } : { ...view, sort: column, dir: FIRST_DIR[column] });
  const clear = () => setView(clearFilters(view));

  let body: ReactNode;
  if (query.isPending || reference.isPending) {
    body = <PageSkeleton />;
  } else if ((query.isError && !query.data) || (reference.isError && !reference.data)) {
    // a failed background refetch keeps what is on screen; only a read that never loaded is the page's error.
    body = (
      <EmptyState
        tone="error"
        title="The competitors didn't load."
        hint="FrogEye's API didn't answer. Check your connection, then try again."
        action={
          <Button variant="outline" size="sm" onClick={retry} loading={query.isRefetching || reference.isRefetching}>
            <RotateCwIcon />
            Try again
          </Button>
        }
      />
    );
  } else if (all.length === 0) {
    body = <EmptyState title="No competitors are tracked yet" hint="Competitors come from FrogEye's seed; once it is loaded, each daily run fills in their signals." />;
  } else {
    const filters = hasFilters(view);
    body = (
      <RulesContext value={reference.data}>
        <div className="flex flex-col gap-10">
          <ThisWeek rows={all} today={today} />
          <div className="flex flex-col gap-6">
            <Filters rows={all} view={view} onChange={setView} />
            {/* Nothing left to draw: the table below says so once, with the way back. */}
            {filtered.length > 0 ? <LandscapeChart rows={filtered} tiers={tiers} filtered={filters} today={today} /> : null}
          </div>
          <section aria-labelledby={tableHead} className="flex flex-col">
            <SectionHead
              id={tableHead}
              title="All competitors"
              meta={`${filters ? `${filtered.length} of ${all.length}` : all.length} · ${orderLine(view)}`}
              className="pb-1"
            />
            {sorted.length === 0 ? (
              <EmptyState
                className="mt-3"
                title="No competitor matches these filters"
                hint="Try another tier, JFrog product or category, or clear the filters to see every competitor."
                action={
                  <Button variant="outline" size="sm" onClick={clear}>
                    <XIcon />
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <CompetitorTable rows={sorted} view={view} onSort={onSort} labelledBy={tableHead} today={today} />
            )}
            <YoungNote today={today} />
          </section>
        </div>
      </RulesContext>
    );
  }

  return (
    <>
      <PageHeader title="Competitors" description={query.data && all.length > 0 ? leadSentence(all) : "Who matters to JFrog, and who is moving."} />
      {body}
    </>
  );
}
