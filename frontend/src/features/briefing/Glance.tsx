import { useId, useMemo, useSyncExternalStore } from "react";
import { useNavigate } from "react-router";
import { BarList } from "@/components/charts/BarList";
import { plural } from "@/lib/format";
import { nameFromSlug, useCompetitorIndex } from "@/lib/competitors";
import { useJfrogProducts } from "@/lib/intelReference";
import { productHref } from "@/lib/products";
import { cn } from "@/lib/utils";
import { glanceOf, STANCES, type Stance } from "./tally";
import type { BriefingOut } from "./useBriefing";

const STANCE_ROW: Record<Stance, { label: string; color: "threat" | "opportunity" | "neutral" }> = {
  threat: { label: "Threat", color: "threat" },
  opportunity: { label: "Opportunity", color: "opportunity" },
  neutral: { label: "Neutral", color: "neutral" },
};

const count = (n: number, one: string, many: string) => plural(n, one, many);

/** Phones (below `md`): the strip sits above the stories, so its lists fold sooner. */
const PHONE = "(max-width: 47.99rem)";
function subscribe(onChange: () => void) {
  const query = window.matchMedia?.(PHONE);
  query?.addEventListener?.("change", onChange);
  return () => query?.removeEventListener?.("change", onChange);
}
const usePhone = () => useSyncExternalStore(subscribe, () => Boolean(window.matchMedia?.(PHONE).matches), () => false);

/**
 * "Today at a glance": what the reader's own edition adds up to, in three small bar lists from
 * the chart kit, each with its printed summary and its table of exact values. From `xl` it is
 * the page's sticky right rail; below that, a strip above the stories (three abreast from `md`;
 * on a phone each list folds past three rows into "Other (n)", so the stories come soon).
 * A quiet or unwritten day has no glance at all, never charts drawn at zero.
 */
export function TodayAtAGlance({ briefing, className }: { briefing: BriefingOut; className?: string }) {
  const id = useId();
  const navigate = useNavigate();
  const index = useCompetitorIndex();
  const jfrogProducts = useJfrogProducts();
  const phone = usePhone();
  const glance = useMemo(() => glanceOf(briefing, (slug) => Boolean(index.get(slug)?.is_self)), [briefing, index]);
  if (glance.stories.length === 0) return null;

  const { stance, movers, products, topStories } = glance;
  const nameOf = (slug: string) => index.get(slug)?.name ?? nameFromSlug(slug);
  const stanceSummary = `${count(stance.threat, "threat", "threats")}, ${count(stance.opportunity, "opportunity", "opportunities")}, ${stance.neutral} neutral`;
  const top = movers[0];
  const lead = products[0];

  return (
    <aside aria-labelledby={id} className={cn("flex min-w-0 flex-col", className)}>
      <h2 id={id} className="border-t border-foreground pt-3 pb-1 text-body font-semibold">
        Today at a glance
      </h2>
      <p className="text-meta text-muted-foreground">
        {`From the ${plural(glance.stories.length, "story", "stories")} in your edition.`}
      </p>
      <div className="mt-5 grid gap-x-8 gap-y-7 md:grid-cols-3 xl:grid-cols-1 xl:gap-y-0 xl:divide-y">
        <BarList
          className="xl:pb-6"
          title="Stories by stance"
          summary={stanceSummary}
          empty={{ title: "No stories today" }}
          unit="stories"
          items={STANCES.filter((s) => stance[s] > 0).map((s) => ({ key: s, label: STANCE_ROW[s].label, value: stance[s], color: STANCE_ROW[s].color }))}
        />
        <BarList
          className="xl:py-6"
          title="Who moved"
          summary={top ? `${nameOf(top.slug)} moved most: ${plural(top.stories, "story", "stories")}, importance ${top.importance} in all.` : ""}
          empty={{ title: "No competitor named", hint: "Today's stories are industry news, not one competitor's move." }}
          unit="importance, summed"
          max={phone ? 3 : 5}
          onNavigate={(href) => navigate(href)}
          items={movers.map((m) => ({ key: m.slug, label: nameOf(m.slug), value: m.importance, href: `/competitors/${m.slug}` }))}
        />
        <BarList
          className="xl:pt-6"
          title="Your products in play"
          summary={lead ? `${lead.name} is named in ${lead.stories} of today's top stories.` : ""}
          empty={{ title: "No JFrog product named", hint: `None of today's ${plural(topStories, "top story", "top stories")} names a JFrog product.` }}
          unit={topStories === 1 ? "top story" : "top stories"}
          max={phone ? 3 : 5}
          onNavigate={(href) => navigate(href)}
          items={products.map((p) => ({ key: p.name, label: p.name, value: p.stories, href: productHref(p.name, jfrogProducts) ?? undefined }))}
        />
      </div>
    </aside>
  );
}
