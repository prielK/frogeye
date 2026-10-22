import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "sonner";
import {
  agentRun,
  analysedSignal,
  briefing,
  briefingSignals,
  briefingSummary,
  costs,
  competitorOverview,
  EDITION_TAKEAWAYS,
  legacyBriefing,
  LIVE_DAY_FUNNEL,
  preAngleBriefing,
  quietBriefing,
  signalCard,
  signalDetail,
  skippedBriefing,
} from "@/test/fixtures";
import { MockEventSource, server } from "@/test/msw";
import { renderAs } from "@/test/render";

const HEADLINE = "Sonatype and GitHub both move onto JFrog's registry turf";
const READ = "Read full analysis →";
const PENDING = "Full analysis pending";
const JFROG = "Why it matters for JFrog";
/** The label over a card's why-it-matters line, one per angle (exact copy). */
const ANGLE_LABEL = /^(Why it matters for JFrog|In your deals|Product angle|Strategic angle|Messaging angle)$/;

function serveLatest(body: ReturnType<typeof briefing>, seen?: URL[]) {
  server.use(
    http.get("/api/v1/briefings/latest", ({ request }) => {
      seen?.push(new URL(request.url));
      return HttpResponse.json(body);
    }),
  );
}

/** Signal 101 with its full analysis; the others as stage-A details. */
function serveDetails() {
  server.use(
    http.get("/api/v1/signals/:id", ({ params }) => {
      const id = Number(params.id);
      return HttpResponse.json(id === 101 ? analysedSignal() : signalDetail({ ...briefingSignals.find((s) => s.id === id)! }));
    }),
  );
}

async function takeawayCards() {
  const region = await screen.findByRole("region", { name: /top takeaways/i });
  return within(region).getAllByRole("article");
}

type Reader = "analyst" | "sales" | "pm" | "exec" | "pmm_devrel";
/** The edition the API serves each reader: their role's own, the master for analysts. */
const OWN: Record<Reader, keyof typeof EDITION_TAKEAWAYS> = { analyst: "master", sales: "sales", pm: "pm", exec: "exec", pmm_devrel: "pmm_devrel" };

/** Signed in as `role`, served that reader's own edition, as the API does (it takes no edition parameter). */
function renderEdition(role: Reader, seen?: URL[]) {
  serveLatest(briefing({ edition: OWN[role], requested_edition: OWN[role] }), seen);
  return renderAs(role, "/briefing");
}

/** A story's ONE link to its analysis (a card's product tags are links of their own, to the product pages). */
function storyLink(story: HTMLElement): HTMLElement {
  const links = within(story)
    .getAllByRole("link")
    .filter((a) => !a.getAttribute("href")?.startsWith("/products/"));
  expect(links).toHaveLength(1);
  return links[0]!;
}

/** The signal each takeaway on screen tells, in card order. */
function shownTakeaways() {
  const region = screen.getByRole("region", { name: /top takeaways/i });
  return within(region)
    .getAllByRole("article")
    .map((card) => Number(/\/signals\/(\d+)/.exec(storyLink(card).getAttribute("href") ?? "")?.[1]));
}

/** The page's header: the publication's name, its edition and the day. */
function nameplate() {
  return screen.getByRole("heading", { level: 1, name: "Morning Briefing" }).closest("header")!;
}

function angleLabels(cards: HTMLElement[]) {
  return cards.map((card) => within(card).getByText(ANGLE_LABEL).textContent);
}

/** Classes that would hide an element until hover or focus: an affordance must show at rest. */
function expectShownAtRest(el: HTMLElement, root: HTMLElement) {
  for (let node: HTMLElement | null = el; node && node !== root.parentElement; node = node.parentElement) {
    expect(node.className).not.toMatch(/(^|\s)(opacity-0|invisible|hidden|sr-only)(\s|$)/);
    expect(node.className).not.toMatch(/(^|\s)group-hover[^\s]*:(opacity|visible|block|inline|flex)/);
  }
}

it("renders the headline and the three takeaways from the latest briefing", async () => {
  serveLatest(briefing());
  renderAs("sales", "/briefing");

  expect(await screen.findByRole("heading", { name: HEADLINE })).toBeInTheDocument();
  const takeaways = screen.getByRole("region", { name: /top takeaways/i });
  expect(within(takeaways).getAllByRole("article")).toHaveLength(3);
  expect(within(takeaways).getByRole("heading", { name: /sonatype makes its dependency firewall/i })).toBeInTheDocument();
  expect(within(takeaways).getByRole("heading", { name: /github opens packages/i })).toBeInTheDocument();
  expect(within(takeaways).getByRole("heading", { name: /github gives enterprise cloud more free packages storage/i })).toBeInTheDocument();

  // The card's substance, copied from the analysis: summary, why it matters, what to do.
  const [lead] = await takeawayCards();
  expect(within(lead!).getByText(/^Sonatype now includes Repository Firewall in every Nexus One subscription/)).toBeInTheDocument();
  // The Sales edition's own angle stands where "Why it matters for JFrog" stood: its first objection.
  expect(within(lead!).getByText("In your deals")).toBeInTheDocument();
  expect(within(lead!).getByText(/^Objection: Nexus One now includes a firewall, so why would we pay for Curation\? — Response: /)).toBeInTheDocument();
  expect(within(within(lead!).getByRole("list", { name: "What to do" })).getAllByRole("listitem")).toHaveLength(3);
  // The funnel strip, from the day's counts: new signals filed, then what this briefing carries.
  expect(screen.getByText(/scanned/i).closest("p")).toHaveTextContent(
    /scanned\s*412.*61\s*relevant.*14\s*new signals.*9\s*in this briefing.*3\s*that matter/i,
  );
  // the nameplate's time is the app's (06:12 in Israel), not the browser's (03:12 UTC).
  expect(screen.getByText(/published 06:12/)).toBeInTheDocument();
  // Trust is on the card: the Admiralty grade and the approval state, in words.
  expect(within(lead!).getByText("B1")).toBeInTheDocument();
  expect(within(lead!).getByText("Analyst approved")).toBeInTheDocument();
  expect(within(lead!).getByText(/admiralty grade b1: source usually reliable, information confirmed/i)).toBeInTheDocument();
  // ...with the company, the outlet and the time.
  expect(within(lead!).getByText("Sonatype")).toBeInTheDocument();
  expect(within(lead!).getByText("Sonatype blog")).toBeInTheDocument();
  expect(lead!.querySelector('time[datetime="2026-10-17T05:40:00Z"]')).not.toBeNull();
});

it("whole card opens the full analysis", async () => {
  serveLatest(briefing());
  serveDetails();
  const app = renderAs("sales", "/briefing");

  const cards = await takeawayCards();
  for (const card of cards) {
    // ONE link to the analysis per card, with nothing interactive inside it. Its ::after covers
    // the card (the card is its positioned frame), so the whole block opens it; the product tags
    // are links of their own to the product pages, lifted above that cover, never nested in it
    //. The affordance shows at rest.
    const links = within(card).getAllByRole("link");
    const products = links.filter((a) => a.getAttribute("href")?.startsWith("/products/"));
    const analysis = links.filter((a) => !products.includes(a));
    expect(analysis).toHaveLength(1);
    expect(analysis[0]!.querySelectorAll("a, button, input, select, [tabindex]")).toHaveLength(0);
    // The cover really spans the card: its ::after is absolute, has content and bleeds 12px,
    // the card is its positioned frame, and no positioned element between them would shrink the
    // cover to the headline.
    const cover = analysis[0]!;
    for (const cls of ["after:absolute", "after:content-['']", "after:-inset-3"]) expect(cover.className.split(/\s+/)).toContain(cls);
    expect(card.className).toMatch(/(^|\s)relative(\s|$)/);
    for (let el = cover.parentElement; el && el !== card; el = el.parentElement) {
      expect(el.className).not.toMatch(/(^|\s)(relative|absolute|fixed|sticky)(\s|$)/);
    }
    for (const p of products) {
      expect(analysis[0]!.contains(p)).toBe(false);
      expect(p.className).toMatch(/(^|\s)z-10(\s|$)/);
    }
    expectShownAtRest(within(card).getByText(READ), card);
  }
  const lead = within(cards[0]!).getByRole("link", { name: "Sonatype makes its dependency firewall part of Nexus One" });
  expect(lead).toHaveAttribute("href", "/signals/101");

  // The cover is the link itself, so a click anywhere on the card lands on it (jsdom has no
  // layout; the screenshot harness clicks the summary in a real browser).
  await userEvent.click(lead);
  expect(app.location()).toMatchObject({ pathname: "/signals/101", search: "" });
  expect(await screen.findByRole("heading", { level: 1, name: /sonatype bundles repository firewall/i })).toBeInTheDocument();
});

it("cards are uniform", async () => {
  serveLatest(briefing());
  renderAs("sales", "/briefing");

  const cards = await takeawayCards();
  const titles = cards.map((c) => within(c).getByRole("heading", { level: 3 }));
  const bodies = [
    within(cards[0]!).getByText(/^Sonatype now includes Repository Firewall/),
    within(cards[1]!).getByText(/^GitHub raised the Packages storage included/),
    within(cards[2]!).getByText(/^GitHub Packages can now run as a self-hosted registry/),
  ];
  const whys = cards.map((c) => within(c).getByText(ANGLE_LABEL));
  const actions = cards.map((c) => within(within(c).getByRole("list", { name: "What to do" })).getAllByRole("listitem")[0]!);
  const ctas = cards.map((c) => within(c).getByText(READ));
  // One anatomy: the same parts in the same styles, except the lead's headline, the page's hero.
  for (const part of [bodies, whys, actions, ctas]) {
    expect(new Set(part.map((el) => el.className)).size).toBe(1);
  }
  expect(titles[1]!.className).toBe(titles[2]!.className);
  expect(titles[0]!.className).toMatch(/(^|\s)text-display(\s|$)/);
  expect(titles[1]!.className).toMatch(/(^|\s)text-title(\s|$)/);
  // Every card speaks the same three sizes; the lead adds display for its headline.
  const sizes = cards.map((c) =>
    [
      ...new Set(
        [c, ...c.querySelectorAll("*")].flatMap((el) => (el.getAttribute("class") ?? "").match(/\btext-(display|title|body|meta)\b/g) ?? []),
      ),
    ]
      .sort()
      .join(" "),
  );
  expect(sizes).toEqual(["text-body text-display text-meta text-title", "text-body text-meta text-title", "text-body text-meta text-title"]);
});

it("no story appears twice", async () => {
  // A new briefing files each signal once; an older one repeated its takeaways
  // in the sections, and the page still shows each story once.
  for (const filed of [briefing(), legacyBriefing()]) {
    serveLatest(filed);
    const view = renderAs("sales", "/briefing");
    await screen.findByRole("heading", { name: HEADLINE });
    // Every story (a card or a row) is an article of its own inside the page's.
    const stories = screen.getAllByRole("article").filter((a) => a.querySelector("article") === null);
    const hrefs = stories.map((a) => storyLink(a).getAttribute("href"));
    expect(hrefs.length).toBeGreaterThan(3);
    expect(new Set(hrefs).size).toBe(hrefs.length);
    view.unmount();
  }
});

it("legacy briefing renders", async () => {
  serveLatest(legacyBriefing());
  renderAs("sales", "/briefing");

  const [lead, second, third] = await takeawayCards();
  // so_what is the summary, now_what the one action, and the card is not marked as pending.
  expect(within(lead!).getByText(/^Nexus customers now get pre-download malware blocking/)).toBeInTheDocument();
  const todo = within(within(lead!).getByRole("list", { name: "What to do" })).getAllByRole("listitem");
  expect(todo.map((li) => li.textContent)).toEqual([
    "In Nexus renewals, ask how they block malicious packages today and show Curation's policy audit trail.",
  ]);
  expect(within(lead!).queryByText("Why it matters for JFrog")).not.toBeInTheDocument();
  expect(screen.queryByText(PENDING)).not.toBeInTheDocument();
  // A takeaway resting on two signals opens the first and names their outlet once.
  expect(within(second!).getByRole("link", { name: /github opens packages/i })).toHaveAttribute("href", "/signals/102");
  expect(within(second!).getAllByText("GitHub Changelog")).toHaveLength(1);
  // An empty now_what leaves no empty "What to do".
  expect(within(third!).queryByRole("list", { name: "What to do" })).not.toBeInTheDocument();
  expect(within(third!).getByText(READ)).toBeInTheDocument();
  // Legacy rows read their why-it-matters line (no summary was filed) and still link to the page.
  const pulse = screen.getByRole("region", { name: /industry pulse/i });
  expect(within(pulse).getByText(/a shared format favours registries that already store build provenance/i)).toBeInTheDocument();
  expect(within(pulse).getByRole("link", { name: /openssf publishes a draft attestation format/i })).toHaveAttribute("href", "/signals/106");
});

it("marks a stage-A card \"Full analysis pending\", as the page and drawer do, never an analysed or a legacy one", async () => {
  serveLatest(briefing({ edition: "master" }));
  renderAs("analyst", "/briefing");

  const [lead, second, third] = await takeawayCards();
  expect(within(third!).getByText(PENDING)).toBeInTheDocument();
  expect(within(lead!).queryByText(PENDING)).not.toBeInTheDocument();
  expect(within(second!).queryByText(PENDING)).not.toBeInTheDocument();
  // The stage-A card keeps its whole anatomy: never blank.
  expect(within(third!).getByText(/^Docker Hardened Images, a catalogue/)).toBeInTheDocument();
  expect(within(third!).getByText(/image provenance becomes a buying criterion/i)).toBeInTheDocument();
  // On the master, the first stage-A line of each role that has one.
  expect(within(within(third!).getByRole("list", { name: "What to do" })).getAllByRole("listitem")).toHaveLength(3);
  // So is a section row.
  const releases = screen.getByRole("region", { name: /product & releases/i });
  expect(within(releases).getByText(PENDING)).toBeInTheDocument();
  const moves = screen.getByRole("region", { name: /competitive moves/i });
  expect(within(moves).queryByText(PENDING)).not.toBeInTheDocument();
});

it("says why a stage-A story has no full analysis, from its live state, in the page's words", async () => {
  // 103 turned out headline-only, 104 ranks below the full-analysis floor; one copy source
  const live = { 103: "thin", 104: "below_threshold" } as const;
  serveLatest(
    briefing({
      edition: "master",
      signals: briefingSignals.map((s) => (s.id in live ? { ...s, analysis_state: live[s.id as keyof typeof live] } : s)),
    }),
  );
  renderAs("analyst", "/briefing");

  const [, , third] = await takeawayCards();
  expect(within(third!).getByText("Limited evidence: headline only")).toBeInTheDocument();
  expect(within(third!).queryByText(PENDING)).not.toBeInTheDocument();
  const releases = screen.getByRole("region", { name: /product & releases/i });
  expect(within(releases).getByText("Short take: not analysed in full")).toBeInTheDocument();
  expect(screen.queryByText(PENDING)).not.toBeInTheDocument();
});

it("drops the mark once the story's analysis has landed since the briefing was filed", async () => {
  serveLatest(briefing({ signals: briefingSignals.map((s) => (s.id === 103 || s.id === 104 ? { ...s, analysis_state: "current" } : s)) }));
  renderAs("sales", "/briefing");
  await takeawayCards();
  expect(screen.queryByText(PENDING)).not.toBeInTheDocument();
  expect(screen.queryByText(/limited evidence|short take/i)).not.toBeInTheDocument();
});

it("shows at rest that a watchlist note opens, and does not repeat a headline already on the page", async () => {
  serveLatest(briefing());
  renderAs("sales", "/briefing");
  const watch = await screen.findByRole("region", { name: /watchlist/i });
  const link = within(watch).getByRole("link", { name: /whether gitlab's maven proxy ships/i });
  const cue = link.querySelector("svg.lucide-chevron-right");
  expect(cue).not.toBeNull();
  expect(cue!.getAttribute("class")).not.toMatch(/(^|\s)(opacity-0|invisible|hidden|sr-only)(\s|$)|group-hover[^\s]*:opacity/);
  expectShownAtRest(link, link);
  // 104 is already a row in "Product & releases": its headline is not printed again here
  expect(within(watch).queryByText("GitLab tests a dependency proxy for Maven")).not.toBeInTheDocument();
});

it("shows why it matters for JFrog, each affected product with its effect in words", async () => {
  serveLatest(briefing({ edition: "master" }));
  renderAs("analyst", "/briefing");

  const [lead, second] = await takeawayCards();
  const products = within(within(lead!).getByRole("list", { name: "JFrog products affected" })).getAllByRole("listitem");
  // Highest severity first, each effect as icon + word (never colour alone).
  expect(products.map((p) => p.textContent)).toEqual(["CurationThreat", "ArtifactoryThreat", "XrayNeutral"]);
  expect(products[0]!.querySelector("svg")).not.toBeNull();
  const github = within(within(second!).getByRole("list", { name: "JFrog products affected" })).getAllByRole("listitem");
  expect(github.map((p) => p.textContent)).toEqual(["ArtifactoryThreat", "DistributionOpportunity"]);
});

it("master actions are role-labelled", async () => {
  serveLatest(briefing({ edition: "master" }));
  renderAs("analyst", "/briefing");

  const [lead, , third] = await takeawayCards();
  const items = within(within(lead!).getByRole("list", { name: "What to do" })).getAllByRole("listitem");
  expect(items.map((li) => li.textContent)).toEqual([
    "Sales: We block malicious packages before they reach developers, and Curation keeps a policy audit trail for every decision.",
    "Product: Parity on pre-download blocking for npm, PyPI and Maven; Curation's wider ecosystem coverage is not matched by the bundle.",
    "Executive: Bundling turns malicious-package blocking into a base registry feature, which pressures Curation's pricing as an add-on.",
  ]);
  const stageA = within(within(third!).getByRole("list", { name: "What to do" })).getAllByRole("listitem");
  expect(stageA.map((li) => li.textContent)).toEqual([
    "Sales: Ask which base images the team trusts today and how they verify them.",
    "Product: Check which Docker Hardened Images signatures Xray can verify today.",
    "PMM & DevRel: Write up how to mirror Docker Hardened Images into Artifactory.",
  ]);
});

it("labels no role in a role's own edition, and shows its first three actions", async () => {
  serveLatest(briefing());
  renderAs("sales", "/briefing");

  const [lead] = await takeawayCards();
  const items = within(within(lead!).getByRole("list", { name: "What to do" })).getAllByRole("listitem");
  expect(items.map((li) => li.textContent)).toEqual([
    "We block malicious packages before they reach developers, and Curation keeps a policy audit trail for every decision.",
    "How do you block malicious packages today, and who reviews the exceptions?",
    "Which ecosystems beyond npm, PyPI and Maven do your teams pull from?",
  ]);
});

it("labels each card's why-it-matters line by its angle, where \"Why it matters for JFrog\" stood", async () => {
  const expected: Record<Reader, string[]> = {
    analyst: [JFROG, JFROG, JFROG],
    sales: ["In your deals", "In your deals", "In your deals"],
    // The stage-A card has no Product text of its own: it keeps the JFrog line and its label.
    pm: ["Product angle", JFROG, "Product angle"],
    exec: ["Strategic angle", "Strategic angle", "Strategic angle"],
    pmm_devrel: ["Messaging angle", "Messaging angle", "Messaging angle"],
  };
  for (const [role, labels] of Object.entries(expected) as [Reader, string[]][]) {
    const view = renderEdition(role);
    const cards = await takeawayCards();
    expect(shownTakeaways()).toEqual(EDITION_TAKEAWAYS[OWN[role]]);
    expect(angleLabels(cards)).toEqual(labels);
    // The label replaces the JFrog one in place: one label per card.
    for (const card of cards) expect(within(card).getAllByText(ANGLE_LABEL)).toHaveLength(1);
    view.unmount();
  }
});

it("reads each role's own line verbatim under its angle, and does not repeat it in What to do", async () => {
  const sales = renderEdition("sales");
  const [lead, , third] = await takeawayCards();
  expect(
    within(lead!).getByText(
      "Objection: Nexus One now includes a firewall, so why would we pay for Curation? — Response: Curation's policies cover every package type Artifactory proxies, not only npm, PyPI and Maven Central.",
    ),
  ).toBeInTheDocument();
  expect(within(within(lead!).getByRole("list", { name: "What to do" })).queryByText(/^Objection:/)).not.toBeInTheDocument();
  // No objection on this story: its talk-track line, as speech.
  expect(
    within(third!).getByText("Say: “Artifactory keeps every format your teams build beside the runners that build them, in any cloud.”"),
  ).toBeInTheDocument();
  const thirdTodo = within(within(third!).getByRole("list", { name: "What to do" })).getAllByRole("listitem");
  expect(thirdTodo.map((li) => li.textContent)).toEqual(["Which package types beyond npm and container images do your teams publish today?"]);
  sales.unmount();

  renderEdition("exec");
  const [execLead] = await takeawayCards();
  expect(within(execLead!).getByText("Strategic angle")).toBeInTheDocument();
  expect(
    within(execLead!).getByText(
      "GitHub is making Packages the default home for build output, which narrows Artifactory's entry point in GitHub-first teams.",
    ),
  ).toBeInTheDocument();
  const execTodo = within(within(execLead!).getByRole("list", { name: "What to do" })).getAllByRole("listitem");
  expect(execTodo.map((li) => li.textContent)).toEqual(["Decide whether an Actions-native Artifactory setup is a funded priority this quarter."]);
});

it("shows the Exec risk level beside the strategic angle as icon plus word, and no chip on the master", async () => {
  const exec = renderEdition("exec");
  const cards = await takeawayCards();
  const chips = cards.map((card) => within(card).getByText(/^Risk: /));
  expect(chips.map((chip) => chip.textContent)).toEqual(["Risk: high", "Risk: medium", "Risk: low"]);
  chips.forEach((chip, i) => {
    // Never colour alone: an icon and the word, on the angle label's line.
    expect(chip.querySelector("svg")).not.toBeNull();
    expect(chip.parentElement).toContainElement(within(cards[i]!).getByText("Strategic angle"));
  });
  // Each level has its own mark, so the level reads without its colour.
  expect(new Set(chips.map((chip) => chip.querySelector("svg")!.getAttribute("class"))).size).toBe(3);
  exec.unmount();

  // The master carries no chip, even on a row that names a level; nor does another role's edition.
  const master = briefing({ edition: "master" });
  serveLatest({
    ...master,
    content: { ...master.content, top_takeaways: master.content.top_takeaways.map((t) => ({ ...t, risk_level: "high" as const })) },
  });
  const analyst = renderAs("analyst", "/briefing");
  await takeawayCards();
  expect(screen.queryByText(/^Risk: /)).not.toBeInTheDocument();
  analyst.unmount();
  renderEdition("pm");
  await takeawayCards();
  expect(screen.queryByText(/^Risk: /)).not.toBeInTheDocument();
});

it("reads a card filed before angles under \"Why it matters for JFrog\"", async () => {
  const filed = preAngleBriefing();
  expect(filed.content.top_takeaways.every((t) => !("angle" in t) && !("risk_level" in t))).toBe(true);
  serveLatest(filed);
  renderAs("sales", "/briefing");

  const cards = await takeawayCards();
  expect(angleLabels(cards)).toEqual([JFROG, JFROG, JFROG]);
  expect(within(cards[0]!).getByText(/removes the separate purchase that made Curation's price advantage visible/i)).toBeInTheDocument();
  expect(screen.queryByText(/^Risk: /)).not.toBeInTheDocument();
});

it("shows each reader only their own edition: its own stories and angles, no edition switch", async () => {
  // every role edition leads with at least one story the master does not.
  for (const role of ["sales", "pm", "exec", "pmm_devrel"] as const) {
    expect(EDITION_TAKEAWAYS[role].some((id) => !EDITION_TAKEAWAYS.master.includes(id))).toBe(true);
  }
  const readers = [
    { role: "sales", name: "Sales edition", labels: ["In your deals", "In your deals", "In your deals"] },
    { role: "exec", name: "Executive edition", labels: ["Strategic angle", "Strategic angle", "Strategic angle"] },
    { role: "pmm_devrel", name: "PMM & DevRel edition", labels: ["Messaging angle", "Messaging angle", "Messaging angle"] },
    { role: "pm", name: "Product edition", labels: ["Product angle", JFROG, "Product angle"] },
    { role: "analyst", name: "Master edition", labels: [JFROG, JFROG, JFROG] },
  ] as const;
  for (const reader of readers) {
    const seen: URL[] = [];
    const view = renderEdition(reader.role, seen);
    expect(angleLabels(await takeawayCards())).toEqual(reader.labels);
    expect(shownTakeaways()).toEqual(EDITION_TAKEAWAYS[OWN[reader.role]]);
    // The header names the reader's edition; nothing offers another one.
    expect(nameplate()).toHaveTextContent(reader.name);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    // The page never asks for an edition: the API serves the reader's own.
    expect(seen.length).toBeGreaterThan(0);
    for (const url of seen) expect(url.searchParams.has("edition")).toBe(false);
    view.unmount();
  }
});

it("section rows link to the page and read the summary's first sentence", async () => {
  serveLatest(briefing({ edition: "master" }));
  serveDetails();
  const app = renderAs("analyst", "/briefing");

  const moves = await screen.findByRole("region", { name: /competitive moves/i });
  const row = within(moves).getByRole("link", { name: "GitHub raises Packages storage limits for Enterprise Cloud" });
  expect(row).toHaveAttribute("href", "/signals/105");
  expect(within(row).getByText("GitHub raised the Packages storage included with Enterprise Cloud plans.")).toBeInTheDocument();
  expect(within(moves).queryByText(/weakens the cost case/i)).not.toBeInTheDocument();
  // The meta line: stance, company, outlet, trust; nothing interactive inside the row's link.
  expect(within(row).getByText("Threat")).toBeInTheDocument();
  expect(within(row).getByText("GitHub Changelog")).toBeInTheDocument();
  expect(within(row).getByText("A1")).toBeInTheDocument();
  expect(row.querySelectorAll("a, button, input, select, [tabindex]")).toHaveLength(0);

  await userEvent.click(within(row).getByText("GitHub Changelog"));
  expect(app.location()).toMatchObject({ pathname: "/signals/105" });
});

it("opens every story's analysis in the reader's own edition, with no edition in any address", async () => {
  renderEdition("pm");
  const [lead] = await takeawayCards();
  expect(storyLink(lead!)).toHaveAttribute("href", "/signals/102");
  const moves = screen.getByRole("region", { name: /competitive moves/i });
  expect(within(moves).getAllByRole("link")[0]).toHaveAttribute("href", "/signals/101");
  const watch = screen.getByRole("region", { name: /watchlist/i });
  expect(within(watch).getByRole("link")).toHaveAttribute("href", "/signals/104");
  for (const a of screen.getAllByRole("link")) expect(a.getAttribute("href") ?? "").not.toMatch(/edition=/);
});

it("explanation lines", async () => {
  serveLatest(briefing());
  const first = renderAs("sales", "/briefing");
  expect(await screen.findByText("Today's most important competitor and market moves, written for your role.")).toBeInTheDocument();
  first.unmount();

  renderAs("sales", "/signals");
  expect(await screen.findByText("Every analysed story, searchable. The Briefing is drawn from these.")).toBeInTheDocument();
});

it("labels the day's funnel the way the live data needs: new signals vs in this briefing", async () => {
  // A v2 after a same-day re-run: the day read 5,292 items over two runs and filed 224 new
  // signals, while the briefing carries 2. The stages must not read as one shrinking chain.
  serveLatest(briefing({ version: 2, funnel: { ...LIVE_DAY_FUNNEL }, signals: briefingSignals.slice(0, 2) }));
  renderAs("sales", "/briefing");

  const strip = (await screen.findByText(/scanned/i)).closest("button")!;
  expect(strip).toHaveTextContent(/scanned\s*5,292.*233\s*relevant.*224\s*new signals/i);
  expect(strip).toHaveTextContent(/2\s*in this briefing/i);
  expect(strip).not.toHaveTextContent(/\b2\s*signals\b/i);
  await userEvent.hover(strip);
  const tip = await screen.findByRole("tooltip");
  expect(tip).toHaveTextContent(/from the 2 runs that filed this day's briefing/i);
  expect(tip).toHaveTextContent(/224 new signals filed, 224 analysed/i);
});

it("takes a takeaway's grade and trust state from the same, least-trusted signal", async () => {
  const strong = signalCard({ id: 201, grade: "B1", reliability: "B", credibility: 1, status: "approved", primary_source: "Sonatype blog" });
  const weak = signalCard({ id: 202, grade: "C3", reliability: "C", credibility: 3, status: "auto", primary_source: "Google News" });
  const base = legacyBriefing();
  serveLatest(
    briefing({
      signals: [strong, weak],
      content: {
        ...base.content,
        // An older briefing could rest one takeaway on several signals.
        top_takeaways: [{ ...base.content.top_takeaways[0]!, signal_ids: [201, 202] }],
        sections: [],
        watchlist: [],
      },
    }),
  );
  renderAs("sales", "/briefing");

  const [card] = await takeawayCards();
  // not B1 (the first signal) beside "AI analysis" (the second): both come from C3
  expect(within(card!).getByText(/admiralty grade c3.*ai analysis/i)).toBeInTheDocument();
  expect(within(card!).getByText("C3")).toBeInTheDocument();
  expect(within(card!).queryByText("B1")).not.toBeInTheDocument();
  expect(within(card!).queryByText("Analyst approved")).not.toBeInTheDocument();
});

it("keeps the signal on screen while the drawer closes, with no flash of skeletons", async () => {
  serveLatest(briefing());
  server.use(
    http.get("/api/v1/signals/:id", ({ params }) =>
      HttpResponse.json(signalDetail({ ...briefingSignals.find((s) => s.id === Number(params.id))! })),
    ),
  );
  renderAs("sales", "/briefing?signal=101");
  const drawer = await screen.findByRole("dialog", { name: /sonatype bundles repository firewall/i });
  expect(await within(drawer).findByRole("link", { name: "Open full analysis →" })).toBeInTheDocument();

  const skeletons: boolean[] = [];
  const watch = new MutationObserver(() => skeletons.push(Boolean(screen.queryByRole("status", { name: /loading signal/i }))));
  watch.observe(document.body, { childList: true, subtree: true, attributes: true });
  await userEvent.click(screen.getByRole("button", { name: /^close$/i }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  watch.disconnect();
  expect(skeletons).not.toContain(true);
});

it("reads the master edition for analysts, with no fallback note", async () => {
  renderEdition("analyst");
  expect(await screen.findByRole("heading", { name: HEADLINE })).toBeInTheDocument();
  expect(nameplate()).toHaveTextContent("Master edition");
  expect(screen.queryByText(/showing the master/i)).not.toBeInTheDocument();
});

it("notes quietly when the reader's edition was not filed and master is shown", async () => {
  serveLatest(briefing({ edition: "master", requested_edition: "sales" }));
  renderAs("sales", "/briefing");
  expect(await screen.findByRole("heading", { name: HEADLINE })).toBeInTheDocument();
  // The header still names the reader's edition; the note says what stands in for it.
  expect(nameplate()).toHaveTextContent("Sales edition");
  expect(screen.getByText(/sales edition wasn't filed.*; showing the master/i)).toBeInTheDocument();
  const [lead] = await takeawayCards();
  expect(storyLink(lead!)).toHaveAttribute("href", "/signals/101");
});

it("opens the quick look over the briefing from ?signal=, and closing clears it", async () => {
  serveLatest(briefing());
  server.use(
    http.get("/api/v1/signals/:id", ({ params }) =>
      HttpResponse.json(signalDetail({ ...briefingSignals.find((s) => s.id === Number(params.id))! })),
    ),
  );
  const app = renderAs("sales", "/briefing?signal=101");

  const drawer = await screen.findByRole("dialog", { name: /sonatype bundles repository firewall into nexus one/i });
  // The quick look: the stage-A fact (no analysis yet, marked), this edition's action, the sources, the way to the page.
  expect(await within(drawer).findByText(/now includes repository firewall in every nexus one subscription/i)).toBeInTheDocument();
  expect(within(drawer).getByText("Full analysis pending")).toBeInTheDocument();
  expect(within(drawer).getByText(/show curation's policy audit trail/i)).toBeInTheDocument();
  expect(within(drawer).getByRole("link", { name: /introducing nexus one with repository firewall/i })).toHaveAttribute(
    "href",
    "https://www.sonatype.com/blog/nexus-one",
  );
  expect(within(drawer).getByRole("link", { name: "Open full analysis →" })).toHaveAttribute("href", "/signals/101");

  await userEvent.click(screen.getByRole("button", { name: /^close$/i }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(app.location()).toMatchObject({ pathname: "/briefing", search: "" });
});

it("opens the drawer from ?signal= on any route, through the one app-level host", async () => {
  server.use(
    http.get("/api/v1/signals/:id", ({ params }) =>
      HttpResponse.json(signalDetail({ ...briefingSignals.find((s) => s.id === Number(params.id))! })),
    ),
  );
  renderAs("pm", "/admin/runs?signal=101");

  const drawer = await screen.findByRole("dialog", { name: /sonatype bundles repository firewall into nexus one/i });
  // A deep link outside the briefing shows the reader's own edition's action line (pm).
  expect(await within(drawer).findByText(/compare curation's coverage of npm and pypi/i)).toBeInTheDocument();
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
});

it("opens the full analysis from the quick look in the drawer's place, so Back returns to the briefing", async () => {
  serveLatest(briefing());
  server.use(
    http.get("/api/v1/signals/:id", ({ params }) =>
      HttpResponse.json(signalDetail({ ...briefingSignals.find((s) => s.id === Number(params.id))! })),
    ),
  );
  const app = renderAs("sales", "/briefing");
  expect(await screen.findByRole("heading", { name: HEADLINE })).toBeInTheDocument();

  // The breaking toast opens the quick look over the briefing (its stories open the page itself).
  act(() =>
    MockEventSource.emit("signal.breaking", {
      signal_id: 101,
      title: "Sonatype bundles Repository Firewall into Nexus One",
      importance: 88,
      competitors: ["Sonatype"],
    }),
  );
  await userEvent.click(await screen.findByRole("button", { name: /^open$/i }));
  const drawer = await screen.findByRole("dialog", { name: /sonatype bundles repository firewall/i });
  await userEvent.click(await within(drawer).findByRole("link", { name: "Open full analysis →" }));
  expect(await screen.findByRole("heading", { level: 1, name: /sonatype bundles repository firewall/i })).toBeInTheDocument();
  expect(app.location()).toMatchObject({ pathname: "/signals/101", search: "" });

  await userEvent.click(screen.getByRole("button", { name: /^back$/i }));
  expect(await screen.findByRole("heading", { name: HEADLINE })).toBeInTheDocument();
  expect(app.location()).toMatchObject({ pathname: "/briefing", search: "" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  // Sonner's store outlives a render: leave no toast behind for the next test.
  act(() => void toast.dismiss());
});

it("shows a quiet day with a link to the last full briefing", async () => {
  serveLatest(quietBriefing());
  server.use(
    http.get("/api/v1/briefings", () =>
      HttpResponse.json([
        briefingSummary({ date: "2026-10-18", status: "quiet", headline: "No significant moves" }),
        briefingSummary({ date: "2026-10-17", status: "published" }),
      ]),
    ),
  );
  renderAs("sales", "/briefing");

  expect(await screen.findByRole("heading", { name: /no significant moves/i })).toBeInTheDocument();
  const previous = await screen.findByRole("link", { name: new RegExp(HEADLINE) });
  expect(previous).toHaveAttribute("href", "/briefing/2026-10-17");
});

it("explains a briefing skipped at the daily LLM cap, with the API's amounts, and how to raise it", async () => {
  serveLatest(skippedBriefing());
  server.use(
    http.get("/api/v1/costs", () =>
      HttpResponse.json(costs({ today_usd: 1.25, daily_budget_usd: 1.25, total_usd: 4.1, total_budget_usd: 12 })),
    ),
  );
  renderAs("sales", "/briefing");

  expect(await screen.findByRole("heading", { name: /briefing skipped/i })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: /the day's ai budget ran out/i })).toBeInTheDocument();
  expect(screen.getByText(/spent its daily ai budget of \$1\.25/i)).toBeInTheDocument();
  expect(screen.getByText(/total cap of \$12\.00/i)).toBeInTheDocument();
  expect(screen.getByText("LLM_DAILY_BUDGET_USD")).toBeInTheDocument();
  expect(screen.getByText("python -m app.cli.run_daily --max-usd 2.50")).toBeInTheDocument();
  expect(screen.queryByText("LLM_TOTAL_BUDGET_USD")).not.toBeInTheDocument();
  // A master-only placeholder is not an edition fallback worth flagging.
  expect(screen.queryByText(/showing the master edition/i)).not.toBeInTheDocument();
});

it("says when the total LLM cap is spent and that only LLM_TOTAL_BUDGET_USD lifts it", async () => {
  serveLatest(skippedBriefing());
  server.use(
    http.get("/api/v1/costs", () =>
      HttpResponse.json(costs({ today_usd: 0.4, daily_budget_usd: 0.75, total_usd: 9, total_budget_usd: 9 })),
    ),
  );
  renderAs("analyst", "/briefing");

  expect(await screen.findByRole("heading", { name: /the total ai budget is spent/i })).toBeInTheDocument();
  expect(screen.getByText(/spent its total ai budget: \$9\.00 of \$9\.00/i)).toBeInTheDocument();
  expect(screen.getByText("LLM_TOTAL_BUDGET_USD")).toBeInTheDocument();
  // Neither the daily cap nor a catch-up run would help, so neither is offered.
  expect(screen.queryByText("LLM_DAILY_BUDGET_USD")).not.toBeInTheDocument();
  expect(screen.queryByText(/--max-usd/)).not.toBeInTheDocument();
  expect(screen.queryByText(/daily ai budget of/i)).not.toBeInTheDocument();
});

it("names both caps without picking one when costs are unavailable", async () => {
  serveLatest(skippedBriefing());
  server.use(http.get("/api/v1/costs", () => new HttpResponse(null, { status: 500 })));
  renderAs("sales", "/briefing");

  expect(await screen.findByRole("heading", { name: /an ai budget cap was reached/i })).toBeInTheDocument();
  expect(screen.getByText("LLM_DAILY_BUDGET_USD")).toBeInTheDocument();
  expect(screen.getByText("LLM_TOTAL_BUDGET_USD")).toBeInTheDocument();
  expect(screen.getByText(/a ci analyst can change them/i)).toBeInTheDocument();
  expect(screen.queryByText(/daily ai budget of/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/total ai budget is spent/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/\$\d/)).not.toBeInTheDocument();
});

it("stays neutral about which cap stopped an older skipped day", async () => {
  server.use(
    http.get("/api/v1/briefings/:date", () => HttpResponse.json(skippedBriefing({ date: "2026-10-16" }))),
    http.get("/api/v1/briefings", () =>
      HttpResponse.json([briefingSummary({ date: "2026-10-17" }), briefingSummary({ date: "2026-10-16", status: "skipped_budget" })]),
    ),
  );
  renderAs("sales", "/briefing/2026-10-16");

  // Today's spend says nothing about that day: no cause is asserted, both caps are named.
  expect(await screen.findByRole("heading", { name: /an ai budget cap was reached/i })).toBeInTheDocument();
  expect(screen.getByText("LLM_DAILY_BUDGET_USD")).toBeInTheDocument();
  expect(screen.getByText("LLM_TOTAL_BUDGET_USD")).toBeInTheDocument();
});

it("shows enable-AI empty state", async () => {
  server.use(http.get("/api/v1/agent-runs", () => HttpResponse.json([agentRun({ notes: "llm_disabled", funnel: null })])));
  renderAs("sales", "/briefing");

  expect(await screen.findByText(/add openai_api_key to enable ai analysis/i)).toBeInTheDocument();
});

it("offers analysts a run when there is no briefing yet, and explains calmly to everyone else", async () => {
  server.use(
    http.post("/api/v1/runs", () => HttpResponse.json({ task_id: "t-1", mode: "full" }, { status: 202 })),
  );
  const first = renderAs("analyst", "/briefing");
  await userEvent.click(await screen.findByRole("button", { name: /run the pipeline/i }));
  // the toast describes this page, not the Runs table's "below".
  expect(await screen.findByText("Run started")).toBeInTheDocument();
  expect(screen.getByText(/the briefing appears here when the run files it/i)).toBeInTheDocument();
  expect(screen.queryByText(/shows up below/i)).not.toBeInTheDocument();
  first.unmount();

  renderAs("exec", "/briefing");
  expect(await screen.findByText(/your first morning briefing/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /run the pipeline/i })).not.toBeInTheDocument();
});

it("refetches when the stream announces a published briefing", async () => {
  let headline = "Yesterday's view of the market";
  server.use(
    http.get("/api/v1/briefings/latest", () =>
      HttpResponse.json(briefing({ content: { ...briefing().content, headline } })),
    ),
  );
  renderAs("sales", "/briefing");
  expect(await screen.findByRole("heading", { name: headline })).toBeInTheDocument();

  headline = "This morning's view of the market";
  act(() => MockEventSource.emit("briefing.published", { date: "2026-10-18", status: "published" }));

  expect(await screen.findByRole("heading", { name: headline })).toBeInTheDocument();
});

it("toasts a breaking signal calmly and opens it over the current page (/feed), without leaving it", async () => {
  const breaking = signalDetail({
    id: 555,
    title: "GitLab acquires a package security startup",
    stance: "neutral",
    related: [briefingSignals[3]!],
  });
  server.use(
    http.get("/api/v1/signals/:id", ({ params }) =>
      HttpResponse.json(
        Number(params.id) === 555 ? breaking : signalDetail({ ...briefingSignals.find((s) => s.id === Number(params.id))! }),
      ),
    ),
  );
  const app = renderAs("pm", "/feed?q=nexus");
  expect(await screen.findByRole("heading", { name: /^feed$/i })).toBeInTheDocument();

  act(() =>
    MockEventSource.emit("signal.breaking", {
      signal_id: 555,
      title: "GitLab acquires a package security startup",
      importance: 91,
      competitors: ["GitLab"],
    }),
  );

  const toast = await screen.findByText("GitLab acquires a package security startup");
  expect(screen.getByText(/breaking signal · gitlab · importance 91/i)).toBeInTheDocument();
  expect(toast.closest("[data-sonner-toast]")).not.toHaveAttribute("data-type", "error");

  await userEvent.click(screen.getByRole("button", { name: /^open$/i }));
  const drawer = await screen.findByRole("dialog", { name: /gitlab acquires a package security startup/i });
  // The drawer opens over the page the reader is on; the page's own query survives.
  expect(app.location()).toMatchObject({ pathname: "/feed", search: "?q=nexus&signal=555" });

  // Closing returns to that same page, its query intact (never back past it).
  expect(await within(drawer).findByRole("link", { name: "Open full analysis →" })).toHaveAttribute("href", "/signals/555");
  await userEvent.click(screen.getByRole("button", { name: /^close$/i }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(app.location()).toMatchObject({ pathname: "/feed", search: "?q=nexus" });
  expect(screen.getByRole("heading", { name: /^feed$/i })).toBeInTheDocument();
});

/* ---- what draws the eye: today at a glance, the hero, every story's weight ---- */

/** The chart named `title` in today's at-a-glance summary. */
function glanceChart(title: RegExp) {
  const glance = screen.getByRole("complementary", { name: /today at a glance/i });
  return within(glance).getByRole("figure", { name: title });
}

it("sums up the reader's own edition at a glance: stance, who moved and the products in play, with exact values", async () => {
  renderEdition("sales");
  await takeawayCards();

  // Stance: every story on the page (three cards, six rows), counted with its word, never colour alone.
  const stance = glanceChart(/stories by stance/i);
  expect(stance).toHaveTextContent("5 threats, 2 opportunities, 2 neutral");
  expect(within(stance).getAllByRole("img").map((bar) => bar.getAttribute("aria-label"))).toEqual([
    expect.stringMatching(/^Threat: 5 stories/),
    expect.stringMatching(/^Opportunity: 2 stories/),
    expect.stringMatching(/^Neutral: 2 stories/),
  ]);

  // Who moved: competitors by their stories' summed importance, each opening its deep dive.
  const moved = glanceChart(/who moved/i);
  await waitFor(() => expect(moved).toHaveTextContent(/GitHub moved most: 2 stories, importance 132 in all/));
  const links = within(moved).getAllByRole("link");
  expect(links.map((a) => a.getAttribute("href"))).toEqual([
    "/competitors/github",
    "/competitors/gitlab",
    "/competitors/sonatype",
    "/competitors/docker",
    "/competitors/harness",
    "/competitors/socket",
  ]);
  expect(links[0]).toHaveAccessibleName(/^GitHub: 132/);

  // Your products in play: the JFrog products the top stories name, each opening its page.
  const products = glanceChart(/your products in play/i);
  expect(products).toHaveTextContent(/Artifactory is named in 3 of today's top stories/);
  const productLinks = await within(products).findAllByRole("link");
  expect(productLinks[0]).toHaveAttribute("href", "/products/artifactory");
  expect(productLinks[0]).toHaveAccessibleName(/^Artifactory: 3/);
  expect(within(products).getAllByText("Curation").length).toBeGreaterThan(0);

  // The exact values are one disclosure away, in a table.
  expect(within(stance).getByText("Show the numbers")).toBeInTheDocument();
});

it("computes the glance from the reader's own edition, not another one's", async () => {
  // Exec's edition leaves out the OpenSSF story (an opportunity); Product's top stories never name Curation.
  const exec = renderEdition("exec");
  await takeawayCards();
  expect(glanceChart(/stories by stance/i)).toHaveTextContent("5 threats, 1 opportunity, 2 neutral");
  exec.unmount();

  renderEdition("pm");
  await takeawayCards();
  const products = glanceChart(/your products in play/i);
  expect(products).toHaveTextContent(/Artifactory is named in 2 of today's top stories/);
  expect(within(products).queryAllByText("Curation")).toHaveLength(0);
});

it("leads with the top story as a hero: its importance, its stance and its competitor's momentum", async () => {
  server.use(http.get("/api/v1/competitors/overview", () => HttpResponse.json(competitorOverview())));
  renderEdition("sales");
  const [lead, second, third] = await takeawayCards();

  expect(lead).toHaveAttribute("data-variant", "lead");
  expect(within(lead!).getByRole("img", { name: "Importance 88 of 100" })).toBeInTheDocument();
  const chip = lead!.querySelector("[data-stance-chip]")!;
  expect(chip).toHaveTextContent("Threat");
  expect(chip.querySelector("svg")).not.toBeNull();
  // The 13-week momentum of the story's competitor, its latest week printed beside the line.
  expect(await within(lead!).findByRole("img", { name: /^Sonatype momentum, last 13 weeks: 62 to 131/ })).toBeInTheDocument();
  expect(lead).toHaveTextContent(/Momentum\s*131 this week/);

  // Every card carries its weight and stance; so does every row, with its importance.
  expect(within(second!).getByRole("img", { name: "Importance 58 of 100" })).toBeInTheDocument();
  expect(second!.querySelector("[data-stance-chip]")).toHaveTextContent("Threat");
  expect(within(third!).getByRole("img", { name: "Importance 74 of 100" })).toBeInTheDocument();
  const moves = screen.getByRole("region", { name: /competitive moves/i });
  expect(within(moves).getAllByRole("img", { name: /^Importance \d+ of 100$/ }).length).toBeGreaterThan(0);
});

it("keeps a quiet day calm: no charts drawn at zero", async () => {
  serveLatest(quietBriefing());
  renderAs("sales", "/briefing");
  expect(await screen.findByRole("heading", { name: /no significant moves/i })).toBeInTheDocument();
  expect(screen.queryByRole("figure")).not.toBeInTheDocument();
  expect(screen.queryByRole("complementary", { name: /today at a glance/i })).not.toBeInTheDocument();
});
