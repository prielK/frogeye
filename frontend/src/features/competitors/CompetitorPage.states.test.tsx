import { within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { competitorProfile, dayOneProfile, dayOneReference, intelReference, posting, sparseProfile, SPARSE_NOTES, youngProfile } from "@/test/fixtures";
import { server } from "@/test/msw";
import { menu, openProfile, section, serveRules } from "@/test/profile";
import { densify } from "@/features/intel/series";

/* The competitor deep dive with little, no or young data: honest empty states and no number nobody measured. */

describe("a competitor with little or no data", () => {
  it("renders every section's honest empty state and never a fake zero", async () => {
    await openProfile(sparseProfile());

    const overview = section("Overview");
    expect(within(overview).getByText("Not enough evidence for a brief yet")).toBeInTheDocument();
    expect(within(overview).getByText(/^0 public signals in the last 180 days \(3 needed\)\./)).toBeInTheDocument();
    const figures = within(overview).getByRole("list", { name: "Key figures" });
    expect(within(figures).getAllByText("Insufficient data").some((el) => el.getAttribute("data-threat") === "insufficient")).toBe(true);
    expect(within(figures).getByRole("img", { name: "Momentum, last 13 weeks: Insufficient data" })).toBeInTheDocument();
    expect(within(figures).getByText("No public Greenhouse, Lever or Ashby board found")).toBeInTheDocument();
    expect(within(figures).getAllByText("Not reported")).toHaveLength(2);
    expect(within(figures).queryByText(/^0$|^0\.0|^\$0|^0 /)).not.toBeInTheDocument();

    const expectations: [string, string][] = [
      ["Products vs JFrog", "No products of this competitor are mapped to JFrog products"],
      ["Momentum", "No public signals about it in the last 180 days"],
      ["People & hiring", "No public Greenhouse, Lever or Ashby board found"],
      ["Headcount", "No headcount or layoff figures have been reported in its sources yet"],
      ["Money", "No funding, valuation or revenue figures have been reported in its sources yet"],
      ["Market", "Market share appears only when a cited report states it"],
      ["Customers", "No customers or customer wins and losses have been reported in its sources yet"],
      ["Pricing", "No pricing page is watched for this competitor"],
      ["Community", "No GitHub repositories are tracked for this competitor"],
      ["Community", "No Hacker News search runs for this competitor"],
      ["People & partners", "No leadership changes, acquisitions, partnerships or offices have been reported in its sources yet"],
      ["Latest signals", "No public signals about it yet"],
      ["Sources", "No news sources of its own: it is covered through industry feeds only"],
    ];
    for (const [name, text] of expectations) expect(within(section(name)).getByText(text, { exact: false })).toBeInTheDocument();

    // an insufficient threat is an empty state, never a flat zero series or "0.0"; with
    // nothing of its own the section is one quiet line, not an empty chart and lanes.
    const momentum = section("Momentum");
    expect(within(momentum).getByText(/^No public signals about it in the last 180 days/)).toBeInTheDocument();
    expect(momentum.querySelector("[data-quiet-note]")).not.toBeNull();
    expect(momentum.querySelectorAll("[data-line]")).toHaveLength(0);
    expect(within(momentum).queryByText(/0\.0|^0$/)).not.toBeInTheDocument();
    // No hiring chart draws at all without a board, and nothing reads 0 open roles.
    const hiring = section("People & hiring");
    expect(hiring.querySelectorAll("svg [data-line], [data-bar-fill]")).toHaveLength(0);
    expect(hiring).not.toHaveTextContent(/\b0 open/);
    // Share of voice is measured (its peers have articles): it is drawn, and says Anchore had none.
    expect(within(section("Market")).getByRole("figure", { name: "Share of voice" })).toHaveTextContent(/Anchore drew no articles in 13 weeks/);
    // No brief, so nothing is cited.
    expect(within(section("Sources")).queryByRole("list", { name: "Cited in the brief" })).not.toBeInTheDocument();
    expect(SPARSE_NOTES).toHaveLength(14);
  });

  it("says a board was found but has no first snapshot yet, and never shows 0 open roles", async () => {
    const profile = sparseProfile();
    profile.kpis.has_board = true;
    profile.hiring.has_board = true;
    profile.sources_freshness.notes = profile.sources_freshness.notes.map((n) => (n.section === "hiring" ? { ...n, text: "Job board found — first snapshot pending" } : n));
    await openProfile(profile);
    expect(within(within(section("Overview")).getByRole("list", { name: "Key figures" })).getByText("Job board found — first snapshot pending")).toBeInTheDocument();
    expect(within(section("People & hiring")).getByText(/^Job board found — first snapshot pending/)).toBeInTheDocument();
  });

  it("says no brief has been written yet when evidence is enough but none is filed", async () => {
    const profile = competitorProfile();
    profile.brief = null;
    profile.sources_freshness.notes = [{ section: "overview", text: "No brief has been written yet: briefs are written weekly" }];
    await openProfile(profile);
    expect(within(section("Overview")).getByText("No brief has been written yet")).toBeInTheDocument();
    expect(within(section("Overview")).getByText("Briefs are written weekly.")).toBeInTheDocument();
    // The key figures stand without the brief.
    expect(within(within(section("Overview")).getByRole("list", { name: "Key figures" })).getByText("142")).toBeInTheDocument();
  });
});

describe("densify", () => {
  it("fills a key's missing days with 0 once it has appeared, and leaves it absent before", () => {
    const days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"];
    const out = densify(
      [
        { x: "2026-10-02", key: "a", y: 3 },
        { x: "2026-10-04", key: "a", y: 5 },
        { x: "2026-10-01", key: "b", y: 1 },
      ],
      days,
    );
    expect(out.get("a")).toEqual([
      { x: "2026-10-02", y: 3 },
      { x: "2026-10-03", y: 0 },
      { x: "2026-10-04", y: 5 },
    ]);
    expect(out.get("b")).toEqual([
      { x: "2026-10-01", y: 1 },
      { x: "2026-10-02", y: 0 },
      { x: "2026-10-03", y: 0 },
      { x: "2026-10-04", y: 0 },
    ]);
  });
});

// ---- more cases ----------------------------------------------------

describe("a board under 30 days old", () => {
  it("states no 30-day change anywhere and starts every line at the board's first snapshot", async () => {
    await openProfile(youngProfile());
    const figures = within(section("Overview")).getByRole("list", { name: "Key figures" });
    const roles = within(figures).getByText("Open roles", { selector: "[data-kpi-label]" }).closest("li")!;
    expect(within(roles).getByText("142")).toBeInTheDocument();
    expect(roles).toHaveTextContent(/No 30-day change yet: its board was first read on \d{1,2} \w{3}/);
    expect(roles).not.toHaveTextContent(/in 30 days/);
    // The threat counts no hiring trend, and says so.
    const threat = within(figures).getByText("Threat to JFrog", { selector: "[data-kpi-label]" }).closest("li")!;
    expect(threat).not.toHaveTextContent(/hiring [+−]/);

    const hiring = section("People & hiring");
    const open = within(hiring).getByRole("figure", { name: "Open roles" });
    expect(open).toHaveTextContent(/142 open now; its board has 10 days of history, since \d{1,2} \w{3}, so no 30-day change yet/);
    // The 90-day view holds only the 11 days since the first snapshot: nothing drawn before it.
    expect(within(open).getAllByRole("row").length - 1).toBe(11);
    const mix = within(hiring).getByRole("figure", { name: "Open roles by department" });
    expect(mix.querySelectorAll("[data-direction]")).toHaveLength(0);
    // No change is known, so the summary claims none ("no department grew" would be a claim).
    expect(mix).toHaveTextContent("Engineering has the most open roles (48 of 142).");
    expect(mix).not.toHaveTextContent(/grew/);
    // a department's bar opens its open roles
    expect(within(mix).getByRole("link", { name: /^Engineering: 48 open roles/ }).getAttribute("aria-label")).not.toMatch(/in 30 days/);
    const tags = within(hiring).getByRole("figure", { name: "Capabilities in its open roles" });
    expect(tags.querySelectorAll("[data-direction]")).toHaveLength(0);
  });
});

describe("community sources and dates", () => {
  it("names each source and its last fetch, dates the stars, and links every repository", async () => {
    await openProfile();
    const community = section("Community");
    const head = community.querySelector("h2")!.parentElement!;
    expect(head).toHaveTextContent(/GitHub: sonatype\s*·\s*fetched 5h ago/);
    expect(head).toHaveTextContent(/Hacker News: Sonatype\s*·\s*fetched 4h ago/);
    const stars = within(community).getByRole("figure", { name: "GitHub stars" });
    expect(stars).toHaveTextContent(/sonatype\/nexus-public leads with 3,620 stars on \d{1,2} \w{3}, \+\d+ since \d{1,2} \w{3}/);
    expect(within(stars).getByRole("link", { name: /^sonatype\/nexus-public/ })).toHaveAttribute("href", "https://github.com/sonatype/nexus-public");
    expect(within(stars).getByRole("link", { name: /^sonatype-nexus-community\/nancy/ })).toHaveAttribute(
      "href",
      "https://github.com/sonatype-nexus-community/nancy",
    );
  });
});

describe("share of voice with nothing counted", () => {
  it("is an empty state, never four flat zero lines, and the menu marks Market as having no data", async () => {
    const profile = competitorProfile();
    profile.market.share_of_voice = profile.market.share_of_voice.map((p) => ({ ...p, documents: 0, signals: 0 }));
    profile.market.market_share = [];
    await openProfile(profile);
    // nothing counted and no share reported: quiet lines, never four flat zero lines.
    const market = section("Market");
    expect(within(market).getByText(/^Nothing counted for it or its peers in 13 weeks/)).toBeInTheDocument();
    expect(market.querySelectorAll("[data-line]")).toHaveLength(0);
    expect(within(menu()).getByRole("link", { name: "Market, no data yet" })).toBeInTheDocument();
  });
});

describe("smaller fixes", () => {
  it("offers a retry when the open roles fail to load", async () => {
    let fail = true;
    server.use(
      http.get("/api/v1/competitors/:slug/postings", () =>
        fail ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : HttpResponse.json({ items: [posting({ id: 8100, title: "Recovered role" })], next_cursor: null }),
      ),
    );
    await openProfile();
    const hiring = section("People & hiring");
    await userEvent.click(within(hiring).getByRole("button", { name: "Show all 142 open roles" }));
    expect(await within(hiring).findByText("The open roles didn't load.")).toBeInTheDocument();
    fail = false;
    await userEvent.click(within(hiring).getByRole("button", { name: "Try again" }));
    expect(await within(hiring).findByText("Recovered role")).toBeInTheDocument();
  });

  it("names an analysis it cannot title as unavailable, in plain text", async () => {
    const profile = competitorProfile();
    profile.products[0]!.signal_ids = [9101, 4242];
    await openProfile(profile);
    const table = within(section("Products vs JFrog")).getByRole("table", { name: "Products vs JFrog" });
    const nexus = within(table).getAllByRole("row")[1]!;
    await userEvent.click(within(nexus).getByText("evidence 9 · 2 analyses"));
    expect(within(nexus).getByRole("link", { name: "Sonatype bundles Repository Firewall into every Nexus One plan" })).toHaveAttribute("href", "/signals/9101");
    const unknown = within(nexus).getByText("Signal not available");
    expect(unknown.closest("a")).toBeNull();
  });

  it("says a measured board has no open roles right now, not that data is missing", async () => {
    const profile = competitorProfile();
    Object.assign(profile.hiring, { open_now: 0, delta_30d: -3, by_department: [], by_seniority: [], by_location: [], tags: [], recent: [] });
    profile.kpis.open_roles = 0;
    profile.kpis.delta_30d = -3;
    await openProfile(profile);
    const hiring = section("People & hiring");
    expect(within(hiring).getAllByText("No open roles on its board right now").length).toBeGreaterThan(0);
    expect(hiring).not.toHaveTextContent("No department recorded");
  });
});

// ---- cross-cutting cases ----------------------------------------------------------------

describe("the rules come from the API", () => {
  it("states the thresholds the API serves in every formula and trend word", async () => {
    const base = intelReference();
    serveRules({
      ...base,
      severity_weights: { high: 5, medium: 3, low: 1 },
      overlap: { high_evidence: 8, medium_evidence: 4, lookback_days: 120 },
      threat: { ...base.threat, high_score: 15, medium_score: 5, min_signals: 4, lookback_days: 120, momentum_factor_min: 0.6, momentum_factor_max: 1.8, hiring_factor_bound: 0.3 },
      trend: { ...base.trend, cap: 2.5, rising_ratio: 2, easing_ratio: 0.7 },
    });
    await openProfile();
    const threat = within(section("Overview")).getByText("How this is computed").closest("details")!;
    expect(threat).toHaveTextContent(/held between 0\.6 and 1\.8/);
    expect(threat).toHaveTextContent(/within ±0\.3/);
    expect(threat).toHaveTextContent(/high from a score of 15, medium from 5/);
    expect(threat).toHaveTextContent(/fewer than 4 public signals in 120 days/);
    const overlap = within(section("Products vs JFrog")).getByText("How this is computed").closest("details")!;
    expect(overlap).toHaveTextContent(/high 5, medium 3, low 1/);
    expect(overlap).toHaveTextContent(/High from 8, medium from 4, low from 1/);
    const momentum = section("Momentum");
    expect(within(momentum).getByText("How this is computed").closest("details")!).toHaveTextContent(/at most 2\.5×: rising from 2×, easing at 0\.7× or less/);
    // 1.6× is steady when the API rises from 2×.
    expect(within(momentum).getByRole("figure", { name: "Weekly momentum" })).toHaveTextContent(/1\.6× the 9 before: steady/);
    const figures = within(section("Overview")).getByRole("list", { name: "Key figures" });
    expect(within(figures).getByText("1.6× · steady")).toBeInTheDocument();
  });
});

describe("day 1 of a young deployment", () => {
  async function openDayOne() {
    serveRules(dayOneReference());
    return openProfile(dayOneProfile());
  }

  it("starts every weekly series at the first collected week and says why no trend reads yet", async () => {
    await openDayOne();
    const figures = within(section("Overview")).getByRole("list", { name: "Key figures" });
    const momentum = within(figures).getByText("Momentum", { selector: "[data-kpi-label]" }).closest("li")!;
    expect(within(momentum).getByRole("img", { name: /^Momentum, last 3 weeks: 112 to 131/ })).toBeInTheDocument();
    expect(momentum).toHaveTextContent(/no baseline yet: collected since \d{1,2} \w{3,4}/);

    const weekly = within(section("Momentum")).getByRole("figure", { name: "Weekly momentum" });
    expect(weekly).toHaveTextContent(/^Weekly momentum131 this week; no baseline yet: FrogEye has collected since \d{1,2} \w{3,4}, under the 13 weeks a trend compares\./);
    expect(weekly).not.toHaveTextContent(/no public signals in the 9 weeks/);
    expect(within(weekly).getAllByRole("row").length - 1).toBe(3);
    const how = within(section("Momentum")).getByText("How this is computed").closest("details")!;
    expect(how).toHaveTextContent(/FrogEye has collected since \d{1,2} \w{3,4} \d{4}, so its weeks start then: a trend needs all 13 collected, from \d{1,2} \w{3,4} \d{4}\./);

    // Share of voice and Hacker News count the weeks collected, never 13.
    const market = section("Market");
    expect(market.querySelector("h2")!.parentElement!).toHaveTextContent(/Share of voice: 3 weeks to \d{1,2} \w{3,4}/);
    expect(within(market).getByRole("figure", { name: "Share of voice" })).toHaveTextContent(/over 3 weeks/);
    expect(within(section("Community")).getByRole("figure", { name: "Hacker News mentions" })).toHaveTextContent(/in 3 weeks to/);
    expect(document.body).not.toHaveTextContent(/13 weeks to|in 13 weeks|over 13 weeks|last 13 weeks/);
    // The 180-day lanes began 20 days ago: the months before are not quiet months, and the footnote says so.
    expect(within(section("Momentum")).getByRole("figure", { name: "Moves by kind" })).toHaveTextContent(
      /FrogEye has collected since \d{1,2} \w{3,4} \d{4}; before then the lanes hold only moves its sources still listed when it began\./,
    );
  });

  it("says a board first read today was first read today, never that it has 0 days of history", async () => {
    await openDayOne();
    const open = within(section("People & hiring")).getByRole("figure", { name: "Open roles" });
    expect(open).toHaveTextContent(/142 open now; its board was first read today, so no 30-day change yet/);
    expect(open).not.toHaveTextContent(/0 days of history/);
    // One snapshot is no line yet: the frame says so (no axis, no lone dot), and the window toggle waits for one.
    expect(open).toHaveTextContent("One snapshot so far");
    expect(open.querySelector("svg")).toBeNull();
    expect(within(open).queryByRole("group", { name: "Window" })).not.toBeInTheDocument();
    const figures = within(section("Overview")).getByRole("list", { name: "Key figures" });
    const roles = within(figures).getByText("Open roles", { selector: "[data-kpi-label]" }).closest("li")!;
    expect(roles).toHaveTextContent("No 30-day change yet: its board was first read today");
  });
});

describe("Community with no forks counted", () => {
  it("keeps the Stars/Forks toggle and says no forks were recorded, not that no repository is tracked", async () => {
    const profile = competitorProfile();
    profile.community.repos = profile.community.repos.map((r) => ({ ...r, forks: r.forks.map((p) => ({ ...p, value: 0 })) }));
    await openProfile(profile);
    const community = section("Community");
    await userEvent.click(within(community).getByRole("button", { name: "Forks" }));
    const forks = within(community).getByRole("figure", { name: "GitHub forks" });
    expect(forks).toHaveTextContent("No forks recorded");
    expect(forks).not.toHaveTextContent(/No GitHub repositories are tracked/);
    expect(forks.querySelectorAll("[data-line]")).toHaveLength(0);
    // The toggle stays, so the reader can go back to the stars.
    await userEvent.click(within(community).getByRole("button", { name: "Stars" }));
    expect(within(community).getByRole("figure", { name: "GitHub stars" }).querySelectorAll("[data-line]").length).toBeGreaterThan(0);
  });
});

describe("the menu's Market check counts only the drawn series", () => {
  it("marks Market as having no data when only the fourth peer, which the chart drops, has counts", async () => {
    const profile = competitorProfile();
    const drawn = new Set([profile.competitor.slug, ...profile.market.peers.slice(0, 3)]);
    const dropped = profile.market.peers[3]!;
    profile.market.share_of_voice = profile.market.share_of_voice.map((p) => (drawn.has(p.competitor_slug) ? { ...p, documents: 0, signals: 0 } : p));
    profile.market.market_share = [];
    expect(profile.market.share_of_voice.some((p) => p.competitor_slug === dropped && p.documents > 0)).toBe(true);
    await openProfile(profile);
    expect(within(section("Market")).getByText(/^Nothing counted for it or its peers in 13 weeks/)).toBeInTheDocument();
    expect(within(menu()).getByRole("link", { name: "Market, no data yet" })).toBeInTheDocument();
  });
});
