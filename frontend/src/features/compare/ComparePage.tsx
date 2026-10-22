import { ChevronRightIcon, RotateCwIcon, XIcon } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, type ReactNode } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionHead } from "@/components/layout/SectionHead";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { threatFormula } from "@/features/competitors/profileWords";
import { HowComputed } from "@/features/intel/grammar";
import { ThreatMark } from "@/features/intel/ThreatChip";
import { dayMonth } from "@/features/intel/words";
import { PressureChip } from "@/features/products/PressureChip";
import { pressureFormula } from "@/features/products/useProducts";
import { localToday } from "@/lib/days";
import { plural } from "@/lib/format";
import { RulesContext, useIntelReference, useRules, youngCollection } from "@/lib/intelReference";
import { useNow } from "@/lib/time";
import { cn } from "@/lib/utils";
import { CellDetail, type OpenCell } from "./CellDetail";
import { CellThreat, CompareMatrix } from "./CompareMatrix";
import {
  applyFilters,
  categoriesOf,
  cellFormula,
  cellIndex,
  cellKey,
  hasFilters,
  knownView,
  leadSentence,
  levelsNote,
  parseView,
  productsLine,
  tiersOf,
  useCompare,
  viewParams,
  type Compare,
  type CompareCell,
  type CompareCompetitor,
  type View,
} from "./useCompare";

/*
 * `/compare`: how JFrog compares to its competitors, on one screen. JFrog's products are the rows
 * (the products list's order), the competitors with seeded products the columns (by tier, then
 * threat), and each cell the pairing's competing products and the evidence-based threat to the
 * product, from one read (`GET /compare`). Filters scope the columns and live in the address,
 * as on the competitor list; a cell opens its pairing in a sheet (`?cell=`), so a pairing can be
 * shared. Phones pick one product and list its competitors instead of a matrix.
 */

const ALL = "all";
const CELL_PARAM = "cell";
const PRODUCT_PARAM = "product";

function Facet({ label, value, any, onChange, className, children }: { label: string; value: string | undefined; any: string; onChange: (v: string | undefined) => void; className?: string; children: ReactNode }) {
  const set = value !== undefined;
  return (
    <Select value={set ? value : ALL} onValueChange={(v) => onChange(v === ALL ? undefined : v)}>
      <SelectTrigger aria-label={label} className={cn("min-w-0 flex-1 basis-40 sm:flex-none", className, !set && "text-muted-foreground")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent position="popper" className="max-h-80">
        <SelectItem value={ALL}>{any}</SelectItem>
        {children}
      </SelectContent>
    </Select>
  );
}

function Filters({ cols, view, onChange }: { cols: CompareCompetitor[]; view: View; onChange: (next: View) => void }) {
  return (
    <div role="search" aria-label="Filter competitors" className="flex flex-wrap items-center gap-2">
      <Facet label="Tier" value={view.tier === undefined ? undefined : String(view.tier)} any="All tiers" onChange={(v) => onChange({ ...view, tier: v ? Number(v) : undefined })} className="sm:w-36">
        {tiersOf(cols).map((t) => (
          <SelectItem key={t} value={String(t)}>
            Tier {t}
          </SelectItem>
        ))}
      </Facet>
      <Facet label="Category" value={view.category} any="All categories" onChange={(v) => onChange({ ...view, category: v })} className="sm:w-60">
        {categoriesOf(cols).map((c) => (
          <SelectItem key={c.slug} value={c.slug}>
            {c.name}
          </SelectItem>
        ))}
      </Facet>
      {hasFilters(view) ? (
        <Button variant="ghost" size="sm" onClick={() => onChange({})}>
          <XIcon />
          Clear filters
        </Button>
      ) : null}
      <p className="hidden text-meta text-muted-foreground sm:block md:ml-2">Narrows the competitors compared.</p>
    </div>
  );
}

/**
 * Phones: one JFrog product at a time (its pressure beside it), then each competitor with a
 * product mapped to it, as a row that opens the same sheet; the rest are named in one line.
 */
function ProductList({ data, cols, cells, onOpen, young }: { data: Compare; cols: CompareCompetitor[]; cells: Map<string, CompareCell>; onOpen: (key: string) => void; young: boolean }) {
  const [params, setParams] = useSearchParams();
  const since = useRules().collected_since;
  const product = data.products.find((p) => p.slug === params.get(PRODUCT_PARAM)) ?? data.products[0]!;
  const rows = cols.flatMap((c) => {
    const cell = cells.get(cellKey(product.slug, c.slug));
    return cell ? [{ c, cell }] : [];
  });
  const without = cols.filter((c) => !cells.has(cellKey(product.slug, c.slug)));
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Select
          value={product.slug}
          onValueChange={(v) =>
            setParams(
              (current) => {
                const next = new URLSearchParams(current);
                next.set(PRODUCT_PARAM, v);
                return next;
              },
              { replace: true },
            )
          }
        >
          <SelectTrigger aria-label="JFrog product" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent position="popper" className="max-h-80">
            {data.products.map((p) => (
              <SelectItem key={p.slug} value={p.slug}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <PressureChip product={product} collectedSince={since} young={young} />
      </div>
      {rows.length === 0 ? (
        <p className="text-body text-pretty text-muted-foreground">{`No competitor in view has a product mapped to ${product.name}.`}</p>
      ) : (
        <ul aria-label={`Competitors of ${product.name}`} className="flex flex-col divide-y border-t">
          {rows.map(({ c, cell }) => (
            <li key={c.slug}>
              <button
                type="button"
                data-cell={cellKey(product.slug, c.slug)}
                onClick={() => onOpen(cellKey(product.slug, c.slug))}
                className="group flex w-full cursor-pointer items-start gap-3 py-3.5 text-left transition-colors duration-150 ease-out-quart hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <span className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-body font-semibold text-foreground">{c.name}</span>
                    <span className="text-meta text-muted-foreground">{`Tier ${c.tier}`}</span>
                  </span>
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <CellThreat cell={cell} />
                    {cell.last_move ? <span className="text-meta text-muted-foreground">{`Latest move ${dayMonth(cell.last_move.day)}`}</span> : null}
                  </span>
                  <span className="text-meta text-pretty text-muted-foreground">{productsLine(cell)}</span>
                </span>
                <ChevronRightIcon aria-hidden className="mt-1 size-4 shrink-0 text-muted-foreground transition-transform duration-150 ease-out-quart group-hover:translate-x-0.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {without.length > 0 ? <p className="text-meta text-pretty text-muted-foreground">{`No product mapped to ${product.name}: ${without.map((c) => c.name).join(", ")}.`}</p> : null}
    </div>
  );
}

function PageSkeleton() {
  return (
    <div role="status" aria-label="Loading the comparison" className="flex flex-col gap-6">
      <div className="flex gap-2">
        <Skeleton className="h-9 w-36" />
        <Skeleton className="h-9 w-60" />
      </div>
      <div className="flex flex-col border-t border-foreground pt-3">
        <Skeleton className="mb-4 h-4 w-40" />
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex gap-3 border-b py-3">
            <Skeleton className="h-10 w-48" />
            {Array.from({ length: 6 }, (_, j) => (
              <Skeleton key={j} className="hidden h-10 w-32 md:block" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** "How this is computed" for the three kinds of value on the page, from the API's rules. */
function Method() {
  const rules = useRules();
  return (
    <HowComputed>
      <p>{cellFormula(rules)}</p>
      <p>{`A row's pressure is the product's, as the products list shows it. ${pressureFormula(rules)}`}</p>
      <p>{`${levelsNote} ${threatFormula(rules)}`}</p>
    </HowComputed>
  );
}

const DESCRIPTION = "How JFrog's products compare with each competitor's, on the evidence of public signals.";

export function ComparePage() {
  const query = useCompare();
  const reference = useIntelReference();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const headId = useId();
  const today = localToday(useNow(60_000));
  const setView = useCallback((next: View) => setParams((current) => viewParams(next, current), { replace: true }), [setParams]);

  const data = query.data;
  const view = useMemo(() => (data ? knownView(parseView(params), data.competitors) : parseView(params)), [params, data]);
  useEffect(() => {
    if (!data) return;
    const asked = parseView(params);
    const known = knownView(asked, data.competitors);
    if (known !== asked) setView(known);
  }, [params, data, setView]);
  const cols = useMemo(() => (data ? applyFilters(data.competitors, view) : []), [data, view]);
  const cells = useMemo(() => cellIndex(data?.cells ?? []), [data]);

  // The open pairing: `?cell=` names it; opening pushes an entry, so Back closes the sheet.
  const openKey = params.get(CELL_PARAM);
  const open: OpenCell | null = useMemo(() => {
    if (!data || !openKey) return null;
    const cell = cells.get(openKey);
    const product = data.products.find((p) => p.slug === cell?.product_slug);
    const competitor = data.competitors.find((c) => c.slug === cell?.competitor_slug);
    return cell && product && competitor ? { cell, product, competitor } : null;
  }, [data, openKey, cells]);
  const openCell = (key: string) => {
    const next = new URLSearchParams(location.search);
    next.set(CELL_PARAM, key);
    navigate({ pathname: location.pathname, search: `?${next}` }, { state: { cell: true } });
  };
  const closeCell = () => {
    if ((location.state as { cell?: boolean } | null)?.cell) {
      navigate(-1);
      return;
    }
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete(CELL_PARAM);
        return next;
      },
      { replace: true },
    );
  };

  let body: ReactNode;
  if (query.isPending || reference.isPending) {
    body = <PageSkeleton />;
  } else if ((query.isError && !query.data) || (reference.isError && !reference.data)) {
    body = (
      <EmptyState
        tone="error"
        title="The comparison didn't load."
        hint="FrogEye's API didn't answer. Check your connection, then try again."
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              if (query.isError) void query.refetch();
              if (reference.isError) void reference.refetch();
            }}
            loading={query.isRefetching || reference.isRefetching}
          >
            <RotateCwIcon />
            Try again
          </Button>
        }
      />
    );
  } else if (data!.competitors.length === 0) {
    body = <EmptyState title="No competitor product is mapped yet" hint="The matrix fills from FrogEye's seed of competitor products; once it is loaded, each daily run adds the evidence." />;
  } else {
    const rules = reference.data!;
    const young = youngCollection(rules.collected_since, today, rules.trend);
    const filtered = hasFilters(view);
    const all = data!;
    body = (
      <RulesContext value={rules}>
        <div className="flex flex-col gap-6">
          <Filters cols={all.competitors} view={view} onChange={setView} />
          <section aria-labelledby={headId} className="flex min-w-0 flex-col">
            <SectionHead
              id={headId}
              title="JFrog vs competitors"
              meta={`${plural(all.products.length, "product")} × ${filtered ? `${cols.length} of ${all.competitors.length}` : all.competitors.length} competitors · threat on ${plural(all.lookback_days, "day")} of public signals, to ${dayMonth(all.as_of)}`}
              className="pb-1"
            />
            {cols.length === 0 ? (
              <EmptyState
                className="mt-3"
                title="No competitor matches these filters"
                hint="Try another tier or category, or clear the filters to compare every competitor."
                action={
                  <Button variant="outline" size="sm" onClick={() => setView({})}>
                    <XIcon />
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <>
                <div className="max-md:hidden">
                  <CompareMatrix products={all.products} competitors={cols} cells={cells} young={young} onOpen={openCell} labelledBy={headId} />
                </div>
                <div className="pt-3 md:hidden">
                  <ProductList data={all} cols={cols} cells={cells} onOpen={openCell} young={young} />
                  <div className="mt-5 flex flex-col gap-2 border-t pt-3">
                    <p className="text-meta text-muted-foreground">Overall threat to JFrog</p>
                    <ul className="flex flex-col gap-2">
                      {cols.map((c) => (
                        <li key={c.slug} className="flex items-center justify-between gap-3">
                          <span className="text-body text-foreground">{c.name}</span>
                          <ThreatMark threat={c.threat} />
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </>
            )}
            <div className="mt-4 flex flex-col gap-2 border-t pt-3">
              <p className="max-w-[72ch] text-meta text-pretty text-muted-foreground">
                Each cell is a JFrog product against one competitor: their products mapped to it, the threat their public signals name for it and the day of the latest.<span className="max-md:hidden"> A blank cell means no product of theirs is mapped to it.</span>
              </p>
              <Method />
            </div>
          </section>
        </div>
        <CellDetail open={open} asOf={all.as_of} onClose={closeCell} />
      </RulesContext>
    );
  }

  return (
    <div data-wide>
      <PageHeader title="Compare" description={data && data.competitors.length > 0 ? leadSentence(data) : DESCRIPTION} />
      {body}
    </div>
  );
}
