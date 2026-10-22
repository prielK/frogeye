import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { Schemas } from "@/lib/api/client";
import { searchHit, signalCard, signalDetail } from "@/test/fixtures";
import { server } from "@/test/msw";
import { renderAs, selectOption } from "@/test/render";

type SignalCard = Schemas["SignalCard"];
type SignalPage = Schemas["SignalPage"];

const page = (items: SignalCard[], next_cursor: string | null = null, search_mode: SignalPage["search_mode"] = null): SignalPage => ({
  items,
  next_cursor,
  search_mode,
});

/** Serve GET /signals from `answer`, recording every request URL in `seen`. */
function serveSignals(answer: (url: URL) => SignalPage, seen: URL[] = []) {
  server.use(
    http.get("/api/v1/signals", ({ request }) => {
      const url = new URL(request.url);
      seen.push(url);
      return HttpResponse.json(answer(url));
    }),
  );
  return seen;
}

/** Serve each card's detail from GET /signals/:id. */
function serveDetails(cards: SignalCard[]) {
  server.use(
    http.get("/api/v1/signals/:id", ({ params }) => {
      const card = cards.find((c) => c.id === Number(params.id));
      return card ? HttpResponse.json(signalDetail({ ...card })) : HttpResponse.json({ detail: "Signal not found" }, { status: 404 });
    }),
  );
}

const FIREWALL = signalCard({
  id: 1,
  title: "Sonatype bundles Repository Firewall into Nexus One",
  importance: 88,
  stance: "threat",
  signal_type: "pricing_change",
  grade: "B1",
  reliability: "B",
  credibility: 1,
  status: "approved",
  competitors: ["sonatype"],
  primary_source: "Sonatype blog",
});
const MAVEN = signalCard({ id: 2, title: "GitLab tests a dependency proxy for Maven", importance: 47, competitors: ["gitlab"] });

it("lists signals by importance with their trust line, and loads the next page from the cursor", async () => {
  const seen = serveSignals((url) => (url.searchParams.get("cursor") === "page-2" ? page([MAVEN]) : page([FIREWALL], "page-2")));
  renderAs("pm", "/signals");

  const row = await screen.findByRole("article", { name: /sonatype bundles repository firewall/i });
  expect(row).toHaveTextContent(/importance\s*88/i);
  expect(within(row).getByText("Threat")).toBeInTheDocument();
  expect(within(row).getByText("Pricing change")).toBeInTheDocument();
  expect(within(row).getByRole("button", { name: /admiralty grade b1/i })).toBeInTheDocument();
  expect(within(row).getByText(/analyst approved/i)).toBeInTheDocument();
  expect(within(row).getByText("Sonatype")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /load more/i }));
  expect(await screen.findByRole("article", { name: /gitlab tests a dependency proxy/i })).toBeInTheDocument();
  expect(seen.map((u) => u.searchParams.get("cursor"))).toEqual([null, "page-2"]);
  expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
});

it("debounces the search box into one unpaged q request", async () => {
  const seen = serveSignals((url) =>
    url.searchParams.get("q")
      ? page([signalCard({ title: "GitLab ships a model registry" })], "never-followed", "hybrid")
      : page([FIREWALL], "page-2"),
  );
  renderAs("sales", "/signals");
  await screen.findByRole("article", { name: /sonatype bundles/i });

  // De-flaked: the keys go in with no timer between them, so the 300ms debounce can
  // only fire once the word is typed, however busy the machine. Before, each keystroke waited a
  // timer, and under load a gap past 300ms sent a half-typed term ("re", "reg"). With that fixed the
  // default 1s find window holds again: the debounce and one answer fit in it.
  const user = userEvent.setup({ delay: null });
  await user.type(screen.getByRole("searchbox", { name: /search signals/i }), "registry");
  expect(await screen.findByRole("article", { name: /gitlab ships a model registry/i })).toBeInTheDocument();
  // One request for the list and one for the settled term, none per keystroke.
  expect(seen.map((u) => u.searchParams.get("q"))).toEqual([null, "registry"]);
  // Search is the top matches in one page: no paging, even if a cursor came back.
  expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
  expect(seen.every((u) => !u.searchParams.has("cursor"))).toBe(true);
  expect(screen.queryByText(/keyword results/i)).not.toBeInTheDocument();
});

it("labels keyword-only search results quietly", async () => {
  serveSignals((url) => (url.searchParams.get("q") ? page([MAVEN], null, "keyword") : page([FIREWALL])));
  renderAs("pm", "/signals?q=maven");
  expect(await screen.findByRole("article", { name: /gitlab tests a dependency proxy/i })).toBeInTheDocument();
  expect(screen.getByText("Keyword results — AI search unavailable")).toBeInTheDocument();
});

it("treats /signals/:id as the signal's full analysis page, not the drawer", async () => {
  const docker = signalCard({ id: 123, title: "Docker Hardened Images reach general availability", competitors: ["docker"] });
  serveSignals(() => page([FIREWALL, docker]));
  serveDetails([FIREWALL, docker]);
  const app = renderAs("sales", "/signals/123");

  expect(await screen.findByRole("heading", { level: 1, name: /docker hardened images reach general availability/i })).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(app.location()).toMatchObject({ pathname: "/signals/123", search: "" });
  // Arrived from outside the app: the way back is the Explorer.
  expect(screen.getByRole("link", { name: /all signals/i })).toHaveAttribute("href", "/signals");
});

it("says what the Explorer is for, in one line", async () => {
  serveSignals(() => page([FIREWALL]));
  renderAs("pm", "/signals");
  expect(await screen.findByText("Every analysed story, searchable. The Briefing is drawn from these.")).toBeInTheDocument();
});

it("searches everything: mixed results with kind badges, links out, and signals open in the drawer", async () => {
  const searches: URL[] = [];
  server.use(
    http.get("/api/v1/search", ({ request }) => {
      searches.push(new URL(request.url));
      return HttpResponse.json({
        plan: null,
        hits: [
          searchHit({ kind: "article_chunk", ref_id: 9002, title: "Sonatype folds its firewall into Nexus One", url: "https://devops.com/sonatype-nexus-one" }),
          searchHit({ kind: "signal", ref_id: 1, title: FIREWALL.title, url: "/signals/1" }),
          searchHit({ kind: "jfrog_kb", ref_id: 31, title: "JFrog Curation overview", url: "https://jfrog.com/help/curation", competitors: [] }),
        ],
      });
    }),
  );
  const signals = serveSignals(() => page([FIREWALL]));
  serveDetails([FIREWALL]);
  const app = renderAs("pm", "/signals");
  await screen.findByRole("article", { name: /sonatype bundles/i });

  await userEvent.click(screen.getByRole("switch", { name: /search everything/i }));
  await userEvent.type(screen.getByRole("searchbox", { name: /search articles, signals and jfrog docs/i }), "firewall");

  const results = await screen.findByRole("list", { name: /search results/i });
  expect(searches.at(-1)!.searchParams.get("q")).toBe("firewall");
  expect(within(results).getByText("Article")).toBeInTheDocument();
  expect(within(results).getByText("Signal")).toBeInTheDocument();
  expect(within(results).getByText("JFrog docs")).toBeInTheDocument();
  const article = within(results).getByRole("link", { name: /sonatype folds its firewall into nexus one/i });
  expect(article).toHaveAttribute("href", "https://devops.com/sonatype-nexus-one");
  expect(article).toHaveAttribute("target", "_blank");
  // Search everything never asks /signals for q.
  expect(signals.some((u) => u.searchParams.has("q"))).toBe(false);

  await userEvent.click(within(results).getByRole("button", { name: /sonatype bundles repository firewall/i }));
  expect(await screen.findByRole("dialog", { name: /sonatype bundles repository firewall/i })).toBeInTheDocument();
  expect(new URLSearchParams(app.location().search).get("signal")).toBe("1");
});

it("notes when AI search is unavailable (503) and offers signal search instead", async () => {
  server.use(http.get("/api/v1/search", () => HttpResponse.json({ detail: "AI search needs OPENAI_API_KEY" }, { status: 503 })));
  const seen = serveSignals((url) => (url.searchParams.get("q") ? page([FIREWALL], null, "keyword") : page([MAVEN])));
  renderAs("exec", "/signals?mode=everything&q=firewall");

  expect(await screen.findByText(/^AI search unavailable/i)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /search signals instead/i }));
  expect(screen.getByRole("switch", { name: /search everything/i })).not.toBeChecked();
  expect(await screen.findByRole("article", { name: /sonatype bundles/i })).toBeInTheDocument();
  expect(seen.at(-1)!.searchParams.get("q")).toBe("firewall");
});

it("labels keyword-only results in Search everything when AI search could not run", async () => {
  server.use(
    http.get("/api/v1/search", () =>
      HttpResponse.json({ plan: null, hits: [searchHit({ title: "Harness CI adds a firewall" })], search_mode: "keyword" }),
    ),
  );
  serveSignals(() => page([FIREWALL]));
  renderAs("pm", "/signals?mode=everything&q=firewall");

  expect(await screen.findByRole("link", { name: /harness ci adds a firewall/i })).toBeInTheDocument();
  expect(screen.getByText(/Keyword results — AI search unavailable/)).toBeInTheDocument();
  expect(screen.queryByText(/^AI search unavailable/)).not.toBeInTheDocument(); // not the 503 state
});

it("moves between rows with j and k, and Enter opens the drawer", async () => {
  const cards = [FIREWALL, MAVEN, signalCard({ id: 3, title: "Docker raises Hub pull limits", importance: 30 })];
  serveSignals(() => page(cards));
  serveDetails(cards);
  const app = renderAs("pm", "/signals");
  await screen.findByRole("article", { name: /sonatype bundles/i });
  const title = (name: RegExp) => screen.getByRole("button", { name });

  await userEvent.keyboard("j");
  expect(title(/sonatype bundles/i)).toHaveFocus();
  await userEvent.keyboard("j");
  expect(title(/gitlab tests a dependency proxy/i)).toHaveFocus();
  await userEvent.keyboard("k");
  expect(title(/sonatype bundles/i)).toHaveFocus();
  await userEvent.keyboard("jjjj");
  expect(title(/docker raises hub pull limits/i)).toHaveFocus();

  await userEvent.keyboard("{Enter}");
  expect(await screen.findByRole("dialog", { name: /docker raises hub pull limits/i })).toBeInTheDocument();
  expect(app.location().search).toBe("?signal=3");
});

it("leaves j and k to the search box while typing", async () => {
  serveSignals(() => page([FIREWALL, MAVEN]));
  renderAs("pm", "/signals");
  await screen.findByRole("article", { name: /sonatype bundles/i });
  const box = screen.getByRole("searchbox", { name: /search signals/i });
  await userEvent.type(box, "jk");
  expect(box).toHaveFocus();
  expect(box).toHaveValue("jk");
});

it("sends the facets as API parameters, from the reference endpoints", async () => {
  const seen = serveSignals(() => page([FIREWALL]));
  renderAs("pm", "/signals");
  await screen.findByRole("article", { name: /sonatype bundles/i });

  await selectOption(/^competitor$/i, "Sonatype");
  await selectOption(/^signal type$/i, "Pricing change");
  await selectOption(/^stance$/i, /threat/i);
  await selectOption(/^minimum importance$/i, /importance 75\+/i);
  await selectOption(/^updated$/i, /past 7 days/i);
  await userEvent.click(screen.getByRole("button", { name: /more filters/i }));
  await selectOption(/^capability$/i, "Model registry");
  await selectOption(/^key intelligence topic$/i, "AI supply chain");
  await selectOption(/^scope$/i, "Industry");

  await waitFor(() => expect(seen.at(-1)!.searchParams.get("scope")).toBe("industry"));
  const last = seen.at(-1)!.searchParams;
  expect(last.get("competitor")).toBe("sonatype");
  expect(last.get("type")).toBe("pricing_change");
  expect(last.get("stance")).toBe("threat");
  expect(last.get("min_importance")).toBe("75");
  expect(last.get("capability")).toBe("model-registry");
  expect(last.get("kit")).toBe("ai-supply-chain");
  const since = Date.parse(last.get("since")!);
  expect(Math.abs(Date.now() - 7 * 86_400_000 - since)).toBeLessThan(60_000);
  // Non-analysts never send a status.
  expect(last.has("status")).toBe(false);
});

it("shows the status facet to analysts only", async () => {
  const seen = serveSignals(() => page([FIREWALL]));
  renderAs("sales", "/signals");
  await screen.findByRole("article", { name: /sonatype bundles/i });
  await userEvent.click(screen.getByRole("button", { name: /more filters/i }));
  expect(screen.getByRole("combobox", { name: /^capability$/i })).toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: /^status$/i })).not.toBeInTheDocument();
  expect(seen.every((u) => !u.searchParams.has("status"))).toBe(true);
});

it("lets analysts filter by status, including what readers never see", async () => {
  const seen = serveSignals(() => page([signalCard({ title: "Harness renames its artifact registry", status: "needs_review" })]));
  renderAs("analyst", "/signals");
  const row = await screen.findByRole("article", { name: /harness renames/i });
  expect(within(row).getByText(/needs review/i)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /more filters/i }));
  await selectOption(/^status$/i, /^needs review$/i);
  await waitFor(() => expect(seen.at(-1)!.searchParams.get("status")).toBe("needs_review"));
});

it("explains an empty Explorer, and offers to clear filters that match nothing", async () => {
  serveSignals((url) => (url.searchParams.get("stance") ? page([]) : page([])));
  renderAs("pm", "/signals");
  expect(await screen.findByText(/no signals yet/i)).toBeInTheDocument();

  await selectOption(/^stance$/i, /opportunity/i);
  expect(await screen.findByText(/no signals match these filters/i)).toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: /clear filters/i }).at(-1)!);
  expect(await screen.findByText(/no signals yet/i)).toBeInTheDocument();
});

it("lists companies by tier in the competitor facet, as the Feed does (one shared list)", async () => {
  renderAs("pm", "/signals");
  const box = await screen.findByRole("combobox", { name: /^competitor$/i });
  await waitFor(() => expect(box).toBeEnabled());
  await userEvent.click(box);
  const options = await screen.findAllByRole("option");
  // JFrog is never a signal's company, so it is no filter option
  expect(options.map((o) => o.textContent)).toEqual(["All companies", "GitHub", "GitLab", "Sonatype", "Docker"]);
  expect(screen.getByText("Tier 1")).toBeInTheDocument();
  expect(screen.getByText("Tier 2")).toBeInTheDocument();
});

it("offers no JFrog scope: every JFrog-self signal is hidden", async () => {
  serveSignals(() => page([FIREWALL]));
  renderAs("pm", "/signals");
  await screen.findByRole("article", { name: /sonatype bundles/i });
  await userEvent.click(screen.getByRole("button", { name: /more filters/i }));
  await userEvent.click(await screen.findByRole("combobox", { name: /^scope$/i }));
  const options = await screen.findAllByRole("option");
  expect(options.map((o) => o.textContent)).toEqual(["Any scope", "Competitor", "Industry"]);
});

it("reads each row from its analysis summary, and shows at rest that the row opens", async () => {
  const summarised = signalCard({
    id: 3,
    title: "Wiz adds a malicious-package feed",
    summary: "Wiz described a pipeline that flags malicious npm releases before a CVE exists.",
    impact: "Wiz's partner announcement is relevant to JFrog's malicious-package defense.",
    analysis_state: "current",
  });
  serveSignals(() => page([summarised, MAVEN]));
  renderAs("pm", "/signals");
  const row = await screen.findByRole("article", { name: /wiz adds a malicious-package feed/i });
  expect(row).toHaveTextContent("Wiz described a pipeline that flags malicious npm releases");
  expect(row).not.toHaveTextContent("partner announcement");
  // without a summary the row keeps its first-take impact line
  expect(screen.getByRole("article", { name: /gitlab tests a dependency proxy/i })).toHaveTextContent(MAVEN.impact!);
  // the chevron is there at rest, not only on hover (DESIGN.md: hover-only is no affordance)
  const chevron = row.querySelector("svg.lucide-chevron-right");
  expect(chevron).not.toBeNull();
  expect(chevron!.getAttribute("class")).not.toMatch(/opacity-0/);
});

it("says what search reads, the summary included", async () => {
  serveSignals(() => page([]));
  renderAs("pm", "/signals?q=zzzz");
  expect(await screen.findByText(/title, fact, impact and summary/i)).toBeInTheDocument();
});
