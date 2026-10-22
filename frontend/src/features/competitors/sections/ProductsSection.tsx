import { ChevronRightIcon } from "lucide-react";
import { Link } from "react-router";
import { INLINE_LINK } from "@/components/common/links";
import { ProductLink } from "@/components/common/ProductLink";
import { analysisHref } from "@/components/signal/param";
import { ExternalLink } from "@/components/signal/SourceLink";
import { Cites } from "@/features/analysis/CitedSources";
import { plural } from "@/lib/format";
import { useRules } from "@/lib/intelReference";
import { productRank } from "@/lib/products";
import { cn } from "@/lib/utils";
import { HowComputed, QuietNote, ProfileSection } from "@/features/intel/grammar";
import { ThreatChip } from "@/features/intel/ThreatChip";
import { OVERLAP_CHIP, OVERLAP_WORD } from "@/features/intel/levels";
import { overlapFormula } from "../profileWords";
import { notesFor, sectionOf, type CompetitorProfile, type ProductOverlap } from "../useCompetitorProfile";

/*
 * Products vs JFrog: each of their seeded products against the JFrog products it competes with,
 * the overlap the evidence shows (a word and the risk chip's mark, never colour alone: high in
 * the threat tone with an up triangle, medium a diamond, low a down triangle, "No recent
 * evidence" on a dashed edge), the analyses it is built from, and the brief's edge for them
 * beside each JFrog product it names. From `md` a table; below, each row reflows into a block
 * with its cells labelled in place, so a phone never scrolls it sideways.
 */

const RANK = { high: 0, medium: 1, low: 2, none: 3 } as const;

/** "3 of 4 products overlap with JFrog on recent evidence, most of all Nexus Repository (evidence 9)." */
function summary(products: ProductOverlap[], days: number): string {
  const live = products.filter((p) => p.level !== "none");
  if (live.length === 0) return `None of its ${plural(products.length, "product")} shows overlap with JFrog in the last ${plural(days, "day")} of evidence.`;
  const top = live.reduce((a, b) => (b.evidence > a.evidence ? b : a));
  const of = live.length === products.length ? (products.length === 1 ? "Its one product" : `All ${products.length} products`) : `${live.length} of ${products.length} products`;
  return `${of} ${live.length === 1 ? "overlaps" : "overlap"} with JFrog on recent evidence, most of all ${top.product} (evidence ${top.evidence}).`;
}

const HEAD = "h-9 px-3 text-left align-bottom text-meta font-medium whitespace-nowrap text-muted-foreground first:pl-0";
const CELL = "px-3 py-3.5 align-top first:pl-0 max-md:block max-md:p-0";
const LABEL = "mb-1 block text-meta text-muted-foreground md:hidden";

function Analyses({ product, titles }: { product: ProductOverlap; titles: Map<number, string> }) {
  const days = useRules().overlap.lookback_days;
  const n = product.signal_ids.length;
  if (n === 0) return <span className="text-meta text-muted-foreground">{`No analyses in ${plural(days, "day")}`}</span>;
  return (
    <details className="group/an">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-sm text-meta text-muted-foreground transition-colors duration-150 select-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
        {`evidence ${product.evidence} · ${plural(n, "analysis", "analyses")}`}
        <ChevronRightIcon aria-hidden className="size-3.5 transition-transform duration-150 group-open/an:rotate-90" />
      </summary>
      <ul className="mt-1.5 flex flex-col gap-1">
        {product.signal_ids.map((id) => {
          const title = titles.get(id);
          return (
            <li key={id}>
              {/* An analysis the page cannot title (not on its timeline) reads as unavailable, as hidden citations do. */}
              {title ? (
                <Link to={analysisHref(id)} className={cn(INLINE_LINK, "text-meta text-foreground")}>
                  {title}
                </Link>
              ) : (
                <span className="text-meta text-muted-foreground">Signal not available</span>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function ProductRow({ product, titles }: { product: ProductOverlap; titles: Map<number, string> }) {
  const order = useRules().jfrog_products;
  const counterparts = [...product.jfrog_products].sort((a, b) => productRank(a, order) - productRank(b, order));
  return (
    <tr role="row" className="border-b last:border-b-0 max-md:grid max-md:gap-3 max-md:py-4">
      <td role="cell" className={CELL}>
        <span className="flex flex-col gap-0.5">
          {product.url ? (
            <ExternalLink href={product.url} className="w-fit text-body font-semibold text-foreground">
              <span data-product>{product.product}</span>
            </ExternalLink>
          ) : (
            <span data-product className="text-body font-semibold text-foreground">
              {product.product}
            </span>
          )}
          <span className="text-meta text-muted-foreground">{product.category}</span>
        </span>
      </td>
      <td role="cell" className={CELL}>
        <span className={LABEL}>Competes with</span>
        <ul className="flex flex-col gap-2">
          {counterparts.map((name) => {
            const edge = product.edges.find((e) => e.jfrog_product === name);
            return (
              <li key={name} className="flex flex-col gap-0.5">
                {/* A JFrog product opens its own deep dive, as everywhere else. */}
                <ProductLink name={name} className="w-fit text-body font-medium text-foreground" linkClassName={INLINE_LINK} />
                {edge ? (
                  <p className="max-w-[60ch] text-body text-pretty text-muted-foreground">
                    {edge.edge.text}
                    <Cites cites={edge.edge.cites} />
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      </td>
      <td role="cell" className={CELL}>
        <span className={LABEL}>Overlap with JFrog</span>
        <span className="flex flex-col items-start gap-1.5">
          <ThreatChip level={OVERLAP_CHIP[product.level]} data-overlap={product.level}>
            {OVERLAP_WORD[product.level]}
          </ThreatChip>
          <Analyses product={product} titles={titles} />
        </span>
      </td>
    </tr>
  );
}

export function ProductsSection({ profile }: { profile: CompetitorProfile }) {
  const rules = useRules();
  const days = rules.overlap.lookback_days;
  const products = [...profile.products].sort((a, b) => RANK[a.level] - RANK[b.level] || b.evidence - a.evidence);
  const titles = new Map(profile.timeline.map((e) => [e.signal_id, e.title]));
  const note = notesFor(profile, "products")[0];
  return (
    <ProfileSection section={sectionOf("products")} meta={`Overlap from ${plural(days, "day")} of public signals`}>
      {products.length === 0 ? (
        <QuietNote note={note ?? "No products of this competitor are mapped to JFrog products"} hint="Products come from FrogEye's seed, each with a public source and the JFrog products it competes with." />
      ) : (
        <>
          <p className="max-w-[72ch] text-body text-pretty text-muted-foreground">{summary(products, days)}</p>
          <table role="table" aria-label="Products vs JFrog" className={cn("w-full table-fixed border-collapse text-body max-md:block")}>
            <colgroup>
              <col className="w-[30%]" />
              <col />
              <col className="w-[13.5rem]" />
            </colgroup>
            <thead role="rowgroup" className="border-b max-md:hidden">
              <tr role="row">
                <th role="columnheader" className={HEAD}>
                  Their product
                </th>
                <th role="columnheader" className={HEAD}>
                  Competes with
                </th>
                <th role="columnheader" className={HEAD}>
                  Overlap with JFrog
                </th>
              </tr>
            </thead>
            <tbody role="rowgroup" className="max-md:block">
              {products.map((p) => (
                <ProductRow key={p.product} product={p} titles={titles} />
              ))}
            </tbody>
          </table>
          <HowComputed>
            <p>{overlapFormula(rules)}</p>
          </HowComputed>
        </>
      )}
    </ProfileSection>
  );
}
