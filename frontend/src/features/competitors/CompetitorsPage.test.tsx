import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { CompanyChips } from "@/components/signal/CompanyChips";
import type { Schemas } from "@/lib/api/client";
import { analysedSignal, competitorOverview, competitorSummary, dayOneOverview, dayOneReference, intelReference, JFROG_PRODUCT_NAMES, localDay, move, quietProduct, sparseOverview, sparseProfile, threat } from "@/test/fixtures";
import { server, serveSignalDetails } from "@/test/msw";
import { renderAs, renderWithProviders, selectOption } from "@/test/render";
import { momentumLevel, sortRows } from "./useCompetitorIntel";

type CompetitorSummary = Schemas["CompetitorSummary"];

/** Serve GET /competitors/overview from `rows`. */
function serveOverview(rows: CompetitorSummary[]) {
  server.use(http.get("/api/v1/competitors/overview", () => HttpResponse.json(rows)));
}

/** The competitors table, named by its section head. */
const table = () => screen.getByRole("table", { name: "All competitors" });

/** The body rows of the table, in screen order. */
function bodyRows() {
  return within(table()).getAllByRole("row").filter((row) => row.querySelector("[data-row-link]"));
}

/** The competitors in the table, in screen order (the name that opens each profile). */
const order = () => bodyRows().map((row) => row.querySelector("[data-row-link]")!.textContent);

/** The table row of the competitor called `name`. */
const rowOf = (name: string) => bodyRows().find((row) => row.querySelector("[data-row-link]")!.textContent === name)!;

const landscape = () => screen.getByRole("figure", { name: "Competitive landscape" });
const thisWeek = () => screen.getByRole("region", { name: "This week" });

async function openPage(rows: CompetitorSummary[] = competitorOverview(), route = "/competitors") {
  serveOverview(rows);
  const app = renderAs("pm", route);
  await screen.findByRole("table", { name: "All competitors" });
  return app;
}

const API_ORDER = ["Sonatype", "GitHub", "GitLab", "Chainguard", "Snyk", "Cloudsmith", "Docker", "AWS", "Socket", "Anchore", "Wiz"];

it("lists every competitor by threat, with what it competes with, momentum, open roles, funding and its latest move", async () => {
  await openPage();

  expect(screen.getByRole("heading", { level: 1, name: "Competitors" })).toBeInTheDocument();
  // The lead says who matters, in counts the table bears out.
  expect(screen.getByText(/11 competitors tracked/)).toHaveTextContent(/2 pose a high threat to JFrog and 3 a medium one/);
  expect(screen.getByText(/11 competitors tracked/)).toHaveTextContent(/3 have too little evidence to score yet/);
  expect(order()).toEqual(API_ORDER);

  const sonatype = rowOf("Sonatype");
  expect(within(sonatype).getByText("Tier 1 · Competitor")).toBeInTheDocument();
  expect(sonatype).toHaveTextContent(/Artifactory.*Xray.*Curation/);
  // Threat as shape and word, its drivers one hover away.
  expect(within(sonatype).getByRole("button", { name: /^high/i })).toBeInTheDocument();
  // Momentum: the 13-week sparkline and the trend printed beside it.
  expect(within(sonatype).getByRole("img", { name: /^Momentum, last 13 weeks: 62 to 131/ })).toBeInTheDocument();
  // the level leads (what the column sorts by), the trend follows.
  expect(within(sonatype).getByText(/^\d+ a week$/)).toBeInTheDocument();
  expect(within(sonatype).getByText("1.6× · rising")).toBeInTheDocument();
  // Open roles with the 30-day change, by sign and word.
  expect(within(sonatype).getByText("142")).toBeInTheDocument();
  expect(within(sonatype).getByText(/\+12 in 30 days/)).toBeInTheDocument();
  expect(within(sonatype).getByText("$154M")).toBeInTheDocument();
  expect(within(sonatype).getByText("4 rounds · latest Mar 2024")).toBeInTheDocument(); // how current
  // The latest move opens its analysis.
  expect(within(sonatype).getByRole("link", { name: "Sonatype bundles Repository Firewall into every Nexus One plan" })).toHaveAttribute(
    "href",
    "/signals/9101",
  );

  // A total with a round it cannot count is a lower bound, and says what it leaves out.
  const snyk = rowOf("Snyk");
  expect(within(snyk).getByText("≥ $1.1B")).toBeInTheDocument();
  expect(within(snyk).getByText(/9 rounds, incl\. 1 undated · 1 not counted/)).toBeInTheDocument();
  // A public company with no reported round reads "Not reported", never $0.
  expect(within(rowOf("GitHub")).getByText("Not reported")).toBeInTheDocument();
  // No baseline is said, not drawn as 1.0×.
  expect(within(rowOf("Docker")).getByText("no baseline yet")).toBeInTheDocument();
  expect(within(rowOf("Docker")).getByText("No change in 30 days")).toBeInTheDocument();
});

it("says a board read for under 30 days has no 30-day change yet, never its whole count as growth", async () => {
  const rows = competitorOverview();
  rows[0] = { ...rows[0]!, delta_30d: null, board_since: localDay(-10) };
  await openPage(rows);
  const sonatype = rowOf("Sonatype");
  expect(within(sonatype).getByText("142")).toBeInTheDocument();
  expect(within(sonatype).getByText("No 30-day change yet")).toBeInTheDocument();
  expect(sonatype).not.toHaveTextContent(/in 30 days/);
});

it("says plainly when a competitor has no board, a pending board or too little evidence, and never shows 0 for it", async () => {
  await openPage();

  // no board, or a board found but never fetched: words, not 0.
  expect(within(rowOf("GitHub")).getByText("No public Greenhouse, Lever or Ashby board found")).toBeInTheDocument();
  expect(within(rowOf("Cloudsmith")).getByText("Job board found — first snapshot pending")).toBeInTheDocument();

  // Insufficient: the threat cell and the sparkline say so, and nothing reads as measured.
  const socket = rowOf("Socket");
  expect(within(socket).getByRole("button", { name: /insufficient data/i })).toBeInTheDocument();
  expect(within(socket).getByRole("img", { name: "Momentum, last 13 weeks: Insufficient data" })).toBeInTheDocument();
  expect(within(socket).getByText("No public Greenhouse, Lever or Ashby board found")).toBeInTheDocument();
  expect(within(socket).queryByText(/^0$|^0\.0|^\$0/)).not.toBeInTheDocument();
  const wiz = rowOf("Wiz");
  expect(within(wiz).getByText("Job board found — first snapshot pending")).toBeInTheDocument();
  expect(within(wiz).getByText("No public signals in 180 days")).toBeInTheDocument();

  // The threat's drivers, in its own words, on hover.
  await userEvent.hover(within(socket).getByRole("button", { name: /insufficient data/i }));
  expect(await screen.findByRole("tooltip")).toHaveTextContent(/1 visible signal in the last 180 days \(3 needed\)/);
});

it("sorts by threat, momentum and open roles, unmeasured values last in either direction", async () => {
  const app = await openPage();
  const head = (name: RegExp) => within(table()).getByRole("columnheader", { name });

  expect(head(/threat/i)).toHaveAttribute("aria-sort", "descending");
  expect(order()).toEqual(API_ORDER);

  await userEvent.click(within(head(/momentum/i)).getByRole("button"));
  expect(head(/momentum/i)).toHaveAttribute("aria-sort", "descending");
  expect(head(/threat/i)).not.toHaveAttribute("aria-sort");
  // by the momentum level (the landscape's y), then the trend; unmeasured last either way.
  const rows = competitorOverview();
  const level = (r: CompetitorSummary) => momentumLevel(r, intelReference().trend);
  const measured = rows.filter((r) => level(r) !== null);
  const unmeasured = rows.filter((r) => level(r) === null).map((r) => r.name);
  const byLevel = (sign: number) =>
    [...measured].sort((a, b) => sign * (level(a)! - level(b)! || (a.threat.momentum_trend ?? 0) - (b.threat.momentum_trend ?? 0))).map((r) => r.name);
  expect(order()).toEqual([...byLevel(-1), ...unmeasured]);
  expect(app.location().search).toBe("?sort=momentum");

  await userEvent.click(within(head(/momentum/i)).getByRole("button"));
  expect(head(/momentum/i)).toHaveAttribute("aria-sort", "ascending");
  expect(order()).toEqual([...byLevel(1), ...unmeasured]);

  await userEvent.click(within(head(/open roles/i)).getByRole("button"));
  expect(order()).toEqual(["GitLab", "Sonatype", "Snyk", "Chainguard", "Docker", "GitHub", "Cloudsmith", "AWS", "Socket", "Anchore", "Wiz"]);

  await userEvent.click(within(head(/threat/i)).getByRole("button"));
  expect(order()).toEqual(API_ORDER);
  await userEvent.click(within(head(/threat/i)).getByRole("button"));
  expect(head(/threat/i)).toHaveAttribute("aria-sort", "ascending");
  expect(order()).toEqual(["AWS", "Docker", "Cloudsmith", "Snyk", "Chainguard", "GitLab", "GitHub", "Sonatype", "Socket", "Anchore", "Wiz"]);
});

it("keeps the sort in the address, so a shared link opens the same view", async () => {
  await openPage(competitorOverview(), "/competitors?sort=open_roles");
  expect(within(table()).getByRole("columnheader", { name: /open roles/i })).toHaveAttribute("aria-sort", "descending");
  expect(order().slice(0, 3)).toEqual(["GitLab", "Sonatype", "Snyk"]);
});

it("filters the landscape and the table by tier and JFrog product, and clears back to everyone", async () => {
  const app = await openPage();

  await selectOption("Tier", "Tier 2");
  expect(order()).toEqual(["Chainguard", "Docker", "AWS", "Socket"]);
  expect(app.location().search).toBe("?tier=2");
  // The landscape draws the same slice: every scored competitor; Socket has too little evidence.
  expect(within(landscape()).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
    "/competitors/chainguard",
    "/competitors/docker",
    "/competitors/aws",
  ]);

  await selectOption("JFrog product", /^Curation/);
  expect(order()).toEqual(["Chainguard", "Socket"]);
  expect(new URLSearchParams(app.location().search).get("product")).toBe("curation");
  expect(screen.getByText(/2 of 11/)).toBeInTheDocument();

  // Nothing left: the table says so and offers the way back.
  await selectOption("Tier", "Tier 3");
  expect(screen.getByText("No competitor matches these filters")).toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: /clear filters/i })[0]!);
  expect(order()).toEqual(API_ORDER);
  expect(app.location().search).toBe("");
});

it("offers only the JFrog products someone competes with, in the product list's order", async () => {
  await openPage();
  await userEvent.click(screen.getByRole("combobox", { name: "JFrog product" }));
  const options = screen.getAllByRole("option").map((o) => o.textContent);
  expect(options).toEqual(["All JFrog products", "Artifactory", "Xray", "Advanced Security", "Curation", "Runtime", "Container Registry", "Platform"]);
});

it("opens a competitor's profile from its row and from its bubble", async () => {
  const app = await openPage();
  // Any competitor's deep dive answers with a profile under its own name.
  server.use(
    http.get("/api/v1/competitors/:slug/profile", ({ params }) => {
      const profile = sparseProfile();
      const name = competitorOverview().find((r) => r.slug === params.slug)?.name ?? String(params.slug);
      return HttpResponse.json({ ...profile, competitor: { ...profile.competitor, slug: String(params.slug), name } });
    }),
  );

  await userEvent.click(within(rowOf("Snyk")).getByText("Snyk"));
  // the deep dive's route loads lazily, so the navigation completes once its code is in.
  await waitFor(() => expect(app.location().pathname).toBe("/competitors/snyk"));
  expect(await screen.findByRole("heading", { level: 1, name: "Snyk" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("link", { name: /all competitors/i }));
  const bubble = await within(await screen.findByRole("figure", { name: "Competitive landscape" })).findByRole("link", { name: /^Sonatype,/ });
  await userEvent.click(bubble);
  await waitFor(() => expect(app.location().pathname).toBe("/competitors/sonatype"));
});

it("plots every scored competitor and lists only those with too little evidence as not plotted", async () => {
  await openPage();
  const fig = landscape();

  // x overlap, y momentum level (the mean of the last 4 weekly values), area open roles.
  expect(within(fig).getAllByRole("link")).toHaveLength(8);
  expect(within(fig).getByRole("link", { name: /^Sonatype, Overlap with JFrog 11, Weekly momentum 111, Open roles 142, Tier 1/ })).toBeInTheDocument();
  // Docker has no trend baseline yet, but its level is measured: it is placed.
  expect(within(fig).getByRole("link", { name: /^Docker, Overlap with JFrog 2, Weekly momentum 31, Open roles 41, Tier 2/ })).toBeInTheDocument();
  // Without an open-roles count it is placed but drawn hollow, saying why: no board, or a pending one.
  expect(within(fig).getByRole("link", { name: /^GitHub, .*Open roles no job board/ })).toBeInTheDocument();
  expect(within(fig).getByRole("link", { name: /^Cloudsmith, .*Open roles first snapshot pending/ })).toBeInTheDocument();
  expect(within(fig).getByText("Sonatype and GitHub overlap most with JFrog; GitHub is the most active, at a weekly momentum of 117.")).toBeInTheDocument();
  expect(within(fig).getByText("High overlap, active")).toBeInTheDocument();
  expect(within(fig).getByText("Low overlap, quiet")).toBeInTheDocument();

  // The trend stays one focus away, beside the level it qualifies.
  act(() => within(fig).getByRole("link", { name: /^Sonatype,/ }).focus());
  expect(within(fig).getByRole("tooltip")).toHaveTextContent(/trend 1\.6×, rising/);
  act(() => within(fig).getByRole("link", { name: /^Docker,/ }).focus());
  expect(within(fig).getByRole("tooltip")).toHaveTextContent(/no trend baseline yet/);

  const notPlotted = screen.getByRole("list", { name: "Not plotted" });
  expect(within(notPlotted).getByText("Insufficient data")).toBeInTheDocument();
  expect(within(notPlotted).queryByText("No momentum baseline yet")).not.toBeInTheDocument();
  expect(within(notPlotted).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/competitors/socket", "/competitors/anchore", "/competitors/wiz"]);
});

it("places every scored competitor of a young collection, Docker included", async () => {
  await openPage(sparseOverview());
  expect(within(landscape()).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
    "/competitors/sonatype",
    "/competitors/github",
    "/competitors/docker",
  ]);
  const notPlotted = screen.getByRole("list", { name: "Not plotted" });
  expect(within(notPlotted).getAllByRole("link")).toHaveLength(5);
});

it("drops an unknown tier or JFrog product from the address and shows everyone", async () => {
  const app = await openPage(competitorOverview(), "/competitors?tier=9&product=astrology&sort=momentum");
  expect(order()).toHaveLength(11);
  expect(screen.queryByText("No competitor matches these filters")).not.toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Tier" })).toHaveTextContent("All tiers");
  expect(screen.getByRole("combobox", { name: "JFrog product" })).toHaveTextContent("All JFrog products");
  await waitFor(() => expect(app.location().search).toBe("?sort=momentum"));
});

it("reverses the order from beside the sort select, where the column heads are hidden", async () => {
  const app = await openPage();
  await userEvent.click(screen.getByRole("button", { name: "Order: highest first" }));
  expect(app.location().search).toBe("?dir=asc");
  expect(order()).toEqual(["AWS", "Docker", "Cloudsmith", "Snyk", "Chainguard", "GitLab", "GitHub", "Sonatype", "Socket", "Anchore", "Wiz"]);
  expect(screen.getByRole("button", { name: "Order: lowest first" })).toBeInTheDocument();

  await selectOption("Sort by", "Sort by name");
  expect(screen.getByRole("button", { name: "Order: A to Z" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Order: A to Z" }));
  expect(app.location().search).toBe("?sort=name&dir=desc");
  expect(order()[0]).toBe("Wiz");
});

it("keeps the table's roles explicit, so the reflowed rows still read as a table", async () => {
  await openPage();
  expect(table()).toHaveAttribute("role", "table");
  for (const group of table().querySelectorAll("thead, tbody")) expect(group).toHaveAttribute("role", "rowgroup");
  for (const row of table().querySelectorAll("tr")) expect(row).toHaveAttribute("role", "row");
  for (const head of table().querySelectorAll("th")) expect(head).toHaveAttribute("role", "columnheader");
  for (const cell of table().querySelectorAll("td")) expect(cell).toHaveAttribute("role", "cell");
});

describe("sortRows", () => {
  it("ranks the threat level before the score, whatever the score's size", () => {
    const low = competitorSummary({ slug: "big", name: "Big", threat: threat("low", { score: 25_000 }) });
    const high = competitorSummary({ slug: "small", name: "Small", threat: threat("high", { score: 1 }) });
    expect(sortRows([low, high], "threat", "desc", intelReference().trend).map((r) => r.slug)).toEqual(["small", "big"]);
    expect(sortRows([low, high], "threat", "asc", intelReference().trend).map((r) => r.slug)).toEqual(["big", "small"]);
  });
});

it("shows the week's biggest moves, each opening its analysis", async () => {
  const app = await openPage();
  const strip = thisWeek();

  // The latest move of each competitor in the last 7 days, most important first, five at most.
  const moves = within(strip).getAllByRole("link");
  expect(moves.map((a) => a.getAttribute("href"))).toEqual(["/signals/9101", "/signals/9102", "/signals/9105", "/signals/9107", "/signals/9104"]);
  expect(moves[0]).toHaveAccessibleName("Sonatype bundles Repository Firewall into every Nexus One plan");
  expect(moves[0]).toHaveTextContent(/Sonatype/);
  expect(moves[0]).toHaveTextContent(/Pricing/);
  expect(moves[0]).toHaveTextContent(/Importance\s*88/);
  // Older moves stay in the table; the sixth of the week waits for the strip to have room.
  expect(within(strip).queryByText(/GitLab tests a dependency proxy/)).not.toBeInTheDocument();
  expect(within(strip).queryByText(/Cloudsmith launches/)).not.toBeInTheDocument();

  await userEvent.click(moves[2]!);
  expect(app.location().pathname).toBe("/signals/9105");
});

it('says "No major moves this week" when nobody moved in the last 7 days', async () => {
  await openPage([
    competitorSummary({ slug: "gitlab", name: "GitLab", latest_move: move({ day: localDay(-7), title: "GitLab tests a dependency proxy for Maven" }) }),
    competitorSummary({ slug: "docker", name: "Docker", tier: 2, latest_move: null }),
  ]);
  expect(within(thisWeek()).getByText("No major moves this week")).toBeInTheDocument();
  expect(within(thisWeek()).queryAllByRole("link")).toHaveLength(0);
});

it("reads honestly before anything is collected", async () => {
  // The default handler: every seeded competitor, none scored, no board, no move.
  renderAs("pm", "/competitors");
  await screen.findByRole("table", { name: "All competitors" });

  expect(order()).toEqual(["Sonatype", "GitHub", "GitLab", "Docker"]);
  expect(within(landscape()).getByText("Not enough evidence to plot the landscape yet")).toBeInTheDocument();
  expect(within(thisWeek()).getByText("No major moves this week")).toBeInTheDocument();
  for (const row of bodyRows()) {
    expect(within(row).getByText("No public Greenhouse, Lever or Ashby board found")).toBeInTheDocument();
    expect(within(row).getByRole("img", { name: "Momentum, last 13 weeks: Insufficient data" })).toBeInTheDocument();
  }
});

it("says when the overview didn't load and retries", async () => {
  let calls = 0;
  server.use(
    http.get("/api/v1/competitors/overview", () => {
      calls += 1;
      return calls === 1 ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : HttpResponse.json(competitorOverview());
    }),
  );
  renderAs("pm", "/competitors");
  expect(await screen.findByText("The competitors didn't load.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByRole("table", { name: "All competitors" })).toBeInTheDocument();
});

describe("navigation", () => {
  it("lists Competitors and Products in the main navigation, and both open", async () => {
    serveOverview(competitorOverview());
    const app = renderAs("sales", "/briefing");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Briefing",
      "Signals",
      "Competitors",
      "Products",
      "Compare",
      "Feed",
      "Sources",
      "Runs",
      "Agent runs",
      "Evals",
    ]);

    await userEvent.click(within(nav).getByRole("link", { name: "Competitors" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Competitors" })).toBeInTheDocument();
    expect(app.location().pathname).toBe("/competitors");

    await userEvent.click(within(nav).getByRole("link", { name: "Products" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Products" })).toBeInTheDocument();
    expect(app.location().pathname).toBe("/products");
  });

  it("opens a product's deep dive at its route (it replaced the placeholder)", async () => {
    server.use(http.get("/api/v1/products/jfrog-ml", () => HttpResponse.json({ ...quietProduct(), slug: "jfrog-ml", name: "JFrog ML" })));
    renderAs("pm", "/products/jfrog-ml");
    expect(await screen.findByRole("heading", { level: 1, name: "JFrog ML" })).toBeInTheDocument();
  });
});

describe("competitor chips", () => {
  it("link to the competitor's profile, except JFrog itself", async () => {
    const app = renderWithProviders(<CompanyChips slugs={["sonatype", "jfrog"]} />);
    const chip = await screen.findByRole("link", { name: "Sonatype" });
    expect(chip).toHaveAttribute("href", "/competitors/sonatype");
    expect((await screen.findByText("JFrog")).closest("a")).toBeNull();
    await userEvent.click(chip);
    expect(app.location().pathname).toBe("/competitors/sonatype");
  });

  it("stay filter buttons where they filter, and plain text inside a link", async () => {
    renderWithProviders(
      <>
        <CompanyChips slugs={["sonatype", "github"]} onSelect={() => {}} active="github" />
        <CompanyChips slugs={["gitlab"]} plain />
      </>,
    );
    expect(await screen.findByRole("button", { name: "Show only Sonatype" })).toBeInTheDocument();
    // The company already filtered to is neither a filter nor a link beside the filters.
    expect((await screen.findByText("GitHub")).closest("a, button")).toBeNull();
    expect(screen.getByText("GitLab").closest("a, button")).toBeNull();
  });

  it("open the profile from a signal's analysis page", async () => {
    serveSignalDetails(analysedSignal());
    const app = renderAs("sales", "/signals/101");
    await screen.findByRole("heading", { level: 1, name: /sonatype bundles repository firewall/i });
    const header = screen.getByRole("heading", { level: 1 }).closest("header")!;
    await userEvent.click(within(header).getByRole("link", { name: "Sonatype" }));
    expect(app.location().pathname).toBe("/competitors/sonatype");
  });
});

/** Serve GET /intel-reference from `rules` (the pages' thresholds and the collected-since day). */
function serveRules(rules: Schemas["IntelReferenceOut"]) {
  server.use(http.get("/api/v1/intel-reference", () => HttpResponse.json(rules)));
}

describe("the rules come from the API", () => {
  it("words each momentum trend by the thresholds the API serves, not a copy", async () => {
    const rules = intelReference();
    serveRules({ ...rules, trend: { ...rules.trend, rising_ratio: 2, easing_ratio: 0.5 } });
    await openPage();
    // 1.6× is "rising" from 1.25× but "steady" when the API rises from 2×; 2.4× still rises; 0.78× no longer eases.
    expect(within(rowOf("Sonatype")).getByText("1.6× · steady")).toBeInTheDocument();
    expect(within(rowOf("Snyk")).getByText(/· rising$/)).toBeInTheDocument();
    expect(within(rowOf("Cloudsmith")).getByText(/· steady$/)).toBeInTheDocument();
    act(() => within(landscape()).getByRole("link", { name: /^Sonatype,/ }).focus());
    expect(within(landscape()).getByRole("tooltip")).toHaveTextContent(/trend 1\.6×, steady/);
  });

  it("orders the JFrog product filter by the product list the API serves", async () => {
    serveRules(intelReference({ jfrog_products: [...JFROG_PRODUCT_NAMES].reverse() }));
    await openPage();
    await userEvent.click(screen.getByRole("combobox", { name: "JFrog product" }));
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["All JFrog products", "Platform", "Container Registry", "Runtime", "Curation", "Advanced Security", "Xray", "Artifactory"]);
  });

  it("says the landscape's y axis is the weekly momentum", async () => {
    await openPage();
    expect([...landscape().querySelectorAll("svg text")].map((t) => t.textContent)).toContain("Weekly momentum");
    expect(within(landscape()).getByRole("link", { name: /^Sonatype, Overlap with JFrog 11, Weekly momentum 111,/ })).toBeInTheDocument();
  });
});

describe("the category filter", () => {
  it("narrows the landscape and the table to the competitors with a product in that category, in the address", async () => {
    const app = await openPage();
    await userEvent.click(screen.getByRole("combobox", { name: "Category" }));
    // Every category some competitor's products are in, A to Z.
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "All categories",
      "Artifact repository",
      "Code security",
      "Container registry",
      "Dependency firewall",
      "Hardened images",
      "Runtime security",
      "SBOM management",
      "Software composition analysis",
    ]);
    await userEvent.click(screen.getByRole("option", { name: "Hardened images" }));
    expect(order()).toEqual(["Chainguard", "Docker"]);
    expect(app.location().search).toBe("?category=hardened-images");
    expect(within(landscape()).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/competitors/chainguard", "/competitors/docker"]);
    expect(screen.getByText(/2 of 11/)).toBeInTheDocument();

    // It combines with the other filters, and Clear filters clears all three.
    await selectOption("Tier", "Tier 2");
    expect(order()).toEqual(["Chainguard", "Docker"]);
    await selectOption("JFrog product", /^Curation/);
    expect(order()).toEqual(["Chainguard"]);
    await userEvent.click(screen.getByRole("button", { name: /clear filters/i }));
    expect(order()).toEqual(API_ORDER);
    expect(app.location().search).toBe("");
  });

  it("opens a shared link on its category, and reads an unknown one as All", async () => {
    await openPage(competitorOverview(), "/competitors?category=sbom-management");
    expect(order()).toEqual(["Sonatype", "Anchore"]);
    expect(screen.getByRole("combobox", { name: "Category" })).toHaveTextContent("SBOM management");
  });

  it("drops a category the data cannot match from the address", async () => {
    const app = await openPage(competitorOverview(), "/competitors?category=quantum-builds");
    expect(order()).toEqual(API_ORDER);
    await waitFor(() => expect(app.location().search).toBe(""));
  });
});

describe("day 1 of a young deployment", () => {
  async function openDayOne() {
    serveRules(dayOneReference());
    return openPage(dayOneOverview());
  }

  it("starts each sparkline at the first collected week and says every trend has no baseline yet", async () => {
    await openDayOne();
    const sonatype = rowOf("Sonatype");
    expect(within(sonatype).getByRole("img", { name: /^Momentum, last 3 weeks: 112 to 131/ })).toBeInTheDocument();
    for (const row of bodyRows().filter((r) => r.querySelector('[data-col="momentum"] svg'))) {
      expect(within(row).getByText("no baseline yet")).toBeInTheDocument();
    }
    expect(table()).not.toHaveTextContent(/rising|easing|steady|13 weeks/);
  });

  it("says once, under the table, why no trend reads yet and when the first can", async () => {
    await openDayOne();
    const note = screen.getByText(/FrogEye has collected since/);
    expect(note).toHaveTextContent(/^Momentum trends compare 13 collected weeks\. FrogEye has collected since \d{1,2} \w{3,4}, so every trend reads “no baseline yet” until \d{1,2} \w{3,4} \d{4} at the earliest\.$/);
  });

  it("places every scored competitor by its mean over the weeks collected, and says how many", async () => {
    await openDayOne();
    const fig = landscape();
    // Sonatype's 3 collected weeks: 112, 104, 131 → 116.
    expect(within(fig).getByRole("link", { name: /^Sonatype, Overlap with JFrog 11, Weekly momentum 116,/ })).toBeInTheDocument();
    expect(fig).toHaveTextContent(/mean weekly signal importance over the 3 weeks collected/);
    expect(fig).not.toHaveTextContent(/last 4 weeks|the 9 weeks before/);
    act(() => within(fig).getByRole("link", { name: /^Sonatype,/ }).focus());
    expect(within(fig).getByRole("tooltip")).toHaveTextContent(/no trend baseline yet/);
  });
});

describe("the contract check (D30)", () => {
  it("says why a competitor has no open-roles count in the API's own words (open_roles_note)", async () => {
    const rows = competitorOverview();
    const said = "Job board found — not listed in full yet";
    await openPage(rows.map((r) => (r.slug === "cloudsmith" ? { ...r, open_roles_note: said } : r)));
    expect(within(rowOf("Cloudsmith")).getByText(said)).toBeInTheDocument();
    expect(within(rowOf("GitHub")).getByText("No public Greenhouse, Lever or Ashby board found")).toBeInTheDocument();
  });

  it("says when a young board was first read, from the overview's board_since", async () => {
    serveRules(dayOneReference());
    await openPage(dayOneOverview());
    const sonatype = rowOf("Sonatype");
    expect(within(sonatype).getByText("No 30-day change yet")).toBeInTheDocument();
    expect(within(sonatype).getByText("board first read today")).toBeInTheDocument();
  });
});
