import { ChevronRightIcon, RotateCwIcon } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionHead } from "@/components/layout/SectionHead";
import { CompanyChips } from "@/components/signal/CompanyChips";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { plural } from "@/lib/format";
import { localToday } from "@/lib/days";
import { RulesContext, useIntelReference, useRules, youngCollection } from "@/lib/intelReference";
import { useNow } from "@/lib/time";
import { cn } from "@/lib/utils";
import { dayMonth } from "@/features/intel/words";
import { PressureChip } from "./PressureChip";
import { byPressure, listLead, useProducts, type ProductSummary } from "./useProducts";

/*
 * `/products`: JFrog's products, each with what it does, the competitive pressure on it over the
 * trend's weeks and the competitors pressing it hardest, the same for every persona, from
 * one read (`GET /products`, with the rules it quotes from GET /intel-reference). The so-what
 * leads: the products under rising pressure come first, and one sentence counts them; while
 * FrogEye has collected for less than the trend's weeks, it says the pressure has no baseline yet. A ledger, not a grid of cards: each row is ONE link to the product's
 * deep dive (its serif name's cover spans the row, as on the Explorer's rows), its competitor
 * chips above that cover, opening their profiles. No JFrog company figure appears.
 */

/** From `xl` (where the sidebar leaves room) a ledger of three columns; below, each row stacks. */
const DESKTOP = "xl:grid-cols-[minmax(0,1fr)_16.5rem_15rem]";
/** The row's cover: the name's ::after spans the row, so the whole row opens the product's page. */
const COVER = cn(
  "cursor-pointer decoration-1 underline-offset-[0.2em] group-hover:underline",
  "after:absolute after:inset-y-0 after:-inset-x-3 after:content-['']",
  "focus-visible:outline-none focus-visible:after:rounded-md focus-visible:after:ring-2 focus-visible:after:ring-ring",
);

function ProductRow({ row, young }: { row: ProductSummary; young: boolean }) {
  const titleId = useId();
  return (
    <article aria-labelledby={titleId} className={cn("group relative grid gap-x-8 gap-y-2.5 py-4", DESKTOP)}>
      <div className="flex min-w-0 flex-col gap-1 pr-7 xl:pr-0">
        <h3 id={titleId} className="font-headline text-title text-balance">
          <Link to={`/products/${row.slug}`} className={COVER}>
            {row.name}
          </Link>
        </h3>
        {row.description ? <p className="line-clamp-2 max-w-[65ch] text-body text-pretty text-muted-foreground">{row.description}</p> : null}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 xl:contents">
        <div className="xl:pt-1.5">
          <PressureChip product={row} collectedSince={useRules().collected_since} young={young} />
        </div>
        <div className="min-w-0 pr-7 xl:pt-1">
          {row.top_competitors.length > 0 ? (
            <CompanyChips slugs={row.top_competitors} max={3} className="relative z-10" />
          ) : (
            <span className="text-meta text-muted-foreground">No competitor mapped yet</span>
          )}
        </div>
      </div>
      {/* At rest the chevron says the row opens; it nudges and darkens on hover, and a click on it reaches the cover. */}
      <ChevronRightIcon
        aria-hidden
        className="pointer-events-none absolute top-[1.4rem] right-0 size-4 text-muted-foreground transition-[color,translate] duration-150 ease-out-quart group-hover:translate-x-0.5 group-hover:text-foreground"
      />
    </article>
  );
}

function ListHead({ id, count, young }: { id: string; count: number; young: boolean }) {
  const rules = useRules();
  const since = rules.collected_since;
  const span = young && since ? `since ${dayMonth(since)}` : plural(rules.trend.weeks, "week");
  // The one ink-ruled head, its column words aligned over the ledger's columns.
  return (
    <SectionHead id={id} title="All products" meta={`${count} · most pressed first`} className={cn("grid items-baseline gap-x-8 pb-1", DESKTOP)}>
      <span className="text-meta text-muted-foreground max-xl:hidden">{`Pressure, ${span}`}</span>
      <span className="text-meta text-muted-foreground max-xl:hidden">Top competitors</span>
    </SectionHead>
  );
}

function ListSkeleton() {
  return (
    <div role="status" aria-label="Loading the products" className="flex flex-col">
      <Skeleton className="mb-3 h-px w-full" />
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className={cn("grid gap-x-8 gap-y-2 border-b py-4", DESKTOP)}>
          <div className="flex flex-col gap-2">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-4 w-full max-w-[34rem]" />
          </div>
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-6 w-44" />
        </div>
      ))}
    </div>
  );
}

const DESCRIPTION = "Who is pressing each JFrog product, how hard, and what to do about it.";

/** How pressure and the top competitors are computed, and on a young deployment why no trend shows yet. */
function Footnote({ young }: { young: boolean }) {
  const rules = useRules();
  const t = rules.trend;
  const since = rules.collected_since;
  const why = young && since ? ` FrogEye has collected since ${dayMonth(since)}, under the ${t.weeks} weeks the trend compares, so no product has a pressure trend yet.` : "";
  return (
    <p className="mt-4 max-w-[72ch] border-t pt-3 text-meta text-pretty text-muted-foreground">
      {`Pressure is the severity-weighted threat that public signals' analyses name for each product: the last ${plural(t.recent_weeks, "week")} against the ${t.baseline_weeks} before.${why} Top competitors are those with the most evidence against it in ${plural(rules.overlap.lookback_days, "day")}. No JFrog company figure appears here.`}
    </p>
  );
}

export function ProductsPage() {
  const headId = useId();
  const today = localToday(useNow(60_000));
  const query = useProducts();
  // The rules its words quote and the portfolio's order, read with the list.
  const reference = useIntelReference();

  if (query.isPending || reference.isPending) {
    return (
      <>
        <PageHeader title="Products" description={DESCRIPTION} />
        <ListSkeleton />
      </>
    );
  }
  // a failed background refetch keeps what is on screen; only a read that never loaded is the page's error.
  if ((query.isError && !query.data) || (reference.isError && !reference.data)) {
    return (
      <>
        <PageHeader title="Products" description={DESCRIPTION} />
        <EmptyState
          tone="error"
          title="The products didn't load."
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
      </>
    );
  }
  const rules = reference.data!;
  const rows = byPressure(query.data!, rules.jfrog_products);
  // a young deployment is the deployment's (the collected-since day); a product's own
  // missing baseline (no threat in its baseline weeks) is its chip's word.
  const young = youngCollection(rules.collected_since, today, rules.trend);
  return (
    <RulesContext value={rules}>
      <PageHeader title="Products" description={listLead(rows, rules, young)} />
      <section aria-labelledby={headId} className="flex min-w-0 flex-col wrap-anywhere">
        <ListHead id={headId} count={rows.length} young={young} />
        <ul aria-label="JFrog products" className="flex flex-col divide-y">
          {rows.map((row) => (
            <li key={row.slug}>
              <ProductRow row={row} young={young} />
            </li>
          ))}
        </ul>
        <Footnote young={young} />
      </section>
    </RulesContext>
  );
}
