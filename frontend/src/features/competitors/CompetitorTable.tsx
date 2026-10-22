import { ArrowDownIcon, ArrowUpIcon, ChevronRightIcon, ChevronsUpDownIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { dayDate } from "@/components/charts/scales";
import { Sparkline } from "@/components/charts/Sparkline";
import { INLINE_LINK } from "@/components/common/links";
import { Num } from "@/components/common/Num";
import { analysisHref } from "@/components/signal/param";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { plural } from "@/lib/format";
import { ProductLink } from "@/components/common/ProductLink";
import { daysBetween } from "@/lib/days";
import { ratio, trendWord, useRules } from "@/lib/intelReference";
import { productSlug } from "@/lib/products";
import { dayKey, plainDay } from "@/lib/time";
import { cn } from "@/lib/utils";
import { keepDates, weekOf } from "@/features/intel/words";
import { ThreatMark } from "@/features/intel/ThreatChip";
import { boardFirstRead, changeIn, momentumLabel } from "./profileWords";
import {
  boardState,
  formatMomentum,
  fundingOf,
  momentumLevel,
  NO_BOARD,
  orderedProducts,
  PENDING,
  RELATIONSHIP,
  SORT_LABEL,
  type CompetitorSummary,
  type SortKey,
  type View,
} from "./useCompetitorIntel";

/*
 * Below `xl` the table, its row groups, rows and cells are laid out as blocks and grids, which
 * drops their implicit table roles in some browsers (Safari); every element states its role, so a
 * screen reader still reads a table there.
 *
 * Every competitor in one table: who it is and what it competes with, the threat to JFrog, its
 * 13-week momentum, open roles, money raised and its latest move. The whole row opens the
 * profile (the name link's ::after covers the row, as on the run tables); the threat's drivers,
 * the sparkline and the latest move sit above that cover. A value that was never measured is
 * said in words ("No public Greenhouse, Lever or Ashby board found", "Insufficient data"), never shown as 0.
 *
 * From `xl` it is a table; below, each row reflows into a ruled block (the cells become a grid,
 * labelled in place), so a laptop or a phone reads every column without scrolling sideways.
 */

/** A cell's label where the row reflows (below `xl`); the column head says it in the table. */
function Label({ children }: { children: ReactNode }) {
  return <span className="mb-1 block text-meta text-muted-foreground xl:hidden">{children}</span>;
}


/**
 * The momentum level leads (the landscape's y, what the column sorts by): the mean weekly
 * importance of the trend's recent weeks; the trend is the secondary line ("1.6× · rising", or
 * "no baseline yet" for the first 13 weeks).
 */
function Momentum({ row }: { row: CompetitorSummary }) {
  const rules = useRules();
  const trend = row.threat.momentum_trend;
  const level = momentumLevel(row, rules.trend);
  return (
    <div className="flex flex-col items-start gap-1">
      <Sparkline
        label={momentumLabel(row.momentum, rules.trend.weeks)}
        values={row.momentum ? row.momentum.map((w) => ({ x: dayDate(w.week_start), y: w.value })) : null}
        xFormat={weekOf}
        className="z-10"
      />
      {row.momentum && level !== null ? (
        <span className="flex flex-col text-meta whitespace-nowrap text-muted-foreground">
          <span className="font-semibold text-foreground">{`${formatMomentum(level)} a week`}</span>
          <span>{trend === null ? "no baseline yet" : `${ratio(trend)} · ${trendWord(trend, rules.trend)}`}</span>
        </span>
      ) : null}
    </div>
  );
}

/** "board read today", "board read 18 Oct": when its board was last listed in full. */
const boardRead = (asOf: string, today: string) => (daysBetween(dayKey(asOf), today) <= 0 ? "board read today" : `board read ${plainDay(dayKey(asOf), "dayMonth")}`);

/** "No public … board found (checked 12 Oct 2026)" as its two lines: the why, and "checked 12 Oct 2026". */
function splitChecked(note: string): [string, string | null] {
  const m = /^(.*) \((checked [^)]+)\)$/.exec(note);
  return m ? [m[1]!, m[2]!] : [note, null];
}

function OpenRoles({ row, today }: { row: CompetitorSummary; today: string }) {
  const days = useRules().threat.hiring_history_days;
  const state = boardState(row);
  if (state !== "measured") {
    // The API's own words for why there is no count (D30); the board state is the fallback. Its
    // discovery check goes on its own line, as a measured board's "board read 18 Oct" does.
    const note = row.open_roles_note ?? (state === "pending" ? PENDING : NO_BOARD);
    const [why, checked] = splitChecked(note);
    return (
      <div className="flex flex-col gap-0.5 text-meta text-pretty text-muted-foreground">
        <span>{keepDates(why)}</span>
        {checked ? <span>{keepDates(checked)}</span> : null}
      </div>
    );
  }
  const delta = row.delta_30d;
  const Arrow = delta && delta > 0 ? ArrowUpIcon : ArrowDownIcon;
  return (
    <div className="flex flex-col gap-0.5">
      <Num value={row.open_roles!} className="font-semibold text-foreground" />
      {/* A board read for under the change's window has no change, never its count as growth. */}
      {delta === null ? (
        <>
          <span className="text-meta text-muted-foreground">{`No ${days}-day change yet`}</span>
          {/* Why: the board is younger than the change's window (the overview's board_since, D30). */}
          {row.board_since ? <span className="text-meta text-muted-foreground">{boardFirstRead(row.board_since, today)}</span> : null}
        </>
      ) : delta === 0 ? (
        <span className="text-meta text-muted-foreground">{`No change in ${plural(days, "day")}`}</span>
      ) : (
        <span className="inline-flex items-center gap-0.5 text-meta whitespace-nowrap text-muted-foreground">
          <Arrow aria-hidden className="size-3 shrink-0" />
          {changeIn(delta, days)}
        </span>
      )}
      {/* The count is its board's last complete listing; a young board's first read already says when. */}
      {row.open_roles_as_of && !(delta === null && row.board_since) ? <span className="text-meta text-muted-foreground">{boardRead(row.open_roles_as_of, today)}</span> : null}
    </div>
  );
}

function Funding({ row }: { row: CompetitorSummary }) {
  if (!row.funding_total) return <span className="text-meta text-muted-foreground">Not reported</span>;
  const { amount, scope, latest } = fundingOf(row.funding_total);
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-medium text-foreground">{amount}</span>
      {/* The newest round in it, so an old total never reads as a current one. */}
      <span className="text-meta text-muted-foreground">{latest ? `${scope} · ${latest}` : scope}</span>
    </div>
  );
}

function LatestMove({ row }: { row: CompetitorSummary }) {
  const days = useRules().threat.lookback_days;
  const move = row.latest_move;
  if (!move) return <span className="text-meta text-muted-foreground">{`No public signals in ${plural(days, "day")}`}</span>;
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <Link
        to={analysisHref(move.signal_id)}
        title={move.title}
        className={cn(INLINE_LINK, "relative z-10 line-clamp-3 w-fit text-body text-foreground")}
      >
        {move.title}
      </Link>
      <span className="text-meta text-muted-foreground">
        {move.lane} · {plainDay(move.day, "dayMonth")}
      </span>
    </div>
  );
}

function Products({ row, product }: { row: CompetitorSummary; product?: string }) {
  const names = orderedProducts(row.jfrog_products, useRules().jfrog_products);
  if (names.length === 0) return <span>No JFrog product mapped</span>;
  return (
    <span>
      <span aria-hidden>vs </span>
      <span className="sr-only">Competes with </span>
      {/* Each JFrog product opens its page, above the row's cover (the name's ::after). */}
      {names.map((name, i) => (
        <span key={name}>
          {i > 0 ? ", " : null}
          <ProductLink name={name} className={cn("relative z-10", productSlug(name) === product && "font-medium text-foreground")} linkClassName={INLINE_LINK} />
        </span>
      ))}
    </span>
  );
}

/** Cells reflow below `xl`: no padding, wrapping text, placed on the row's grid. */
const CELL = "max-xl:block max-xl:p-0 max-xl:whitespace-normal";

function CompetitorRow({ row, product, today }: { row: CompetitorSummary; product?: string; today: string }) {
  return (
    <TableRow
      role="row"
      className={cn(
        "group relative",
        // Reflowed: three columns on a phone; from `md` the latest move joins the figures' line.
        "max-xl:grid max-xl:grid-cols-3 max-xl:gap-x-4 max-xl:gap-y-3.5 max-xl:py-4",
        "md:max-xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,2fr)] md:max-xl:gap-x-6",
      )}
    >
      <TableCell role="cell" data-col="competitor" className={cn(CELL, "whitespace-normal max-xl:col-span-2 max-xl:row-start-1 md:max-xl:col-span-3")}>
        <div className="flex min-w-0 flex-col gap-0.5">
          {/* The link's ::after covers the row, so the whole row opens the profile. */}
          <Link
            to={`/competitors/${row.slug}`}
            data-row-link
            className={cn(
              "w-fit text-body font-semibold text-foreground decoration-1 underline-offset-[0.2em] group-hover:underline",
              "after:absolute after:inset-0 after:rounded-md after:content-['']",
              "focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-ring",
            )}
          >
            {row.name}
          </Link>
          <span className="text-meta text-muted-foreground">
            Tier {row.tier} · {RELATIONSHIP[row.relationship] ?? row.relationship}
          </span>
          <span className="text-meta text-pretty text-muted-foreground">
            <Products row={row} product={product} />
          </span>
        </div>
      </TableCell>
      <TableCell role="cell" data-col="threat" className={cn(CELL, "max-xl:col-start-3 max-xl:row-start-1 max-xl:justify-self-end md:max-xl:col-start-4")}>
        <ThreatMark threat={row.threat} />
      </TableCell>
      <TableCell role="cell" data-col="momentum" className={cn(CELL, "max-xl:col-start-1 max-xl:row-start-2")}>
        <Label>Momentum</Label>
        <Momentum row={row} />
      </TableCell>
      <TableCell role="cell" data-col="open_roles" className={cn(CELL, "whitespace-normal max-xl:col-start-2 max-xl:row-start-2")}>
        <Label>Open roles</Label>
        <OpenRoles row={row} today={today} />
      </TableCell>
      <TableCell role="cell" data-col="funding" className={cn(CELL, "whitespace-normal max-xl:col-start-3 max-xl:row-start-2")}>
        <Label>Total raised</Label>
        <Funding row={row} />
      </TableCell>
      <TableCell
        role="cell"
        data-col="latest_move"
        className={cn(CELL, "whitespace-normal max-xl:col-span-3 max-xl:row-start-3 md:max-xl:col-span-1 md:max-xl:col-start-4 md:max-xl:row-start-2")}
      >
        <Label>Latest move</Label>
        <LatestMove row={row} />
      </TableCell>
      <TableCell role="cell" className="pr-1 pl-0 max-xl:hidden">
        <ChevronRightIcon aria-hidden className="mt-0.5 size-4 text-muted-foreground transition-transform duration-150 ease-out-quart group-hover:translate-x-0.5" />
      </TableCell>
    </TableRow>
  );
}

type SortHeadProps = { column: SortKey; view: View; onSort: (column: SortKey) => void; className?: string };

/** A sortable column head: its order as an arrow and `aria-sort`; the others show they sort, at rest. */
function SortHead({ column, view, onSort, className }: SortHeadProps) {
  const active = view.sort === column;
  const Icon = !active ? ChevronsUpDownIcon : view.dir === "asc" ? ArrowUpIcon : ArrowDownIcon;
  return (
    <TableHead role="columnheader" aria-sort={active ? (view.dir === "asc" ? "ascending" : "descending") : undefined} className={className}>
      <button
        type="button"
        onClick={() => onSort(column)}
        className={cn(
          "-mx-1 inline-flex cursor-pointer items-center gap-1 rounded-xs px-1 transition-colors duration-150 ease-out-quart hover:text-foreground",
          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
          active && "text-foreground",
        )}
      >
        {column === "name" ? "Competitor" : SORT_LABEL[column]}
        <Icon aria-hidden className={cn("size-3.5 shrink-0", !active && "opacity-60")} />
      </button>
    </TableHead>
  );
}

type CompetitorTableProps = {
  rows: CompetitorSummary[];
  view: View;
  onSort: (column: SortKey) => void;
  /** The id of the heading that names the table. */
  labelledBy: string;
  /** The app-zone day the page reads as of. */
  today: string;
};

export function CompetitorTable({ rows, view, onSort, labelledBy, today }: CompetitorTableProps) {
  return (
    <Table role="table" aria-labelledby={labelledBy} className="max-xl:block xl:table-fixed">
      <colgroup>
        <col className="w-52" />
        <col className="w-35" />
        <col className="w-32" />
        <col className="w-36" />
        <col className="w-36" />
        <col />
        <col className="w-7" />
      </colgroup>
      <TableHeader role="rowgroup" className="max-xl:hidden">
        <TableRow role="row" className="hover:bg-transparent">
          <SortHead column="name" view={view} onSort={onSort} />
          <SortHead column="threat" view={view} onSort={onSort} />
          <SortHead column="momentum" view={view} onSort={onSort} />
          <SortHead column="open_roles" view={view} onSort={onSort} />
          <TableHead role="columnheader">Total raised</TableHead>
          <TableHead role="columnheader">Latest move</TableHead>
          <TableHead role="columnheader" className="pl-0">
            <span className="sr-only">Open</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody role="rowgroup" className="max-xl:block">
        {rows.map((row) => (
          <CompetitorRow key={row.slug} row={row} product={view.product} today={today} />
        ))}
      </TableBody>
    </Table>
  );
}
