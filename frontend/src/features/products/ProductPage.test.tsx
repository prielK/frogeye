import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { Schemas } from "@/lib/api/client";
import { dayDate } from "@/components/charts/scales";
import { analysedSignal, briefing, dayOneProduct, dayOneProducts, dayOneReference, intelReference, JFROG_PRODUCT_NAMES, localDay, productProfile, productSummaries, quietProduct, quietProductSummaries } from "@/test/fixtures";
import { server, serveSignalDetails } from "@/test/msw";
import { serveRules } from "@/test/profile";
import { renderAs } from "@/test/render";
import { areaNote, areaSeries, type HiringArea } from "./useProducts";

type ProductProfile = Schemas["ProductProfile"];

const SECTIONS = ["Bottom line", "Competitors", "Pressure over time", "Gaps and parity", "What to do", "Competitor hiring in this area", "Signals timeline", "Sources"];

function serveProducts(rows: Schemas["ProductSummary"][]) {
  server.use(http.get("/api/v1/products", () => HttpResponse.json(rows)));
}

/** Serve GET /products/{slug} from `profile` (any other slug is a 404, as the API's). */
function serveProduct(profile: ProductProfile) {
  server.use(
    http.get("/api/v1/products/:slug", ({ params }) =>
      params.slug === profile.slug ? HttpResponse.json(profile) : HttpResponse.json({ detail: "Product not found" }, { status: 404 }),
    ),
  );
}

async function openProduct(profile: ProductProfile = productProfile(), role: Parameters<typeof renderAs>[0] = "pm") {
  serveProduct(profile);
  const app = renderAs(role, `/products/${profile.slug}`);
  await screen.findByText(profile.name, { selector: "h1" });
  return app;
}

/** A section of the page by its head (each is a region named by it); a structural query, cheap on a long page. */
function section(name: string): HTMLElement {
  const head = [...document.querySelectorAll("h2")].find((h) => h.textContent === name);
  if (!head) throw new Error(`no section ${name}`);
  return head.closest("section")!;
}

describe("the product list", () => {
  it("lists the 13 products, most pressed first, each with what it does, its pressure and its top competitors", async () => {
    serveProducts(productSummaries());
    renderAs("sales", "/products");

    const list = await screen.findByRole("list", { name: "JFrog products" });
    expect(screen.getByRole("heading", { level: 1, name: "Products" })).toBeInTheDocument();
    expect(screen.getByText("13 JFrog products. 3 are under rising competitive pressure, 4 steady and 2 easing; 4 have seen no competitive pressure in 13 weeks.")).toBeInTheDocument();
    const rows = within(list).getAllByRole("article");
    // Rising, then steady, easing and none; the portfolio's order within each.
    expect(rows.map((r) => within(r).getByRole("heading", { level: 3 }).textContent)).toEqual([
      "Artifactory",
      "Advanced Security",
      "JFrog ML",
      "Xray",
      "AppTrust",
      "AI Catalog",
      "Container Registry",
      "Curation",
      "Platform",
      "Distribution",
      "Runtime",
      "MCP Registry",
      "Connect",
    ]);
    const artifactory = rows[0]!;
    // The row is ONE link to the product's page; its competitor chips sit above it and open the profiles.
    expect(within(artifactory).getByRole("link", { name: "Artifactory" })).toHaveAttribute("href", "/products/artifactory");
    expect(within(artifactory).getByText(/^Universal artifact repository and the core of the platform/)).toBeInTheDocument();
    expect(within(artifactory).getByText("Rising pressure")).toHaveAttribute("data-pressure", "rising");
    const chips = within(within(artifactory).getByRole("list", { name: "Companies" })).getAllByRole("link");
    expect(chips.map((c) => c.getAttribute("href"))).toEqual(["/competitors/sonatype", "/competitors/github", "/competitors/cloudsmith"]);
    // no pressure reads in words, never as a measured zero.
    const connect = rows.at(-1)!;
    expect(within(connect).getByText("No competitive pressure in 13 weeks")).toHaveAttribute("data-pressure", "none");
    expect(within(connect).getByText("No competitor mapped yet")).toBeInTheDocument();
    // the resting chevron is under the row's cover, so a click on it opens the product.
    expect(artifactory.querySelector("svg.lucide-chevron-right")).toHaveClass("pointer-events-none");
  });

  it("says plainly when no product is under pressure yet", async () => {
    serveProducts(quietProductSummaries());
    renderAs("pm", "/products");

    expect(await screen.findByText("13 JFrog products; none has seen competitive pressure in 13 weeks.")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "JFrog products" })).getAllByText("No competitive pressure in 13 weeks")).toHaveLength(13);
  });

  it("opens a product's deep dive from its row, and is in the primary navigation", async () => {
    serveProducts(productSummaries());
    serveProduct(productProfile());
    const app = renderAs("exec", "/products");

    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Products" })).toHaveAttribute("href", "/products");
    await userEvent.click(await screen.findByRole("link", { name: "Artifactory" }));
    // the deep dive's route loads lazily, so the navigation completes once its code is in.
    await waitFor(() => expect(app.location().pathname).toBe("/products/artifactory"));
    expect(await screen.findByText("Artifactory", { selector: "h1" })).toBeInTheDocument();
  });

  it("offers a retry when the list does not load", async () => {
    let calls = 0;
    server.use(
      http.get("/api/v1/products", () => {
        calls += 1;
        return calls === 1 ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : HttpResponse.json(productSummaries());
      }),
    );
    renderAs("pm", "/products");

    expect(await screen.findByText("The products didn't load.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByRole("list", { name: "JFrog products" })).toBeInTheDocument();
  });
});

describe("a product's deep dive, from a full profile", () => {
  it("renders every section, in order, under a header with the product, what it does and its pressure", async () => {
    await openProduct();

    expect(screen.getByRole("link", { name: "All products" })).toHaveAttribute("href", "/products");
    expect(screen.getByText(/^Universal artifact repository and the core of the platform/)).toBeInTheDocument();
    const header = screen.getByText("Artifactory", { selector: "h1" }).closest("header")!;
    expect(within(header).getByText("Rising pressure")).toHaveAttribute("data-pressure", "rising");
    const heads = [...document.querySelectorAll("h2")].map((h) => h.textContent);
    expect(heads).toEqual(SECTIONS);
    // Each is a region named by its head: a <section> labelled by its h2.
    for (const name of SECTIONS) {
      const el = section(name);
      expect(document.getElementById(el.getAttribute("aria-labelledby")!)).toHaveTextContent(name);
    }
    // Persona-agnostic: no role or edition switch anywhere on the page.
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("leads with the cited bottom line, dated and versioned, beside the key figures", async () => {
    await openProduct();
    const bottom = section("Bottom line");

    expect(within(bottom).getByText(/^Sonatype is turning Nexus One into the default bundle for Artifactory renewals/)).toBeInTheDocument();
    // the brief's date and version.
    expect(within(bottom).getByText(/^Written \d{1,2} \w{3,4} \d{4} · version 2$/)).toBeInTheDocument();
    expect(within(bottom).getByText("Drawn from 9 signals; each claim is numbered to its source under Sources.")).toBeInTheDocument();

    const figures = within(bottom).getByRole("list", { name: "Key figures" });
    const cell = (label: string) => within(figures).getByText(label, { selector: "[data-kpi-label]" }).closest("li")!;
    // The pressure word is in the header; the figure adds its numbers.
    expect(within(cell("Threat pressure")).getByText("6.5 a week")).toBeInTheDocument();
    expect(within(cell("Threat pressure")).getByText("threat over the last 4 weeks; 1.6 in the 9 before")).toBeInTheDocument();
    expect(within(cell("Competitors")).getByText("4 with recent evidence")).toBeInTheDocument();
    expect(within(cell("Competitors")).getByText("1 high, 2 medium and 1 low threat · 5 tracked")).toBeInTheDocument();
    expect(within(cell("Public signals")).getByText("11 in 180 days")).toBeInTheDocument();
    expect(within(cell("Hiring in its area")).getByText("29 open roles")).toBeInTheDocument();
    expect(within(cell("Hiring in its area")).getByText("across 6 competitors' job boards, today")).toBeInTheDocument();
  });

  it("numbers every claim to a source that resolves, skipping an unknown one and keeping a hidden signal as text", async () => {
    await openProduct();

    const markers = [...document.querySelectorAll<HTMLAnchorElement>('a[href^="#source-"]')];
    expect(markers.length).toBeGreaterThanOrEqual(9);
    for (const m of markers) expect(document.getElementById(m.getAttribute("href")!.slice(1))).not.toBeNull();
    expect(markers.some((m) => m.getAttribute("href") === "#source-99")).toBe(false);

    const sources = section("Sources");
    const cited = within(sources).getByRole("list", { name: "Cited in the brief" });
    expect(within(cited).getAllByRole("listitem")).toHaveLength(6);
    expect(within(cited).getByRole("link", { name: "Sonatype folds Repository Firewall into Nexus One" })).toHaveAttribute("href", "/signals/9301");
    expect(within(cited).getByText("Signal not available").closest("a")).toBeNull();

    const first = within(section("Bottom line")).getAllByRole("link", { name: /^Source 1: Sonatype blog/ })[0]!;
    await userEvent.click(first);
    const target = document.getElementById("source-1")!;
    await waitFor(() => expect(target).toHaveFocus());
    expect(target).toHaveAttribute("data-highlighted", "true");
  });

  it("lists who competes with it and how hard, each linking to its profile and its last move", async () => {
    await openProduct();
    const comps = section("Competitors");

    expect(within(comps).getByText("4 of 5 competitors show recent evidence against Artifactory; Sonatype shows the most (evidence 14).")).toBeInTheDocument();
    const table = within(comps).getByRole("table", { name: "Competitors" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("link")[0]!.getAttribute("href"))).toEqual([
      "/competitors/sonatype",
      "/competitors/github",
      "/competitors/cloudsmith",
      "/competitors/gitlab",
      "/competitors/docker",
    ]);
    const [sonatype, , , , docker] = rows;
    expect(within(sonatype!).getByText("Nexus Repository")).toBeInTheDocument();
    expect(within(sonatype!).getByText("High")).toHaveAttribute("data-overlap", "high");
    expect(within(sonatype!).getByText("evidence 14")).toBeInTheDocument();
    expect(within(sonatype!).getByRole("link", { name: "Sonatype folds Repository Firewall into Nexus One" })).toHaveAttribute("href", "/signals/9301");
    // A seeded competitor with no recent evidence says so, never a 0 read as measured.
    expect(within(docker!).getByText("No recent evidence")).toHaveAttribute("data-overlap", "none");
    expect(within(docker!).getByText("No move naming it in 180 days")).toBeInTheDocument();
    expect(within(comps).getByText("How this is computed")).toBeInTheDocument();
    expect(within(comps).getByText(/high from 6, medium from 3, low from 1/)).toBeInTheDocument();
  });

  it("names a competitor with evidence but no threat entry as no threat, never as no evidence", async () => {
    const base = productProfile();
    const profile = { ...base, competitors: base.competitors.map((c) => (c.competitor_slug === "github" ? { ...c, threat_level: "none" as const, evidence: 4 } : c)) };
    await openProduct(profile);
    const rows = within(within(section("Competitors")).getByRole("table", { name: "Competitors" })).getAllByRole("row").slice(1);
    const github = rows.find((r) => within(r).getAllByRole("link")[0]!.getAttribute("href") === "/competitors/github")!;
    expect(within(github).getByText("No threat named")).toHaveAttribute("data-overlap", "none");
    expect(within(github).getByText("evidence 4")).toBeInTheDocument();
    expect(within(github).queryByText("No recent evidence")).toBeNull();
  });

  it("draws the weekly pressure as threat below and opportunity above one baseline, with how it is computed", async () => {
    await openProduct();
    const pressure = section("Pressure over time");

    const chart = within(pressure).getByRole("figure", { name: "Weekly pressure" });
    expect(within(chart).getByText("Threat 40 against opportunity 10 over 13 weeks; the last 4 weeks average 6.5 threat a week, against 1.6 in the 9 before: rising.")).toBeInTheDocument();
    expect(chart.querySelectorAll('[data-bar="below"]')).toHaveLength(11); // 13 weeks, two without threat
    expect(chart.querySelectorAll('[data-bar="above"]')).toHaveLength(6);
    expect(within(chart).getAllByText("Threat").length).toBeGreaterThan(0);
    expect(within(chart).getAllByText("Opportunity").length).toBeGreaterThan(0);
    expect(within(pressure).getByText(/rising from 1\.25×/)).toBeInTheDocument();
  });

  it("rolls up gaps and parity, cited, and what every team should do, without a role switch", async () => {
    await openProduct();

    const gaps = within(section("Gaps and parity")).getByRole("list", { name: "Gaps and parity" });
    expect(within(gaps).getAllByRole("listitem")).toHaveLength(3);
    expect(within(gaps).getByText(/^Parity: GitHub's artifact attestations match/)).toBeInTheDocument();
    expect(within(gaps).getAllByRole("link", { name: /^Source \d+:/ }).length).toBeGreaterThanOrEqual(3);

    const todo = section("What to do");
    // all four role groups, labelled, in order, the same for every reader.
    expect(within(todo).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["Sales", "Product", "Executive", "PMM & DevRel"]);
    const sales = within(todo).getByRole("list", { name: "Sales" });
    expect(within(sales).getAllByRole("listitem")).toHaveLength(3);
    // one level of quotes around a whole line goes; quotes that close inside it stay.
    expect(within(sales).getByText("Lead renewals with multi-site replication and federation, which Nexus One lacks.")).toBeInTheDocument();
    expect(within(sales).getByText('"Total cost" and "time to migrate" are the two questions to raise in every Sonatype bake-off.')).toBeInTheDocument();
    expect(within(within(todo).getByRole("list", { name: "Executive" })).getAllByRole("listitem")).toHaveLength(1);
    expect(within(todo).getByText("No action for PMM & DevRel in this brief.")).toBeInTheDocument();
  });

  it("shows competitor hiring in its area: the four with the most open roles today named, the rest one Other from the day all its boards are read, never JFrog", async () => {
    await openProduct();
    const hiring = section("Competitor hiring in this area");

    const chart = within(hiring).getByRole("figure", { name: "Open roles in its area" });
    expect(within(chart).getByText("29 open roles name its capabilities today, across 6 competitors; Sonatype has the most (14).")).toBeInTheDocument();
    // ranked by today's open roles, so Cloudsmith's young board (3 today) is named, and Chainguard (2) and Docker (1) fold.
    const legend = within(chart).getByRole("list", { name: "Legend" });
    expect(within(legend).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Sonatype", "GitHub", "GitLab", "Cloudsmith", "Other (2)"]);
    expect(within(legend).getByText("Other (2)").closest("li")).toHaveAttribute("title", "Chainguard, Docker");
    expect(chart.textContent).not.toMatch(/JFrog/);
    // The capabilities it counts, by name (an unknown slug in words); the late start of a drawn line, and where Other starts.
    expect(within(chart).getByText(/name Container registry, Remote proxy & caching or Package promotion/)).toBeInTheDocument();
    const note = within(chart).getByText(/Cloudsmith's line starts on/);
    expect(note).toHaveTextContent(/Cloudsmith's line starts on \d{1,2} \w{3,4}, its board's first snapshot/);
    expect(note).toHaveTextContent(/Other \(2\) sums Chainguard and Docker from \d{1,2} \w{3,4}, the first day both boards are read/);
    expect(note).not.toHaveTextContent(/Docker's/); // no line of its own, so no start of its own
    // The table twin: Cloudsmith's column is empty before its board was first read, never 0.
    const twin = within(chart).getByRole("table");
    const first = within(twin).getAllByRole("row")[1]!;
    const head = within(twin).getAllByRole("columnheader").map((h) => h.textContent);
    expect(within(first).getAllByRole("cell")[head.indexOf("Cloudsmith")]!).toHaveTextContent("—");
  });

  it("rests the list's and the rows' chevrons under their covers, so a click on one opens the row", async () => {
    await openProduct();
    const row = within(within(section("Signals timeline")).getByRole("list", { name: "Newest signals" })).getAllByRole("article")[0]!;
    expect(row.querySelector("svg.lucide-chevron-right")).toHaveClass("pointer-events-none");
  });

  it("puts every move naming it in its competitor's lane, each opening its analysis, then the newest signals", async () => {
    const app = await openProduct();
    const timeline = section("Signals timeline");

    const chart = within(timeline).getByRole("figure", { name: "Moves by competitor" });
    expect(within(chart).getByText(/^11 public moves name Artifactory in 180 days, the most from GitHub \(4\); the latest, from Sonatype, on/)).toBeInTheDocument();
    const lanes = [...chart.querySelectorAll("svg > g > g[aria-hidden] > text")].map((l) => l.textContent);
    expect(lanes).toEqual(["Sonatype", "GitHub", "Cloudsmith", "GitLab", "Docker"]);
    expect(chart.querySelectorAll("[data-mark]")).toHaveLength(12); // one move in two lanes

    const newest = within(timeline).getByRole("list", { name: "Newest signals" });
    expect(within(newest).getAllByRole("article")).toHaveLength(8);
    await userEvent.click(within(timeline).getByRole("button", { name: "Show all 9 signals" }));
    expect(within(newest).getAllByRole("article")).toHaveLength(9);
    await userEvent.click(within(newest).getByRole("link", { name: "GitHub cuts Packages storage prices for Enterprise Cloud" }));
    expect(app.location().pathname).toBe("/signals/9302");
  });

  it("is the same page for every reader", async () => {
    const { unmount } = await openProduct(productProfile(), "sales");
    const sales = document.querySelector("article")!.textContent;
    unmount();
    await openProduct(productProfile(), "exec");
    expect(document.querySelector("article")!.textContent).toBe(sales);
  });
});

describe("a product with no competitive signals", () => {
  it("renders an honest empty state in every section, and no zero as if measured", async () => {
    await openProduct(quietProduct());

    const header = screen.getByText("Connect", { selector: "h1" }).closest("header")!;
    // pressure "none" in words.
    expect(within(header).getByText("No competitive pressure in 13 weeks")).toHaveAttribute("data-pressure", "none");
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(SECTIONS);

    const bottom = section("Bottom line");
    // a missing brief, in these words, and why.
    expect(within(bottom).getByText("Not enough evidence for a brief yet")).toBeInTheDocument();
    expect(within(bottom).getByText("No public signal names Connect in the last 180 days (3 needed).")).toBeInTheDocument();
    const figures = within(bottom).getByRole("list", { name: "Key figures" });
    expect(figures).toHaveTextContent("None in 13 weeks");
    expect(figures).toHaveTextContent("None mapped yet");
    expect(figures).toHaveTextContent("None in 180 days");
    expect(figures).toHaveTextContent("No open role names its capabilities");

    expect(within(section("Competitors")).getByText(/^No competitor is mapped to Connect yet/)).toBeInTheDocument();
    expect(within(section("Pressure over time")).getByText("No competitive pressure in 13 weeks")).toBeInTheDocument();
    expect(section("Pressure over time").querySelector("[data-bar]")).toBeNull();
    // ONE note for everything that comes with the brief, never a box per section.
    expect(screen.getAllByText("Not enough evidence for a brief yet")).toHaveLength(1);
    expect(within(bottom).getByText(/Its bottom line, gaps and parity and each team's actions appear here once one is written\./)).toBeInTheDocument();
    for (const name of ["Gaps and parity", "What to do"]) {
      expect(within(section(name)).getByText("Comes with the product brief; see the bottom line above.")).toBeInTheDocument();
      expect(section(name).querySelector(".border-dashed")).toBeNull();
    }
    expect(within(section("What to do")).queryByRole("list")).not.toBeInTheDocument();

    const hiring = section("Competitor hiring in this area");
    // Read on two boards with nothing counted: a measured nothing, in words, never a flat 0 line.
    expect(within(hiring).getByText("No open role names its capabilities")).toBeInTheDocument();
    expect(within(hiring).getByText("None of the 2 competitor job boards FrogEye reads lists an open posting tagged Edge distribution.")).toBeInTheDocument();
    expect(hiring.querySelector("[data-line]")).toBeNull();

    expect(within(section("Signals timeline")).getByText("No public signal names Connect in the last 180 days")).toBeInTheDocument();
    expect(within(section("Signals timeline")).queryByRole("list", { name: "Newest signals" })).not.toBeInTheDocument();
    expect(within(section("Sources")).getByText("No brief has been written for Connect yet, so nothing is cited.")).toBeInTheDocument();
    expect(document.querySelector("article")!.textContent).not.toMatch(/\b0 (open roles|public|competitors)/);
  });

  it("says when no competitor job board has been read yet, and when a brief is due but not written", async () => {
    const p = quietProduct();
    p.hiring_in_area = { capabilities: ["edge-distribution"], boards: [], total: [], by_competitor: [] };
    p.timeline = [0, 1, 2].map((i) => ({ signal_id: 9500 + i, day: localDay(-10 * i), lane: "Launches", importance: 40, title: `A move ${i}`, competitor_slug: "sonatype" }));
    await openProduct(p);

    expect(within(section("Competitor hiring in this area")).getByText("No competitor job board has been read yet")).toBeInTheDocument();
    expect(within(section("Bottom line")).getByText("No brief has been written yet")).toBeInTheDocument();
    expect(within(section("Bottom line")).getByText("Briefs are written weekly; 3 public signals name Connect in the last 180 days.")).toBeInTheDocument();
  });

  it("says plainly when a brief has no bottom line or cites no source", async () => {
    const p = productProfile();
    p.brief = { ...p.brief!, bottom_line: [], sources: [] };
    await openProduct(p);

    const bottom = section("Bottom line");
    expect(within(bottom).getByText(/^Written \d{1,2} \w{3,4} \d{4} · version 2$/)).toBeInTheDocument();
    expect(within(bottom).getByText("The brief has no bottom line")).toBeInTheDocument();
    expect(within(bottom).queryByText(/Not enough evidence|No brief has been written/)).not.toBeInTheDocument();
    const sources = section("Sources");
    expect(within(sources).getByText("This brief cites no source.")).toBeInTheDocument();
    expect(within(sources).queryByText(/No brief has been written/)).not.toBeInTheDocument();
  });

  it("reads an unknown product as not tracked, with the way back", async () => {
    serveProduct(productProfile());
    renderAs("pm", "/products/nexus");

    expect(await screen.findByText("This isn't a JFrog product FrogEye tracks")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All products" })).toHaveAttribute("href", "/products");
  });

  it("offers a retry when the page does not load", async () => {
    let calls = 0;
    server.use(
      http.get("/api/v1/products/:slug", () => {
        calls += 1;
        return calls === 1 ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : HttpResponse.json(productProfile());
      }),
    );
    renderAs("pm", "/products/artifactory");

    expect(await screen.findByText("The product page didn't load.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("Artifactory", { selector: "h1" })).toBeInTheDocument();
  });
});

describe("hiring in its area: which lines are named, and where Other starts", () => {
  /** An area of constant counts: each line [slug, the day its board was first read (days from today), open roles]. */
  function areaOf(lines: [string, number, number][]): HiringArea {
    const days = Array.from({ length: 30 }, (_, i) => localDay(i - 29));
    const by_competitor = days.flatMap((day, i) => lines.filter(([, from]) => i - 29 >= from).map(([key, , count]) => ({ day, key, count })));
    const total = days.map((day) => ({ day, count: by_competitor.filter((p) => p.day === day).reduce((n, p) => n + p.count, 0) }));
    return { capabilities: ["container-registry"], boards: lines.map(([slug, from]) => ({ competitor_slug: slug, board_since: localDay(from), last_listed: localDay(0), stale: false })), total, by_competitor };
  }
  const nameOf = (slug: string) => slug.toUpperCase();

  it("folds two boards of different ages into one Other that starts once both are read: no step on the younger's first day", () => {
    const series = areaSeries(areaOf([["a", -29, 10], ["b", -29, 9], ["c", -29, 8], ["d", -29, 7], ["e", -29, 2], ["f", -5, 1]]), nameOf);
    expect(series.map((s) => s.key)).toEqual(["a", "b", "c", "d", "other"]);
    const other = series.at(-1)!;
    expect(other.members?.map((m) => m.key)).toEqual(["e", "f"]);
    expect(other.points[0]!.x).toEqual(dayDate(localDay(-5)));
    expect(new Set(other.points.map((p) => p.y))).toEqual(new Set([3]));
    // The members keep their own lines for the table twin: E from its first day, F from its own.
    expect(other.members![0]!.points[0]!.x).toEqual(dayDate(localDay(-29)));
  });

  it("names a young board with the most open roles today, first, never folded", () => {
    const series = areaSeries(areaOf([["old1", -29, 5], ["old2", -29, 4], ["old3", -29, 3], ["old4", -29, 2], ["young", -3, 20]]), nameOf);
    expect(series[0]!.key).toBe("young");
    expect(series[0]!.points[0]!.x).toEqual(dayDate(localDay(-3)));
    expect(series.at(-1)!.members?.map((m) => m.key)).toEqual(["old4"]);
  });

  it("draws every line named, with no Other, when four or fewer competitors hire for it", () => {
    const series = areaSeries(areaOf([["a", -29, 3], ["b", -10, 1]]), nameOf);
    expect(series.map((s) => [s.key, s.members])).toEqual([
      ["a", undefined],
      ["b", undefined],
    ]);
  });
});

describe("product chips link to the product page", () => {
  it("from a briefing card: each product tag is its own link beside the card's one link to its analysis", async () => {
    server.use(http.get("/api/v1/briefings/latest", () => HttpResponse.json(briefing({ edition: "master" }))));
    const app = renderAs("analyst", "/briefing");

    const region = await screen.findByRole("region", { name: /top takeaways/i });
    const lead = within(region).getAllByRole("article")[0]!;
    const analysis = within(lead).getByRole("link", { name: "Sonatype makes its dependency firewall part of Nexus One" });
    expect(analysis).toHaveAttribute("href", "/signals/101");
    // Nothing interactive inside the analysis link; the tags are links of their own, never nested in it.
    expect(analysis.querySelectorAll("a, button, input, select, [tabindex]")).toHaveLength(0);
    // The product names link once the API's product list has arrived (GET /intel-reference).
    const tags = await within(within(lead).getByRole("list", { name: "JFrog products affected" })).findAllByRole("link");
    expect(tags.map((t) => t.getAttribute("href"))).toEqual(["/products/curation", "/products/artifactory", "/products/xray"]);
    for (const tag of tags) expect(analysis.contains(tag)).toBe(false);
    expect(tags[0]).toHaveTextContent("CurationThreat");

    await userEvent.click(tags[0]!);
    expect(app.location().pathname).toBe("/products/curation");
  });

  it("from the analysis page: its impact ledger and its side column", async () => {
    serveSignalDetails(analysedSignal());
    const app = renderAs("sales", "/signals/101");
    await screen.findByRole("heading", { level: 1, name: /sonatype bundles repository firewall/i });

    const impact = screen.getAllByRole("heading", { level: 2 }).find((h) => h.textContent === "Impact on JFrog")!.closest("section")!;
    expect(await within(impact).findByRole("link", { name: "Curation" })).toHaveAttribute("href", "/products/curation");
    expect(within(impact).getByRole("listitem", { name: "Curation" })).toHaveTextContent(/high severity/i);
    const side = screen.getByRole("complementary", { name: "About this signal" });
    const products = screen.getAllByRole("heading", { level: 2 }).find((h) => h.textContent === "JFrog products")!.closest("section")!;
    expect(side.contains(products)).toBe(true);
    await userEvent.click(within(products).getByRole("link", { name: "Xray" }));
    expect(app.location().pathname).toBe("/products/xray");
  });
});

// ---- cross-cutting cases ----------------------------------------------------------------

describe("the rules come from the API", () => {
  it("states the thresholds the API serves in the formulas", async () => {
    const base = intelReference();
    serveRules({
      ...base,
      severity_weights: { high: 5, medium: 3, low: 1 },
      overlap: { high_evidence: 8, medium_evidence: 4, lookback_days: 120 },
      threat: { ...base.threat, lookback_days: 120 },
      trend: { ...base.trend, rising_ratio: 2, easing_ratio: 0.7 },
      briefs: { min_signals: 5, lookback_days: 120 },
    });
    await openProduct();
    const pressure = within(section("Pressure over time")).getByText("How this is computed").closest("details")!;
    expect(pressure).toHaveTextContent(/\(high 5, medium 3, low 1\)/);
    expect(pressure).toHaveTextContent(/rising from 2×, easing at 0\.7× or less/);
    const comps = within(section("Competitors")).getByText("How this is computed").closest("details")!;
    expect(comps).toHaveTextContent(/in the last 120 days/);
    expect(comps).toHaveTextContent(/high from 8, medium from 4, low from 1/);
  });

  it("says how many signals a brief needs, as the API serves it", async () => {
    serveRules({ ...intelReference(), briefs: { min_signals: 5, lookback_days: 120 } });
    await openProduct(quietProduct());
    expect(within(section("Bottom line")).getByText("No public signal names Connect in the last 120 days (5 needed).")).toBeInTheDocument();
  });

  it("orders the products within a pressure by the product list the API serves", async () => {
    serveRules(intelReference({ jfrog_products: [...JFROG_PRODUCT_NAMES].reverse() }));
    serveProducts(productSummaries());
    renderAs("sales", "/products");
    const list = await screen.findByRole("list", { name: "JFrog products" });
    const names = within(list)
      .getAllByRole("article")
      .map((r) => within(r).getByRole("heading", { level: 3 }).textContent);
    expect(names.slice(0, 3)).toEqual(["JFrog ML", "Advanced Security", "Artifactory"]);
  });

  it("links only the product names the API lists as JFrog's", async () => {
    serveRules(intelReference({ jfrog_products: JFROG_PRODUCT_NAMES.filter((n) => n !== "Xray") }));
    serveSignalDetails(analysedSignal());
    renderAs("sales", "/signals/101");
    await screen.findByRole("heading", { level: 1, name: /sonatype bundles repository firewall/i });
    const products = [...document.querySelectorAll("h2")].find((h) => h.textContent === "JFrog products")!.closest("section")!;
    expect(await within(products).findByRole("link", { name: "Curation" })).toHaveAttribute("href", "/products/curation");
    expect(within(products).getByText("Xray").closest("a")).toBeNull();
  });
});

describe("the hiring footnote's words", () => {
  const nameOf = (slug: string) => slug.charAt(0).toUpperCase() + slug.slice(1);
  function areaOf(lines: [string, number, number][]): HiringArea {
    const days = Array.from({ length: 30 }, (_, i) => localDay(i - 29));
    const by_competitor = days.flatMap((day, i) => lines.filter(([, from]) => i - 29 >= from).map(([key, , count]) => ({ day, key, count })));
    const total = days.map((day) => ({ day, count: by_competitor.filter((p) => p.day === day).reduce((n, p) => n + p.count, 0) }));
    return { capabilities: ["container-registry"], boards: lines.map(([slug, from]) => ({ competitor_slug: slug, board_since: localDay(from), last_listed: localDay(0), stale: false })), total, by_competitor };
  }

  it("names one folded board in the singular, never 'all 1 boards'", () => {
    const area = areaOf([
      ["alpha", -29, 9],
      ["beta", -29, 8],
      ["gamma", -29, 7],
      ["delta", -29, 6],
      ["epsilon", -5, 1],
    ]);
    const note = areaNote(areaSeries(area, nameOf), ["Container registry"], nameOf, area.total[0]!.day);
    expect(note).toMatch(/the four with the most open roles today are named/);
    expect(note).toMatch(/Other \(1\) is Epsilon, from \d{1,2} \w{3,4}, its board's first snapshot\./);
    expect(note).not.toMatch(/all 1 boards|both/);
  });

  it("says every competitor is named when nothing folds", () => {
    const area = areaOf([
      ["alpha", -29, 3],
      ["beta", -10, 1],
    ]);
    const note = areaNote(areaSeries(area, nameOf), ["Container registry"], nameOf, area.total[0]!.day);
    expect(note).not.toMatch(/the four/);
    expect(note).toMatch(/each competitor hiring for it is named/);
    expect(note).toMatch(/Beta's line starts on \d{1,2} \w{3,4}, its board's first snapshot\./);
  });
});

describe("day 1 of a young deployment", () => {
  it("reads the list with no trend yet: pressure with no baseline, and none since collection began", async () => {
    serveRules(dayOneReference());
    serveProducts(dayOneProducts());
    renderAs("sales", "/products");
    const list = await screen.findByRole("list", { name: "JFrog products" });
    expect(screen.getByText(/^13 JFrog products\. 9 face competitive pressure, with no baseline yet to say whether it is rising or easing; 4 have seen none since \d{1,2} \w{3,4}\.$/)).toBeInTheDocument();
    const rows = within(list).getAllByRole("article");
    expect(within(rows[0]!).getByText("Pressure, no baseline yet")).toHaveAttribute("data-pressure", "baseline");
    expect(within(rows.at(-1)!).getByText(/^No competitive pressure since \d{1,2} \w{3,4}$/)).toHaveAttribute("data-pressure", "none");
    expect(list).not.toHaveTextContent(/Rising pressure|13 weeks/);
    // The portfolio's order within the pressed products: no trend to rank them by.
    expect(within(rows[0]!).getByRole("heading", { level: 3 })).toHaveTextContent("Artifactory");
    expect(screen.getByText(/FrogEye has collected since \d{1,2} \w{3,4}, under the 13 weeks the trend compares, so no product has a pressure trend yet\./)).toBeInTheDocument();
  });

  it("reads a product's pressure over the weeks collected, with no trend word", async () => {
    serveRules(dayOneReference());
    await openProduct(dayOneProduct());
    const header = screen.getByText("Artifactory", { selector: "h1" }).closest("header")!;
    expect(within(header).getByText("Pressure, no baseline yet")).toBeInTheDocument();
    // The chip says "no baseline yet"; the line beside it says why, without repeating it.
    expect(header).toHaveTextContent(/Pressure, no baseline yetFrogEye has collected since \d{1,2} \w{3,4}, under the 13 weeks the trend compares/);

    const figures = within(section("Bottom line")).getByRole("list", { name: "Key figures" });
    const pressure = within(figures).getByText("Threat pressure", { selector: "[data-kpi-label]" }).closest("li")!;
    expect(pressure).toHaveTextContent(/^Threat pressure6\.7 a weekthreat over the 3 weeks collected; no baseline yetSeverity-weighted, since \d{1,2} \w{3,4}$/);

    const over = section("Pressure over time");
    expect(over.querySelector("h2")!.parentElement!).toHaveTextContent(/3 weeks collected, to \d{1,2} \w{3,4}/);
    const chart = within(over).getByRole("figure", { name: "Weekly pressure" });
    expect(chart).toHaveTextContent(/Threat 20 against opportunity 6 over the 3 weeks collected; no baseline yet to call it rising or easing\./);
    expect(chart.querySelectorAll('[data-bar="below"]')).toHaveLength(3);
    // Outside "How this is computed" (whose formula states the rule, 13 weeks included), nothing claims 13 weeks or a trend.
    const page = document.querySelector("article")!.cloneNode(true) as HTMLElement;
    for (const how of page.querySelectorAll("details")) how.remove();
    expect(page).not.toHaveTextContent(/13 weeks to|in 13 weeks|over 13 weeks|: rising\.|in the 9 before/);
  });

  it("starts the area's hiring at today's first listing, one snapshot, said in words", async () => {
    serveRules(dayOneReference());
    await openProduct(dayOneProduct());
    const chart = within(section("Competitor hiring in this area")).getByRole("figure", { name: "Open roles in its area" });
    expect(chart).toHaveTextContent(/29 open roles name its capabilities today, across 6 competitors; Sonatype has the most \(14\)\./);
    // One snapshot is no line yet: today's open roles by competitor, as bars, each named (none folded under four).
    expect(chart.querySelectorAll("[data-line]")).toHaveLength(0);
    expect(within(chart).getAllByRole("img").map((b) => b.getAttribute("aria-label")!.split(":")[0])).toEqual(["Sonatype", "GitHub", "GitLab", "Cloudsmith", "Chainguard", "Docker"]);
    expect(chart).toHaveTextContent(/Every board was first read today, so the line over time starts with the next daily snapshot\./);
    expect(within(section("Signals timeline")).getByRole("figure", { name: "Moves by competitor" })).toHaveTextContent(
      /FrogEye has collected since \d{1,2} \w{3,4} \d{4}; before then the lanes hold only moves its sources still listed when it began\./,
    );
  });
});
