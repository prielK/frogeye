import { DiamondIcon, TriangleIcon, type LucideIcon } from "lucide-react";
import { useId } from "react";
import { Link } from "react-router";
import { ProductLink } from "@/components/common/ProductLink";
import type { SignalCard } from "@/components/signal/query";
import { StanceLabel } from "@/components/signal/StanceLabel";
import type { Schemas } from "@/lib/api/client";
import { ROLES } from "@/lib/roles";
import { cn } from "@/lib/utils";
import { ABOVE_COVER, CARD, CARD_COVER, STORY_TEXT, STORY_TITLE } from "./story";
import { StoryMeta } from "./StoryMeta";
import { CompetitorMomentum, ImportanceMeter, StanceChip } from "./Weight";

type Takeaway = Schemas["Takeaway"];
type Product = Takeaway["products"][number];
type Action = { role: Schemas["RoleAction"]["role"] | null; text: string };
type Angle = Takeaway["angle"];
type Risk = NonNullable<Takeaway["risk_level"]>;

const CONFIDENCE: Record<string, string> = {
  high: "The editor's confidence in this takeaway is high.",
  medium: "The editor's confidence in this takeaway is medium.",
  low: "The editor's confidence in this takeaway is low.",
};

const SEVERITY_RANK: Record<Product["severity"], number> = { high: 0, medium: 1, low: 2 };
const MAX_PRODUCTS = 5;
const MAX_ACTIONS = 3;

/** A label inside the card ("Why it matters for JFrog", "What to do"): body semibold, rank by weight, not size. */
const LABEL = "text-body font-semibold text-foreground";

/**
 * The label over the why-it-matters line, by where the line comes from (`angle`, exact copy;
 * the backend's briefing_schema.ANGLE_LABEL, so the email says the same): the JFrog product
 * impact on the master, on an edition card whose analysis has no line for its role, and on
 * every row filed before angles; otherwise the edition role's own line, from the analysis or,
 * on a stage-A card, the role's own stage-A line.
 */
const ANGLE_LABEL: Record<Angle, string> = {
  jfrog: "Why it matters for JFrog",
  sales: "In your deals",
  pm: "Product angle",
  exec: "Strategic angle",
  pmm_devrel: "Messaging angle",
};

/**
 * The Exec risk chip's look per level. The mark is the email's (up triangle high, diamond
 * medium, down triangle low), so a level reads by its shape and its word; only high takes the
 * threat tone, on its soft ground, and medium and low stay as quiet as a product tag.
 */
const RISK: Record<Risk, { icon: LucideIcon; mark?: string; className: string }> = {
  high: { icon: TriangleIcon, className: "border-threat/30 bg-threat-soft text-threat" },
  medium: { icon: DiamondIcon, className: "border-input bg-card text-foreground" },
  low: { icon: TriangleIcon, mark: "rotate-180", className: "border-input bg-card text-muted-foreground" },
};

/** "Risk: high" as a 20px mark beside the Strategic angle label: icon plus word, never colour alone. */
function RiskChip({ level }: { level: Risk }) {
  const look = RISK[level];
  const Icon = look.icon;
  return (
    <span
      data-risk={level}
      className={cn("inline-flex h-5 items-center gap-1 rounded-sm border px-1.5 text-meta font-medium whitespace-nowrap", look.className)}
    >
      <Icon aria-hidden className={cn("size-2.5 shrink-0 fill-current", look.mark)} />
      {`Risk: ${level}`}
    </span>
  );
}

/**
 * What a card says, from either shape of takeaway. A current briefing copies
 * its body from the signal's analysis (or, marked, its stage-A first read); one filed before
 * has only `so_what` and `now_what`, read as the summary and a single action, unmarked: those
 * rows read `from_analysis` false whatever their source.
 */
function bodyOf(t: Takeaway) {
  const legacy = !t.summary.trim();
  const copied = t.actions.map((a): Action => ({ role: a.role, text: a.text.trim() })).filter((a) => a.text);
  const nowWhat = t.now_what.trim();
  return {
    summary: (legacy ? t.so_what : t.summary).trim(),
    why: t.why_it_matters.trim(),
    // A row filed before angles has none: it is the JFrog line.
    label: ANGLE_LABEL[t.angle] ?? ANGLE_LABEL.jfrog,
    // Highest severity first (stable within a level), as the analysis page's side column.
    products: [...t.products].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]),
    actions: (copied.length > 0 ? copied : nowWhat ? [{ role: null, text: nowWhat }] : []).slice(0, MAX_ACTIONS),
    firstRead: !legacy && !t.from_analysis,
  };
}

const TAG = "inline-flex h-6 max-w-full min-w-0 items-center gap-1.5 rounded-sm border bg-card px-2 text-meta whitespace-nowrap";
/** A tag that opens its product's page: above the card's cover, with the chip's hover (an `input-hover` hairline on `accent`) and focus ring. */
const TAG_LINK = cn(
  ABOVE_COVER,
  "cursor-pointer transition-[border-color,background-color] duration-150 ease-out-quart",
  "hover:border-input-hover hover:bg-accent active:bg-secondary",
  "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none",
);

/**
 * The affected JFrog products as tags: the product, then its effect as icon + word in the meaning
 * colour. Each tag opens the product's deep dive, a link of its own beside the
 * card's cover; a name that is not one of JFrog's products stays a plain tag.
 */
function ProductTags({ products }: { products: Product[] }) {
  const shown = products.slice(0, MAX_PRODUCTS);
  const hidden = products.length - shown.length;
  return (
    <ul aria-label="JFrog products affected" className="flex flex-wrap items-center gap-1.5">
      {shown.map((p, i) => (
        <li key={`${i}-${p.product}`} className="flex max-w-full min-w-0">
          <ProductLink name={p.product} className={TAG} linkClassName={TAG_LINK}>
            <span className="truncate font-medium text-foreground">{p.product}</span>
            <StanceLabel stance={p.effect} showNeutral />
          </ProductLink>
        </li>
      ))}
      {hidden > 0 ? <li className="text-meta text-muted-foreground">+{hidden} more</li> : null}
    </ul>
  );
}

type TakeawayCardProps = {
  takeaway: Takeaway;
  signals: Map<number, SignalCard>;
  /** The first takeaway leads the page across the full width; the rest sit beneath it, two abreast. */
  variant: "lead" | "secondary";
  /** The edition the briefing was written for: the master labels each action with its role. */
  edition: string;
  /** The full analysis page of a signal. */
  hrefFor: (id: number) => string;
  /** Whether a competitor is JFrog itself, which has no momentum. */
  isSelf?: (slug: string) => boolean;
};

/**
 * One top takeaway: a single link to its story's full analysis page (its headline, whose cover
 * spans the card, so the whole block opens it), read as an editorial block rather than a boxed
 * card; its product tags open the product pages, above that cover. Every takeaway has the same
 * anatomy: the serif title, its weight (the importance meter and the stance chip), the summary, why it matters under its angle's label ("Why it matters for JFrog" on
 * the master, "In your deals" on Sales…; on Exec with the risk chip beside it) with the
 * affected products, "What to do" (up to three actions, each labelled with its role on the
 * master), then the meta line and the "Read full analysis →" affordance, shown at rest. The
 * lead is the page's hero: its headline at `display`, its importance meter long, and its
 * competitor's 13-week momentum beside its stance chip; from `xl` its "What to do" takes a ruled column of its own beside
 * the story and why it matters, and the middle row absorbs any difference in height, so
 * neither column leaves a hole.
 */
export function TakeawayCard({ takeaway, signals, variant, edition, hrefFor, isSelf = () => false }: TakeawayCardProps) {
  const titleId = useId();
  const todoId = useId();
  const cited = takeaway.signal_ids.flatMap((id) => signals.get(id) ?? []);
  const primary = cited[0];
  const { summary, why, label, products, actions, firstRead } = bodyOf(takeaway);
  const master = edition === "master";
  // the master never shows a risk level, whatever a row carries.
  const risk = master ? null : (takeaway.risk_level ?? null);
  const lead = variant === "lead";
  // The lead splits in two only when there is a "What to do" to fill its second column.
  const split = lead && actions.length > 0;
  const mover = lead ? primary?.competitors.find((slug) => !isSelf(slug)) : undefined;

  const story = (
    <div className={cn("flex min-w-0 flex-col gap-2.5", split && "xl:col-start-1 xl:row-start-1 xl:pr-10")}>
      <h3 id={titleId} className={cn(STORY_TITLE, lead && "text-display")}>
        {primary ? (
          // The card's one link to the analysis: its ::after covers the whole card.
          <Link to={hrefFor(primary.id)} className={CARD_COVER}>
            {takeaway.title}
          </Link>
        ) : (
          takeaway.title
        )}
      </h3>
      {primary ? (
        // The story's weight, read before its text: importance, stance and, on the lead, its competitor's momentum.
        <div data-weight className={cn("flex flex-wrap items-end gap-x-5 gap-y-3", lead ? "pt-1.5 pb-1.5" : "pt-0.5 pb-0.5")}>
          <ImportanceMeter value={primary.importance} size={lead ? "lead" : "card"} />
          <StanceChip stance={primary.stance} />
          {mover ? <CompetitorMomentum slug={mover} /> : null}
        </div>
      ) : null}
      {summary ? <p className={cn(STORY_TEXT, "text-foreground")}>{summary}</p> : null}
    </div>
  );

  const whyBlock =
    why || products.length > 0 || risk ? (
      <div className={cn("flex min-w-0 flex-col gap-2.5", split && "xl:col-start-1 xl:row-start-2 xl:pr-10")}>
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <p className={LABEL}>{label}</p>
            {risk ? <RiskChip level={risk} /> : null}
          </div>
          {why ? <p className={cn(STORY_TEXT, "text-foreground")}>{why}</p> : null}
        </div>
        {products.length > 0 ? <ProductTags products={products} /> : null}
      </div>
    ) : null;

  const todoBlock =
    actions.length > 0 ? (
      <div className={cn("flex min-w-0 flex-col gap-1", split && "xl:col-start-2 xl:row-span-2 xl:row-start-1 xl:border-l xl:pl-10")}>
        <p id={todoId} className={LABEL}>
          What to do
        </p>
        <ul aria-labelledby={todoId} className="flex list-disc flex-col gap-1.5 pl-[1.125rem] marker:text-muted-foreground">
          {actions.map((a, i) => (
            <li key={i} className={cn(STORY_TEXT, "text-foreground")}>
              {master && a.role ? <span className="font-semibold">{ROLES[a.role].label}: </span> : null}
              {a.text}
            </li>
          ))}
        </ul>
      </div>
    ) : null;

  const foot = (
    <div className={cn("mt-auto flex flex-col items-start gap-3 pt-1", split && "xl:col-span-2 xl:row-start-3")}>
      <StoryMeta signals={cited} firstRead={firstRead} extra={CONFIDENCE[takeaway.confidence]} aboveCover={Boolean(primary)} />
      {primary ? <span className="text-body font-semibold text-primary-ink">Read full analysis →</span> : null}
    </div>
  );

  const layout = cn(
    "gap-5 wrap-anywhere",
    split && "xl:grid xl:grid-cols-[minmax(0,4fr)_minmax(0,3fr)] xl:grid-rows-[auto_1fr_auto] xl:gap-x-0",
  );
  // Without its signal on the page (withdrawn since) the story reads, but leads nowhere.
  return (
    <article aria-labelledby={titleId} data-variant={variant} className={cn(CARD, layout)}>
      {story}
      {whyBlock}
      {todoBlock}
      {foot}
    </article>
  );
}
