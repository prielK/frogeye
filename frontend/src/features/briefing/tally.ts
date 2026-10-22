import type { SignalCard } from "@/components/signal/query";
import type { BriefingOut } from "./useBriefing";

/*
 * What the reader's own edition adds up to, for "Today at a glance": computed only from the
 * stories this edition tells on the page (its top takeaways and its section rows, each once),
 * never from another edition's, so every reader's summary is their own (persona-correct).
 */

export type Stance = "threat" | "opportunity" | "neutral";
export const STANCES: Stance[] = ["threat", "opportunity", "neutral"];

export type Mover = { slug: string; importance: number; stories: number };
export type ProductInPlay = { name: string; stories: number };

export type Glance = {
  /** The stories on the page, takeaways first, each once. */
  stories: SignalCard[];
  stance: Record<Stance, number>;
  /** Competitors by their stories' summed importance, highest first (then most stories, then name). */
  movers: Mover[];
  /** JFrog products the top stories name, by how many of them name each (then first named). */
  products: ProductInPlay[];
  /** How many top stories there are (the products' denominator). */
  topStories: number;
};

/** A stance the API may leave unset reads as neutral: it is neither a threat nor an opportunity. */
export function stanceOf(signal: Pick<SignalCard, "stance"> | undefined): Stance {
  const s = signal?.stance;
  return s === "threat" || s === "opportunity" ? s : "neutral";
}

/** The signal ids this edition tells: the top three takeaways', then every section row's, each once. */
export function toldIds(briefing: BriefingOut): number[] {
  const ids = [
    ...briefing.content.top_takeaways.slice(0, 3).flatMap((t) => t.signal_ids),
    ...briefing.content.sections.flatMap((s) => s.items.map((i) => i.signal_id)),
  ];
  return [...new Set(ids)];
}

export function glanceOf(briefing: BriefingOut, isSelf: (slug: string) => boolean = () => false): Glance {
  const bySignal = new Map(briefing.signals.map((s) => [s.id, s]));
  const stories = toldIds(briefing).flatMap((id) => bySignal.get(id) ?? []);

  const stance: Record<Stance, number> = { threat: 0, opportunity: 0, neutral: 0 };
  for (const s of stories) stance[stanceOf(s)] += 1;

  const moved = new Map<string, Mover>();
  for (const s of stories) {
    for (const slug of new Set(s.competitors)) {
      if (isSelf(slug)) continue;
      const m = moved.get(slug) ?? { slug, importance: 0, stories: 0 };
      m.importance += s.importance;
      m.stories += 1;
      moved.set(slug, m);
    }
  }
  const movers = [...moved.values()].sort((a, b) => b.importance - a.importance || b.stories - a.stories || a.slug.localeCompare(b.slug));

  const top = briefing.content.top_takeaways.slice(0, 3);
  const named = new Map<string, ProductInPlay>();
  for (const t of top) {
    for (const name of new Set(t.products.map((p) => p.product.trim()).filter(Boolean))) {
      const p = named.get(name) ?? { name, stories: 0 };
      p.stories += 1;
      named.set(name, p);
    }
  }
  // Stable sort: equal counts keep the order the stories first name them in.
  const products = [...named.values()].sort((a, b) => b.stories - a.stories);

  return { stories, stance, movers, products, topStories: top.length };
}
