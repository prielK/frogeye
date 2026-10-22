import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { SECTIONS } from "@/features/competitors/useCompetitorProfile";
import type { Schemas } from "@/lib/api/client";
import { competitorProfile } from "./fixtures";
import { server } from "./msw";
import { renderAs } from "./render";

/*
 * The competitor deep dive's test helpers, shared by its three test files (split by topic so a
 * busy parallel run spreads the long page over workers).
 */

type CompetitorProfile = Schemas["CompetitorProfile"];

export const LABELS = ["Overview", "Products vs JFrog", "Momentum", "People & hiring", "Headcount", "Money", "Market", "Customers", "Pricing", "Community", "People & partners", "Latest signals", "Sources"];

/** Serve GET /competitors/{slug}/profile from `profile` (any other slug is a 404). */
export function serveProfile(profile: CompetitorProfile) {
  server.use(
    http.get("/api/v1/competitors/:slug/profile", ({ params }) =>
      params.slug === profile.competitor.slug ? HttpResponse.json(profile) : HttpResponse.json({ detail: "Competitor not found" }, { status: 404 }),
    ),
  );
}

/** Serve GET /intel-reference from `rules` (the pages' thresholds and the collected-since day). */
export function serveRules(rules: Schemas["IntelReferenceOut"]) {
  server.use(http.get("/api/v1/intel-reference", () => HttpResponse.json(rules)));
}

export async function openProfile(profile: CompetitorProfile = competitorProfile(), role: Parameters<typeof renderAs>[0] = "pm") {
  serveProfile(profile);
  const app = renderAs(role, `/competitors/${profile.competitor.slug}`);
  // A text query, not a role query: role queries over the whole long page are what cost time.
  await screen.findByText(profile.competitor.name, { selector: "h1" });
  return app;
}

/** A section by its menu label (the first test checks each is a region named by its head). */
export function section(name: string): HTMLElement {
  const id = SECTIONS.find((s) => s.label === name)!.id;
  const el = document.getElementById(id);
  if (!el) throw new Error(`no section ${name}`);
  return el;
}

export const menu = () => document.querySelector<HTMLElement>('nav[aria-label="Sections"]')!;
