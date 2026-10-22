import { cn } from "@/lib/utils";

/*
 * What a briefing card and a story row share. Each is ONE link to the story's full analysis
 * page with nothing interactive inside it (a control inside a link is invalid and unreachable),
 * so its trust line is text, not the tooltip control, and its company chips are not filters.
 * The link's hit area is the block itself plus a `::before` wash that bleeds past the text
 * column (so the text stays aligned with the page) and carries the hover, press and focus
 * states; the link is `isolate`, so the wash paints behind the text and above the page.
 *
 * A takeaway card also names the JFrog products the story affects, and each opens its product
 * page, so its link is a cover instead: the headline is the ONE link to the
 * analysis, its `::after` spans the card (the card is its positioned frame) and its `::before`
 * is the wash behind the text. Native link behaviour still works anywhere on the card; the
 * product tags are links of their own, beside the cover and lifted above it, never inside it.
 */

const WASH = cn(
  "before:pointer-events-none before:absolute before:-z-10 before:rounded-lg before:content-['']",
  "before:transition-colors before:duration-150 before:ease-out-quart",
  "hover:before:bg-accent/70 active:before:bg-accent",
  "focus-visible:outline-none focus-visible:before:ring-2 focus-visible:before:ring-ring",
);

/** A takeaway card: the positioned, `isolate` frame its cover and wash spread over. */
export const CARD = "relative isolate flex h-full min-w-0 flex-col";

/**
 * A takeaway card's cover, its headline link: the wash and the hit area both bleed 12px on every
 * side, into the gutters between cards. Hovering anywhere on the card underlines the headline.
 */
export const CARD_COVER = cn(
  "cursor-pointer decoration-1 underline-offset-[0.18em] hover:underline",
  WASH,
  "before:-inset-3",
  "after:absolute after:-inset-3 after:rounded-lg after:content-['']",
);

/** Above a card's cover: its product tags (links of their own) and the time's hover tooltip. */
export const ABOVE_COVER = "relative z-10";

/** A story row's link: the wash bleeds sideways only, between the hairlines that part the rows. */
export const ROW_LINK = cn(
  "group/story relative isolate flex min-w-0 cursor-pointer flex-col wrap-anywhere",
  WASH,
  "before:inset-y-0 before:-inset-x-3 before:rounded-md",
);

/** The serif headline of a card or row; it underlines while the story is hovered. */
export const STORY_TITLE = "font-headline text-title text-balance decoration-1 underline-offset-[0.18em] group-hover/story:underline";

/** Card bodies and row summaries; the measure follows the lede's. */
export const STORY_TEXT = "max-w-[62ch] text-body text-pretty";

/*
 * ONE sentence rule per side, the same as the backend's publish.first_sentence (email
 * and Slack rows), with the same test cases: a sentence ends at ".", "!" or "?" (and any closing
 * quotes or brackets) that a new sentence follows, except a period that ends an abbreviation or
 * an initial, so "Oct. 15", "U.S. East", "9 a.m. ET", "No. 1" and "Inc." stay whole.
 */
const SENTENCE_END = /[.!?]["'”’)\]]*(?=\s+["'“‘(]?[A-Z0-9])/g;
const ABBREVIATION =
  /(?:^|[\s("'“‘])(?:[A-Z]|(?:[A-Za-z]\.)+[A-Za-z]|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|No|Nos|Inc|Corp|Ltd|Co|Mr|Mrs|Ms|Dr|St|vs|etc|approx)\.$/;

/** The first sentence of `text`, on one line: up to the first ".", "!" or "?" that a new sentence follows. */
export function firstSentence(text: string): string {
  const t = text.split(/\s+/).filter(Boolean).join(" ");
  for (const m of t.matchAll(SENTENCE_END)) {
    const at = m.index ?? 0;
    if (t[at] === "." && ABBREVIATION.test(t.slice(0, at + 1))) continue;
    return t.slice(0, at + m[0].length);
  }
  return t;
}
