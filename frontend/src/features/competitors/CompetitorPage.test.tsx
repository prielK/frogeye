import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { posting } from "@/test/fixtures";
import { server } from "@/test/msw";
import { LABELS, menu, openProfile, section } from "@/test/profile";
import { SECTIONS } from "./useCompetitorProfile";

/*
 * The competitor deep dive from a full profile: every section's content and the citations. The
 * menu and the page's states are in CompetitorPage.menu.test.tsx, the honest empty and young
 * states in CompetitorPage.states.test.tsx (one long page split over three files).
 */

describe("the deep dive, from a full profile", () => {
  it("renders every section, in order, under the exact menu labels", async () => {
    await openProfile();

    // Structural queries, not whole-page role queries: the page is long, and those are what cost time.
    const back = [...document.querySelectorAll<HTMLAnchorElement>('a[href="/competitors"]')].find((a) => a.textContent === "All competitors");
    expect(back).toBeDefined();
    expect(screen.getByText(/makes Nexus Repository and the Nexus One platform/)).toBeInTheDocument();
    // Layout A: one long page, a section per menu item, in the page's order and words.
    const heads = [...document.querySelectorAll("h2")].map((h) => h.textContent);
    expect(heads.filter((h) => LABELS.includes(h ?? ""))).toEqual(LABELS);
    expect(within(menu()).getAllByRole("link").map((a) => a.textContent)).toEqual(LABELS);
    expect(SECTIONS.map((s) => s.label)).toEqual(LABELS);
    // Each is a region named by its head: a <section> labelled by its h2.
    for (const label of LABELS) {
      const el = section(label);
      expect(el.tagName).toBe("SECTION");
      const head = document.getElementById(el.getAttribute("aria-labelledby")!)!;
      expect(head.tagName).toBe("H2");
      expect(head).toHaveTextContent(label);
      expect(el.contains(head)).toBe(true);
    }
    expect(menu().tagName).toBe("NAV");
    // Persona-agnostic: no role or edition switch anywhere on the page.
    expect(document.querySelector('[role="tablist"]')).toBeNull();
  });

  it("leads the overview with the cited bottom line, dated and versioned, then the key figures with their sources", async () => {
    await openProfile();
    const overview = section("Overview");

    expect(within(overview).getByText(/turning Nexus One into a bundle aimed at Artifactory renewals/)).toBeInTheDocument();
    // the brief's as-of date and version.
    expect(within(overview).getByText(/^Written \d{1,2} \w{3} 2026 · version 3$/)).toBeInTheDocument();
    expect(within(overview).getByText(/from 9 signals, 3 facts, 2 postings and 1 page change/)).toBeInTheDocument();
    expect(within(overview).getByText("A general-availability date for the model registry.")).toBeInTheDocument();

    const figures = within(overview).getByRole("list", { name: "Key figures" });
    const cell = (label: string) => within(figures).getByText(label, { selector: "[data-kpi-label]" }).closest("li")!;
    // Threat as shape and word, its score beside it.
    expect(within(cell("Threat to JFrog")).getByText("High")).toHaveAttribute("data-threat", "high");
    expect(within(cell("Threat to JFrog")).getByText(/score 18\.4/)).toBeInTheDocument();
    expect(within(cell("Momentum")).getByRole("img", { name: /^Momentum, last 13 weeks: 62 to 131/ })).toBeInTheDocument();
    expect(within(cell("Momentum")).getByText("1.6× · rising")).toBeInTheDocument();
    // Open roles, with where the count comes from and when it was read.
    const roles = cell("Open roles");
    expect(within(roles).getByText("142")).toBeInTheDocument();
    expect(within(roles).getByText(/\+12 in 30 days/)).toBeInTheDocument();
    expect(within(roles).getByText(/Sonatype on Greenhouse/)).toBeInTheDocument();
    // Headcount: the newest figure, its date, and every source when they disagree.
    const headcount = cell("Headcount");
    expect(within(headcount).getByText("1,300")).toBeInTheDocument();
    expect(within(headcount).getByText(/10 Jun 2026/)).toBeInTheDocument();
    expect(within(headcount).getByText("Sources disagree")).toBeInTheDocument();
    expect(within(headcount).getByRole("link", { name: /Sonatype about page/ })).toHaveAttribute("href", "https://www.sonatype.com/about");
    expect(within(headcount).getByRole("link", { name: /TechCrunch/ })).toHaveAttribute("href", "https://techcrunch.com/2026/06/03/sonatype-cpo");
    // the funding total states its scope, never a bare sum.
    const raised = cell("Total raised");
    expect(within(raised).getByText("≥ $154M disclosed")).toBeInTheDocument();
    expect(within(raised).getByText("4 rounds, incl. 1 undated · 1 not counted")).toBeInTheDocument();
    expect(within(raised).getByText(/Series C, \$45M, 12 Mar 2024/)).toBeInTheDocument();
    expect(within(raised).getByRole("link", { name: /TechCrunch/ })).toHaveAttribute("href", "https://techcrunch.com/2024/03/12/sonatype-series-c");
  });

  it("explains how the threat, the overlap and the momentum are computed, thresholds named", async () => {
    await openProfile();
    const how = screen.getAllByText("How this is computed");
    expect(how).toHaveLength(3);

    const threat = within(section("Overview")).getByText("How this is computed").closest("details")!;
    expect(threat).toHaveTextContent(/score 18\.4 = overlap 11 × momentum 1\.60 × hiring 1\.05/);
    expect(threat).toHaveTextContent(/high from a score of 12, medium from 4/);
    expect(threat).toHaveTextContent(/fewer than 3 public signals in 180 days/);
    const overlap = within(section("Products vs JFrog")).getByText("How this is computed").closest("details")!;
    expect(overlap).toHaveTextContent(/high 3, medium 2, low 1/);
    expect(overlap).toHaveTextContent(/High from 6, medium from 3, low from 1/);
    const momentum = within(section("Momentum")).getByText("How this is computed").closest("details")!;
    expect(momentum).toHaveTextContent(/rising from 1\.25×, easing at 0\.8× or less/);
    expect(momentum).toHaveTextContent(/at most 3×/);
  });

  it("tables their products against JFrog's, overlap as a word and a mark, with the brief's edges", async () => {
    await openProfile();
    const table = within(section("Products vs JFrog")).getByRole("table", { name: "Products vs JFrog" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0]!.querySelector("[data-product]")!.textContent)).toEqual([
      "Nexus Repository",
      "Repository Firewall",
      "Lifecycle",
      "SBOM Manager",
    ]);
    const nexus = rows[0]!;
    expect(within(nexus).getByText("Artifactory")).toBeInTheDocument();
    expect(within(nexus).getByText("High")).toHaveAttribute("data-overlap", "high");
    expect(within(nexus).getByText("evidence 9 · 4 analyses")).toBeInTheDocument();
    expect(within(nexus).getByText(/leave out what Curation costs/)).toBeInTheDocument();
    expect(within(rows[3]!).getByText("No recent evidence")).toHaveAttribute("data-overlap", "none");
  });

  it("draws momentum as weekly values and the moves by lane, each dot opening its analysis", async () => {
    await openProfile();
    const momentum = section("Momentum");
    const weekly = within(momentum).getByRole("figure", { name: "Weekly momentum" });
    expect(weekly).toHaveTextContent(/131 this week; the last 4 weeks average 1\.6× the 9 before: rising/);
    const lanes = within(momentum).getByRole("figure", { name: "Moves by kind" });
    expect(lanes).toHaveTextContent(/21 public moves in 180 days/);
    expect(within(lanes).getByRole("link", { name: /^Sonatype bundles Repository Firewall into every Nexus One plan, Pricing/ })).toHaveAttribute("href", "/signals/9101");
  });

  it("shows hiring with a 7/30/90 window, a department/seniority/location breakdown, capability tags and the newest postings", async () => {
    await openProfile();
    const hiring = section("People & hiring");

    const open = within(hiring).getByRole("figure", { name: "Open roles" });
    expect(open).toHaveTextContent("142 open now, up 12 in 30 days");
    expect(within(open).getByRole("button", { name: "90 days" })).toHaveAttribute("aria-pressed", "true");
    const rowsOf = (figure: HTMLElement) => within(figure).getAllByRole("row").length - 1;
    expect(rowsOf(open)).toBe(90);
    await userEvent.click(within(open).getByRole("button", { name: "7 days" }));
    expect(within(open).getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "true");
    expect(rowsOf(open)).toBe(8); // today-7 .. today, the two ends a 7-day change compares

    const mix = within(hiring).getByRole("figure", { name: "Open roles by department" });
    // 12 departments: the top 8 and "Other (4)", each with its 30-day change.
    expect(within(mix).getByText("Other (4)")).toBeInTheDocument();
    // a department's bar opens its open roles
    expect(within(mix).getByRole("link", { name: /^Engineering: 48 open roles, .*\+5 in 30 days/ })).toBeInTheDocument();
    await userEvent.click(within(mix).getByRole("button", { name: "Seniority" }));
    expect(within(hiring).getByRole("figure", { name: "Open roles by seniority" })).toHaveTextContent("Staff & Principal");

    const tags = within(hiring).getByRole("figure", { name: "Capabilities in its open roles" });
    // Named from the capability taxonomy; a slug it lacks reads as words; each opens its roles.
    expect(within(tags).getByRole("link", { name: /^Container registry: 21 open roles, .*\+4 in 30 days/ })).toBeInTheDocument();
    expect(within(tags).getByRole("link", { name: /^Software composition analysis: 15 open roles, .*−2 in 30 days/ })).toBeInTheDocument();
    expect(within(tags).getAllByText("+6").length).toBeGreaterThan(0);

    const list = within(hiring).getByRole("list", { name: "Newest open roles" });
    expect(within(list).getByRole("link", { name: /Staff Engineer, AI Model Security/ })).toHaveAttribute("href", "https://boards.greenhouse.io/sonatype/jobs/7001");
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent(/Security · Staff & Principal · Remote, United States/);
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent(/Model scanning/);
  });

  it("loads every open role from the postings endpoint on request", async () => {
    const more = Array.from({ length: 50 }, (_, i) => posting({ id: 8000 + i, title: `Role ${i + 1}` }));
    const asked: string[] = [];
    server.use(
      http.get("/api/v1/competitors/:slug/postings", ({ request }) => {
        const url = new URL(request.url);
        asked.push(url.search);
        return url.searchParams.get("cursor")
          ? HttpResponse.json({ items: [posting({ id: 9999, title: "The last role" })], next_cursor: null })
          : HttpResponse.json({ items: more, next_cursor: "page-2" });
      }),
    );
    await openProfile();
    const hiring = section("People & hiring");
    await userEvent.click(within(hiring).getByRole("button", { name: "Show all 142 open roles" }));
    const list = within(hiring).getByRole("list", { name: "Open roles" });
    await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(50));
    expect(asked[0]).toBe("?status=open&limit=50");
    await userEvent.click(within(hiring).getByRole("button", { name: "Show more open roles" }));
    expect(await within(list).findByText("The last role")).toBeInTheDocument();
    expect(asked[1]).toBe("?status=open&limit=50&cursor=page-2");
    expect(within(hiring).queryByRole("button", { name: "Show more open roles" })).not.toBeInTheDocument();
  });

  it("plots reported headcount as dots only, layoffs as markers, undated figures in a list of their own", async () => {
    await openProfile();
    const headcount = section("Headcount");
    const chart = within(headcount).getByRole("figure", { name: "Reported headcount" });
    expect(chart).toHaveTextContent("Reported figures only — no line is drawn between them");
    expect(headcount).toHaveTextContent("4 reported figures and 1 layoff, the newest of 10 Jun 2026");
    expect(chart.querySelectorAll("path")).toHaveLength(0);
    // Four figures and one layoff on the axis; the undated figure is never placed on it.
    expect(chart.querySelectorAll('[data-mark][data-kind="point"]')).toHaveLength(4);
    expect(chart.querySelectorAll('[data-mark][data-kind="event"]')).toHaveLength(1);
    expect(within(chart).getByText("Sources disagree")).toBeInTheDocument();
    const undated = within(headcount).getByRole("list", { name: "Undated" });
    expect(undated).toHaveTextContent("1,000 employees");
    expect(within(undated).getByRole("link", { name: /DevOps\.com/ })).toHaveAttribute("href", "https://devops.com/sonatype-campus");
    // Every figure keeps its source link and date.
    const figures = within(headcount).getByRole("list", { name: "Reported figures" });
    expect(within(figures).getByRole("link", { name: /SiliconANGLE/ })).toHaveAttribute("href", "https://siliconangle.com/2025/02/10/sonatype");
    expect(figures).toHaveTextContent(/Layoff · 10%/);
  });

  it("shows funding as round columns under a cumulative step line, disputed and undisclosed rounds marked", async () => {
    await openProfile();
    const money = section("Money");
    const chart = within(money).getByRole("figure", { name: "Funding raised" });
    expect(chart).toHaveTextContent(/≥ \$100M disclosed across 3 dated rounds/);
    expect(chart.querySelectorAll("[data-mark]")).toHaveLength(4);
    expect(chart.querySelector('[data-state="disputed"]')).not.toBeNull();
    expect(chart.querySelector('[data-state="undisclosed"]')).not.toBeNull();
    const rounds = within(money).getByRole("list", { name: "Funding rounds" });
    expect(rounds).toHaveTextContent(/Series B · \$35M/);
    expect(within(rounds).getByRole("link", { name: /SiliconANGLE/ })).toBeInTheDocument();
    expect(rounds).toHaveTextContent(/states \$33M/);
    expect(within(money).getByRole("list", { name: "Undated" })).toHaveTextContent(/Debt financing · \$54M/);
    expect(within(money).getByRole("list", { name: "Valuation, revenue and ARR" })).toHaveTextContent(/\$185M ARR/);
  });

  it("compares share of voice with the top 3 peers only, densifying a peer's missing weeks", async () => {
    await openProfile();
    const market = section("Market");
    const voice = within(market).getByRole("figure", { name: "Share of voice" });
    // the competitor and its top 3 peers by overlap; the 4th peer (Snyk) is left out.
    const legend = within(voice).getByRole("list", { name: "Legend" });
    // Peers are named from the competitor index once it loads.
    await waitFor(() => expect(within(legend).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Sonatype", "GitHub", "GitLab", "Chainguard"]));
    expect(voice).not.toHaveTextContent("Snyk");
    // before its first week a key is absent ("—"); after it, a missing week is a 0.
    const rows = within(voice).getAllByRole("row").slice(1);
    const chainguard = rows.map((r) => r.querySelectorAll("td")[3]!.textContent);
    expect(chainguard.slice(0, 3)).toEqual(["—", "—", "—"]);
    expect(chainguard[7]).toBe("0");
    expect(within(market).getByRole("list", { name: "Market share" })).toHaveTextContent(/18% · software composition analysis, IDC/);
  });

  it("lists customers, pricing changes with a diff viewer, community, people and partners, and the latest signals", async () => {
    await openProfile();
    const customers = section("Customers");
    expect(customers).toHaveTextContent("Acme Bank");
    expect(customers).toHaveTextContent("2,000 customers");
    expect(within(customers).getByRole("list", { name: "Undated" })).toHaveTextContent("Globex Insurance");
    expect(within(customers).getByRole("link", { name: /Globex moves its artifact management to Nexus One/ })).toHaveAttribute("href", "/signals/9140");

    const pricing = section("Pricing");
    const changes = within(pricing).getAllByRole("article");
    expect(changes).toHaveLength(2);
    const diff = within(changes[0]!).getByRole("group", { name: /^Changed lines/ });
    expect([...diff.querySelectorAll('[data-diff="removed"]')].map((l) => l.textContent)).toContain("Repository Firewall: add-on, from $12 per developer per month");
    expect([...diff.querySelectorAll('[data-diff="added"]')].map((l) => l.textContent)).toContain("Repository Firewall: included");
    expect(within(changes[0]!).getByText("+3 −2 lines")).toBeInTheDocument();
    expect(within(changes[0]!).getByRole("link", { name: /pricing page/i })).toHaveAttribute("href", "https://www.sonatype.com/pricing");

    const community = section("Community");
    expect(within(community).getByRole("figure", { name: "GitHub stars" })).toHaveTextContent(/sonatype\/nexus-public leads with 3,620 stars/);
    expect(within(community).getByRole("figure", { name: "Hacker News mentions" })).toHaveTextContent(/59 in 13 weeks/);

    const people = section("People & partners");
    expect(people).toHaveTextContent("Partnership · AWS");
    expect(people).toHaveTextContent("Office · Bengaluru, India");

    const latest = section("Latest signals");
    const links = within(latest).getAllByRole("link", { name: /^(?!Sonatype$)/ }).filter((a) => a.getAttribute("href")?.startsWith("/signals/"));
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/signals/9101", "/signals/9113", "/signals/9110", "/signals/9114", "/signals/9115", "/signals/9112"]);
    // The page's own competitor is not repeated as a chip; another company is.
    expect(await within(latest).findByRole("link", { name: "GitHub" })).toHaveAttribute("href", "/competitors/github");
  });

  it("closes with the cited sources and every collection source's health and freshness", async () => {
    await openProfile();
    const sources = section("Sources");
    const cited = within(sources).getByRole("list", { name: "Cited in the brief" });
    expect(within(cited).getAllByRole("listitem")).toHaveLength(8);
    expect(within(cited).getByRole("link", { name: /^Sonatype raises \$45M Series C to expand its supply chain platform/ })).toHaveAttribute(
      "href",
      "https://techcrunch.com/2024/03/12/sonatype-series-c",
    );
    expect(within(cited).getByText(/a \$45 million Series C round/)).toBeInTheDocument();
    const collection = within(sources).getByRole("list", { name: "Collected from" });
    expect(within(collection).getAllByRole("listitem")).toHaveLength(6);
    expect(within(collection).getByText("Sonatype pricing page").closest("li")).toHaveTextContent(/Degraded/);
    // each section says its own why; Sources keeps only the notes about the sources.
    expect(within(sources).queryByRole("list", { name: "Coverage notes" })).not.toBeInTheDocument();
    expect(within(collection).getByText("Sonatype pricing page").closest("li")).toHaveTextContent(/fetched /);
  });
});

describe("citations", () => {
  it("resolve: every [n] marker points at a numbered source on the page, and a missing number is skipped", async () => {
    await openProfile();
    const markers = screen.getAllByRole("link", { name: /^Source \d+: / });
    expect(markers.length).toBeGreaterThanOrEqual(12);
    for (const marker of markers) {
      const id = marker.getAttribute("href")!.slice(1);
      expect(document.getElementById(id), id).not.toBeNull();
    }
    expect(markers.map((m) => m.textContent)).not.toContain("[99]");

    await userEvent.click(markers[0]!);
    const target = document.getElementById("source-1")!;
    expect(target).toHaveAttribute("data-highlighted", "true");
    expect(target).toHaveFocus();
  });

  it("render a hidden signal as plain text, with no link", async () => {
    await openProfile();
    const cited = within(section("Sources")).getByRole("list", { name: "Cited in the brief" });
    const hidden = within(cited).getByText("Signal not available");
    expect(hidden.closest("a")).toBeNull();
    expect(within(cited).getByRole("link", { name: "Nexus Repository adds an AI model registry in beta" })).toHaveAttribute("href", "/signals/9110");
  });
});
