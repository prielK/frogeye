import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { Schemas } from "@/lib/api/client";
import { App } from "@/app/App";
import { analysedSignal, briefingSignals, personaFor, signalAnalysis, signalDetail } from "@/test/fixtures";
import { MockEventSource, server, serveSignalDetails } from "@/test/msw";
import { renderAs, renderWithProviders } from "@/test/render";

type SignalAnalysis = Schemas["SignalAnalysisOut"];

const TITLE = /sonatype bundles repository firewall into nexus one/i;
const STALE = "This story has new coverage; its analysis is being refreshed.";

/** The section of the page headed `name`. */
function section(name: RegExp | string) {
  return screen.getByRole("heading", { level: 2, name }).closest("section")!;
}

it("renders every analysis section with numbered citations", async () => {
  serveSignalDetails(analysedSignal());
  renderAs("sales", "/signals/101");

  expect(await screen.findByRole("heading", { level: 1, name: TITLE })).toBeInTheDocument();
  // The summary leads, then every section in the spec's order.
  expect(screen.getByText(/which removes the price gap jfrog curation has used/i)).toBeInTheDocument();
  // The side column follows the analysis in reading order.
  const order = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
  expect(order).toEqual([
    "What happened",
    "Background",
    "What changed",
    "Impact on JFrog",
    "Competitive implications",
    "Recommended actions",
    "Risks and opportunities",
    "What to watch",
    "Confidence",
    "Sources",
    "JFrog products",
    "Capabilities",
    "Key intelligence topics",
    "Related signals",
    "Why ranked here",
  ]);

  // Product impact: effect as icon + word, severity as a word, never colour alone.
  const curation = within(section("Impact on JFrog")).getByRole("listitem", { name: "Curation" });
  expect(curation).toHaveTextContent(/threat/i);
  expect(curation).toHaveTextContent(/high severity/i);
  expect(within(section("Impact on JFrog")).getByRole("listitem", { name: "Xray" })).toHaveTextContent(/neutral.*low severity/i);

  // The numbered Sources list: documents link out safely, a visible signal opens its page, a hidden one is text.
  const sources = section("Sources");
  const doc = within(sources).getByRole("link", { name: /introducing nexus one with repository firewall/i });
  expect(doc).toHaveAttribute("href", "https://www.sonatype.com/blog/nexus-one");
  expect(doc).toHaveAttribute("target", "_blank");
  expect(doc).toHaveAttribute("rel", "noopener noreferrer");
  expect(within(sources).getByRole("link", { name: /gitlab tests a dependency proxy for maven/i })).toHaveAttribute("href", "/signals/104");
  expect(within(sources).getByText("Signal not available").closest("a")).toBeNull();
  expect(within(sources).getAllByRole("listitem")).toHaveLength(5);

  // A citation marker scrolls to its source and briefly highlights it.
  const scrolled = vi.spyOn(Element.prototype, "scrollIntoView");
  const happened = section("What happened");
  const marker = within(happened).getByRole("link", { name: /source 2: devops\.com/i });
  expect(marker).toHaveTextContent("[2]");
  await userEvent.click(marker);
  const target = document.getElementById("source-2")!;
  expect(within(sources).getAllByRole("listitem")[1]).toBe(target);
  expect(scrolled.mock.contexts).toContain(target);
  expect(target).toHaveAttribute("data-highlighted", "true");
  expect(target).toHaveFocus();
  scrolled.mockRestore();
});

it("reader's role actions open first", async () => {
  serveSignalDetails(analysedSignal());
  renderAs("sales", "/signals/101");
  const actions = (await screen.findByRole("heading", { level: 2, name: "Recommended actions" })).closest("section")!;

  const sales = within(actions).getByRole("button", { name: /^sales/i });
  const pm = within(actions).getByRole("button", { name: /^product/i });
  expect(sales).toHaveAttribute("aria-expanded", "true");
  expect(pm).toHaveAttribute("aria-expanded", "false");
  expect(within(actions).getByText(/how do you block malicious packages today/i)).toBeVisible();
  // An objection reads as a two-part item: what the customer says, then the answer.
  const objection = within(actions).getByText(/why would we pay for curation/i).closest("li")!;
  expect(objection).toHaveTextContent(/curation's policies cover every package type/i);
  expect(within(actions).getByText(/compare curation's supported ecosystems/i)).not.toBeVisible();

  await userEvent.click(pm);
  expect(pm).toHaveAttribute("aria-expanded", "true");
  expect(within(actions).getByText(/compare curation's supported ecosystems/i)).toBeVisible();
});

it("shows every role's actions to a master reader, and hides a role with nothing to do", async () => {
  const analysis = signalAnalysis();
  analysis.recommended_actions.pmm_devrel = { messaging: [], content_ideas: [] };
  serveSignalDetails(analysedSignal({ analysis }));
  renderAs("analyst", "/signals/101");
  const actions = (await screen.findByRole("heading", { level: 2, name: "Recommended actions" })).closest("section")!;

  for (const role of [/^sales/i, /^product/i, /^executive/i]) {
    expect(within(actions).getByRole("button", { name: role })).toHaveAttribute("aria-expanded", "true");
  }
  expect(within(actions).queryByRole("button", { name: /pmm & devrel/i })).not.toBeInTheDocument();
  expect(within(actions).getByText(/risk level/i)).toHaveTextContent(/medium/i);
});

it("thin, pending and stale states", async () => {
  const thin = signalDetail({
    ...briefingSignals[3]!,
    analysis: null,
    analysis_state: "thin",
    fact: "GitLab opened an issue to proxy Maven dependencies.",
  });
  const pending = signalDetail({ ...briefingSignals[2]!, analysis: null, analysis_state: "pending", fact: "Docker Hardened Images are generally available." });
  const stale = analysedSignal({ analysis_state: "stale" });
  const low = signalDetail({ ...briefingSignals[5]!, analysis: null, analysis_state: "below_threshold", fact: "OpenSSF published a draft." });
  serveSignalDetails(thin, pending, stale, low);

  const first = renderAs("pm", "/signals/104");
  expect(await screen.findByText("Limited evidence: headline only")).toBeInTheDocument();
  expect(screen.getByText(/opened an issue to proxy maven dependencies/i)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { level: 2, name: "What happened" })).not.toBeInTheDocument();
  first.unmount();

  const second = renderAs("pm", "/signals/103");
  expect(await screen.findByText("Full analysis pending")).toBeInTheDocument();
  expect(screen.getByText(/docker hardened images are generally available/i)).toBeInTheDocument();
  expect(screen.queryByText("Limited evidence: headline only")).not.toBeInTheDocument();
  second.unmount();

  // below the full-analysis floor it says so, honestly, instead of promising one
  const fourth = renderAs("pm", "/signals/106");
  expect(await screen.findByText("Short take: not analysed in full")).toBeInTheDocument();
  expect(screen.getByText(/writes a full analysis for its most important stories/i)).toBeInTheDocument();
  expect(screen.queryByText("Full analysis pending")).not.toBeInTheDocument();
  fourth.unmount();

  // A stale analysis still reads in full, under a quiet note that it is being refreshed.
  const third = renderAs("pm", "/signals/101");
  expect(await screen.findByText(STALE)).toBeInTheDocument();
  expect(screen.getByText(/which removes the price gap jfrog curation has used/i)).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 2, name: "Competitive implications" })).toBeInTheDocument();
  expect(within(section("What happened")).getByRole("link", { name: /source 2: devops\.com/i })).toBeInTheDocument();
  expect(within(section("Sources")).getAllByRole("listitem")).toHaveLength(5);
  expect(screen.queryByText("Full analysis pending")).not.toBeInTheDocument();
  third.unmount();

  // The quick look shows the stale summary under the same note.
  server.use(http.get("/api/v1/signals", () => HttpResponse.json({ items: [], next_cursor: null, search_mode: null })));
  renderAs("pm", "/signals?signal=101");
  const drawer = await screen.findByRole("dialog", { name: TITLE });
  expect(await within(drawer).findByText(STALE)).toBeInTheDocument();
  expect(within(drawer).getByText(/which removes the price gap jfrog curation has used/i)).toBeInTheDocument();
  expect(within(drawer).queryByText("Full analysis pending")).not.toBeInTheDocument();
});

it("drawer quick look links to the full page", async () => {
  server.use(http.get("/api/v1/signals", () => HttpResponse.json({ items: briefingSignals, next_cursor: null, search_mode: null })));
  serveSignalDetails(analysedSignal());
  const app = renderAs("sales", "/signals?signal=101");

  const drawer = await screen.findByRole("dialog", { name: TITLE });
  // The quick look: the summary, the reader's role actions (up to 3), the first sources.
  expect(await within(drawer).findByText(/which removes the price gap jfrog curation has used/i)).toBeInTheDocument();
  const todo = within(drawer).getByRole("heading", { name: /what to do/i }).closest("section")!;
  expect(within(todo).getAllByRole("listitem")).toHaveLength(3);
  expect(within(todo).getByText(/how do you block malicious packages today/i)).toBeInTheDocument();
  expect(within(drawer).getByRole("link", { name: /introducing nexus one with repository firewall/i })).toBeInTheDocument();

  await userEvent.click(within(drawer).getByRole("link", { name: "Open full analysis →" }));
  expect(app.location().pathname).toBe("/signals/101");
  expect(app.location().search).toBe("");
  expect(await screen.findByRole("heading", { level: 1, name: TITLE })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("long content does not overflow", async () => {
  const products = [
    "Artifactory",
    "Xray",
    "Advanced Security",
    "Curation",
    "Distribution",
    "AppTrust",
    "Runtime",
    "JFrog ML",
    "AI Catalog",
    "MCP Registry",
    "Container Registry",
    "Connect",
    "Platform",
  ];
  const long = (i: number) =>
    `Action ${i}: a deliberately long recommendation naming supercalifragilisticexpialidocious-dependency-firewall-policies and https://example.com/a/very/long/path/that/never/breaks/${i}`;
  const lines = (from: number, n: number) => Array.from({ length: n }, (_, i) => long(from + i));
  const analysis: SignalAnalysis = signalAnalysis({
    jfrog_impact: products.map((product, i) => ({
      product,
      effect: (["threat", "opportunity", "neutral"] as const)[i % 3]!,
      severity: (["high", "medium", "low"] as const)[i % 3]!,
      rationale: `${product} rationale with an unbroken token ${"x".repeat(80)}.`,
      cites: [1, 2, 3],
    })),
    recommended_actions: {
      sales: {
        talk_track: lines(1, 4),
        discovery_questions: lines(5, 4),
        objections: [1, 2, 3].map((i) => ({ objection: long(100 + i), response: long(200 + i) })),
        landmines: lines(9, 3),
      },
      pm: { assessment: lines(12, 3), follow_ups: lines(15, 3) },
      exec: { implications: lines(18, 3), decisions: lines(21, 2), risk_level: "high" },
      pmm_devrel: { messaging: lines(23, 3), content_ideas: lines(26, 2) },
    },
  });
  const width = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 390 });
  window.dispatchEvent(new Event("resize"));
  serveSignalDetails(analysedSignal({ analysis }));
  renderAs("analyst", "/signals/101");

  const page = await screen.findByRole("article", { name: TITLE });
  expect(within(section("Impact on JFrog")).getAllByRole("listitem")).toHaveLength(13);
  // 30 actions: 27 lines plus 3 objection pairs, all expanded for a master reader.
  expect(within(section("Recommended actions")).getAllByRole("listitem")).toHaveLength(30);
  const overflowing = [page, ...page.querySelectorAll("*")].filter((el) => el.scrollWidth > el.clientWidth);
  expect(overflowing).toEqual([]);
  // Long unbroken tokens are allowed to wrap anywhere instead of pushing the column wide.
  expect(page.className.split(" ")).toContain("wrap-anywhere");
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: width });
});

it("lists related signals in the side column with their trust line and companies, as everywhere else", async () => {
  serveSignalDetails(analysedSignal(), signalDetail({ ...briefingSignals[4]! }));
  const app = renderAs("pm", "/signals/101");
  const related = (await screen.findByRole("heading", { level: 2, name: "Related signals" })).closest("section")!;
  const github = within(related).getAllByRole("listitem").find((li) => /github raises packages storage limits/i.test(li.textContent ?? ""))!;
  expect(within(github).getByRole("button", { name: /admiralty grade a1.*ai analysis/i })).toBeInTheDocument();
  // Named from the competitor index, as everywhere else.
  expect(await within(within(github).getByRole("list", { name: "Companies" })).findByText("GitHub")).toBeInTheDocument();

  // A related signal opens its own analysis page; Back returns to this one.
  await userEvent.click(within(github).getByRole("link", { name: /github raises packages storage limits/i }));
  expect(await screen.findByRole("heading", { level: 1, name: /github raises packages storage limits/i })).toBeInTheDocument();
  expect(app.location().pathname).toBe("/signals/105");
  await userEvent.click(screen.getByRole("button", { name: /^back$/i }));
  expect(await screen.findByRole("heading", { level: 1, name: TITLE })).toBeInTheDocument();
});

it("offers the Explorer, not a dead Back, on a deep link that went through sign-in", async () => {
  server.use(http.post("/api/v1/auth/login", () => HttpResponse.json(personaFor.pm)));
  serveSignalDetails(analysedSignal());
  // Signed out: /signals/101 → /login → back to /signals/101, both by replace, so the page is the only entry.
  const app = renderWithProviders(<App />, { route: "/signals/101" });
  await userEvent.click(await screen.findByRole("button", { name: /priya shah/i }));
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  expect(await screen.findByRole("heading", { level: 1, name: TITLE })).toBeInTheDocument();
  expect(app.location().pathname).toBe("/signals/101");
  expect(screen.queryByRole("button", { name: /^back$/i })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("link", { name: /all signals/i }));
  expect(app.location()).toMatchObject({ pathname: "/signals", search: "" });
});

it("opens the reader's own actions first, from the drawer and the page alike; an edition in the address changes nothing", async () => {
  server.use(http.get("/api/v1/signals", () => HttpResponse.json({ items: briefingSignals, next_cursor: null, search_mode: null })));
  serveSignalDetails(analysedSignal(), signalDetail({ ...briefingSignals[3]! }));
  const app = renderAs("sales", "/signals?edition=pm&signal=101");

  const drawer = await screen.findByRole("dialog", { name: TITLE });
  expect(await within(drawer).findByText(/we block malicious packages before they reach developers/i)).toBeInTheDocument();
  expect(within(drawer).queryByText(/compare curation's supported ecosystems/i)).not.toBeInTheDocument();
  const open = within(drawer).getByRole("link", { name: "Open full analysis →" });
  expect(open).toHaveAttribute("href", "/signals/101");
  await userEvent.click(open);

  expect(app.location()).toMatchObject({ pathname: "/signals/101" });
  const actions = (await screen.findByRole("heading", { level: 2, name: "Recommended actions" })).closest("section")!;
  expect(within(actions).getByRole("button", { name: /^sales/i })).toHaveAttribute("aria-expanded", "true");
  expect(within(actions).getByRole("button", { name: /^product/i })).toHaveAttribute("aria-expanded", "false");
  expect(within(actions).getByRole("button", { name: /^sales/i })).toHaveTextContent(/your role/i);
  expect(within(actions).queryByText(/ edition$/i)).not.toBeInTheDocument();
  // Links onward from the page carry no edition either.
  expect(within(section("Sources")).getByRole("link", { name: /gitlab tests a dependency proxy for maven/i })).toHaveAttribute("href", "/signals/104");
});

it("says when a signal isn't available", async () => {
  renderAs("sales", "/signals/999");
  expect(await screen.findByText("This signal isn't available.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /see every signal/i })).toHaveAttribute("href", "/signals");
});

it("never doubles the quotes a model already wrote around a talk-track line or an objection", async () => {
  const base = signalAnalysis();
  const sales = base.recommended_actions.sales;
  serveSignalDetails(
    analysedSignal({
      analysis: {
        ...base,
        recommended_actions: {
          ...base.recommended_actions,
          sales: {
            ...sales,
            talk_track: ['"We gate every package type with one Curation policy."'],
            objections: [{ objection: "“Nexus One already blocks this.”", response: "Curation covers every ecosystem." }],
          },
        },
      },
    }),
  );
  renderAs("sales", "/signals/101");
  const actions = (await screen.findByRole("heading", { level: 2, name: "Recommended actions" })).closest("section")!;
  expect(within(actions).getByText("“We gate every package type with one Curation policy.”")).toBeInTheDocument();
  expect(within(actions).getByText("“Nexus One already blocks this.”")).toBeInTheDocument();
  expect(actions.textContent).not.toMatch(/[“"]{2}|[”"]{2}/);
});

it("counts the story's own articles in the header, not the JFrog pages and signals it cites", async () => {
  serveSignalDetails(analysedSignal());
  renderAs("sales", "/signals/101");
  const header = (await screen.findByRole("heading", { level: 1, name: TITLE })).closest("header")!;
  expect(header).toHaveTextContent("2 articles");
  expect(header).not.toHaveTextContent(/5 sources/);
});

it("keeps the page on screen when a background refresh fails", async () => {
  let fail = false;
  let requests = 0;
  server.use(
    http.get("/api/v1/signals/:id", () => {
      requests += 1;
      return fail ? HttpResponse.json({ detail: "down" }, { status: 500 }) : HttpResponse.json(analysedSignal());
    }),
  );
  renderAs("sales", "/signals/101");
  expect(await screen.findByRole("heading", { level: 1, name: TITLE })).toBeInTheDocument();
  fail = true;
  const before = requests;
  // a run finishing mid-demo invalidates every open detail (refreshAfterRun)
  MockEventSource.emit("run.progress", { pipeline_run_id: 1, agent_run_id: 1, stage: "publish", counts: {} });
  await waitFor(() => expect(requests).toBeGreaterThan(before));
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(screen.getByRole("heading", { level: 1, name: TITLE })).toBeInTheDocument();
  expect(screen.queryByText("The analysis didn't load.")).not.toBeInTheDocument();
});
