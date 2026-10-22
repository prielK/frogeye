import type { Role, Schemas } from "@/lib/api/client";

type Competitor = Schemas["CompetitorOut"];
type DocumentOut = Schemas["DocumentOut"];
type SourceOut = Schemas["SourceOut"];
type RunOut = Schemas["RunOut"];
type SignalCard = Schemas["SignalCard"];
type SignalDetail = Schemas["SignalDetail"];
type BriefingOut = Schemas["BriefingOut"];
type BriefingSummary = Schemas["BriefingSummary"];
type AgentRunOut = Schemas["AgentRunOut"];
type AgentStepOut = Schemas["AgentStepOut"];
type CostsOut = Schemas["CostsOut"];
type CapabilityCategoryOut = Schemas["CapabilityCategoryOut"];
type KitOut = Schemas["KitOut"];
type SearchHitOut = Schemas["SearchHitOut"];
type SignalAnalysisOut = Schemas["SignalAnalysisOut"];
type CitedSourceOut = Schemas["CitedSourceOut"];

/** The app's time zone, as the API delivers it on the signed-in user. */
export const APP_TIME_ZONE = "Asia/Jerusalem";

export const personaFor: Record<Role, Schemas["UserOut"]> = {
  analyst: { id: 1, name: "Avery Cohen", email: "analyst@frogeye.local", role: "analyst", timezone: APP_TIME_ZONE },
  sales: { id: 2, name: "Sam Rivera", email: "sales@frogeye.local", role: "sales", timezone: APP_TIME_ZONE },
  pm: { id: 3, name: "Priya Shah", email: "pm@frogeye.local", role: "pm", timezone: APP_TIME_ZONE },
  exec: { id: 4, name: "Eli Morgan", email: "exec@frogeye.local", role: "exec", timezone: APP_TIME_ZONE },
  pmm_devrel: { id: 5, name: "Noa Bar", email: "pmm@frogeye.local", role: "pmm_devrel", timezone: APP_TIME_ZONE },
};

function competitor(id: number, slug: string, name: string, tier: number, relationship = "competitor"): Competitor {
  return {
    id,
    slug,
    name,
    tier,
    relationship,
    is_self: slug === "jfrog",
    website: null,
    ticker: null,
    description: null,
    logo_url: null,
  };
}

export const competitors: Competitor[] = [
  competitor(1, "jfrog", "JFrog", 0, "self"),
  competitor(2, "sonatype", "Sonatype", 1),
  competitor(3, "github", "GitHub", 1, "frenemy"),
  competitor(4, "gitlab", "GitLab", 1),
  competitor(5, "docker", "Docker", 2),
];

let nextId = 1000;

export function doc(overrides: Partial<DocumentOut> = {}): DocumentOut {
  const id = overrides.id ?? nextId++;
  return {
    id,
    title: `Document ${id}`,
    url: `https://example.com/news/${id}`,
    source_name: "DevOps.com",
    published_at: "2026-10-17T06:30:00Z",
    status: "relevant",
    scope_hint: "competitor",
    competitors: ["sonatype"],
    matched_terms: ["Nexus Repository"],
    excerpt: "Sonatype today announced a new release of its repository manager.",
    ...overrides,
  };
}

export function source(overrides: Partial<SourceOut> = {}): SourceOut {
  const id = overrides.id ?? nextId++;
  return {
    id,
    kind: "rss",
    name: `Source ${id}`,
    url: `https://example.com/feed/${id}.xml`,
    competitor_slug: null,
    reliability: "B",
    enabled: true,
    health: "ok",
    consecutive_failures: 0,
    last_fetched_at: "2026-10-17T05:00:00Z",
    last_error: null,
    items_7d: 12,
    ...overrides,
  };
}

export function run(overrides: Partial<RunOut> = {}): RunOut {
  const id = overrides.id ?? nextId++;
  return {
    id,
    kind: "ingestion",
    mode: "ingest",
    trigger: "schedule",
    status: "succeeded",
    started_at: "2026-10-17T05:00:00Z",
    finished_at: "2026-10-17T05:04:12Z",
    stats: {
      sources_attempted: 112,
      sources_failed: 3,
      fetched: 1240,
      new_raw: 312,
      documents_created: 298,
      duplicates: 20,
      prefilter_passed: 41,
      prefilter_rejected: 257,
      metrics_written: 14,
    },
    error: null,
    agent_run_id: null,
    active: false,
    ...overrides,
  };
}

export function signalCard(overrides: Partial<SignalCard> = {}): SignalCard {
  const id = overrides.id ?? nextId++;
  return {
    id,
    title: `Signal ${id}`,
    scope: "competitor",
    status: "auto",
    signal_type: "product_launch",
    stance: "neutral",
    importance: 64,
    reliability: "B",
    credibility: 2,
    grade: "B2",
    competitors: ["sonatype"],
    fact: "Sonatype announced a new release of its repository manager.",
    impact: "It narrows a gap with Artifactory on enterprise features.",
    role_actions: { sales: "", pm: "", exec: "", pmm_devrel: "" },
    first_seen_at: "2026-10-17T05:40:00Z",
    primary_url: `https://example.com/news/${id}`,
    primary_source: "DevOps.com",
    summary: null,
    // a card with a summary has its analysis; one without is queued for one
    analysis_state: overrides.summary ? "current" : "pending",
    ...overrides,
  };
}

/**
 * A scored signal's breakdown, as the score node stores it: importance 62 by default. Given
 * another `importance` (a signal's own, so "Why ranked here" agrees with the page header),
 * the factor values scale toward it (capped at 1) and the freshness multiplier closes the
 * rest, so the bars still add up to the number printed beneath them.
 */
export function scoreBreakdown(importance?: number): Record<string, unknown> {
  const parts: [string, number, number][] = [
    ["impact", 0.8, 0.3],
    ["novelty", 0.6, 0.15],
    ["competitor_tier", 1, 0.15],
    ["type", 0.9, 0.15],
    ["kit_priority", 0.7, 0.1],
    ["credibility", 0.8, 0.1],
    ["corroboration", 0.33, 0.05],
  ];
  const DECAY = 0.79;
  const base = parts.reduce((sum, [, value, weight]) => sum + value * weight, 0) * 100 * DECAY;
  const scale = importance === undefined ? 1 : importance / base;
  const scaled = parts.map(([name, value, weight]) => [name, Math.min(1, Math.round(value * scale * 100) / 100), weight] as const);
  const points = scaled.reduce((sum, [, value, weight]) => sum + value * weight, 0) * 100;
  const decay = importance === undefined ? DECAY : Math.round((importance / points) * 100) / 100;
  const components = Object.fromEntries(
    scaled.map(([name, value, weight]) => [name, { value, weight, contribution: value * weight }]),
  );
  return { components, decay, age_days: 1.02, importance: importance ?? 62 };
}

/** The two member documents of `signalDetail`, primary first. */
const DETAIL_DOCUMENTS: SignalDetail["documents"] = [
  {
    id: 9001,
    title: "Introducing Nexus One with Repository Firewall",
    url: "https://www.sonatype.com/blog/nexus-one",
    source_name: "Sonatype blog",
    published_at: "2026-10-17T04:10:00Z",
    is_primary: true,
  },
  {
    id: 9002,
    title: "Sonatype folds its firewall into Nexus One",
    url: "https://devops.com/sonatype-nexus-one",
    source_name: "DevOps.com",
    published_at: "2026-10-17T07:30:00Z",
    is_primary: false,
  },
];

export function signalDetail(overrides: Partial<SignalDetail> = {}): SignalDetail {
  const card = signalCard(overrides);
  const documents = overrides.documents ?? DETAIL_DOCUMENTS;
  return {
    ...card,
    score_breakdown: scoreBreakdown(card.importance),
    evidence: [
      {
        quote: "Repository Firewall is now included in every Nexus One subscription.",
        verified: true,
        document_id: 9001,
        url: "https://www.sonatype.com/blog/nexus-one",
        source_name: "Sonatype blog",
      },
      {
        quote: "Analysts expect the bundle to pressure Artifactory renewals.",
        verified: false,
        document_id: 9002,
        url: "https://devops.com/sonatype-nexus-one",
        source_name: "DevOps.com",
      },
    ],
    documents,
    capabilities: [{ slug: "supply-chain-security", name: "Supply chain security" }],
    kits: [{ slug: "kit-2", title: "Where do competitors bundle security?" }],
    related: [],
    analysis: null,
    analysis_state: "pending",
    // As the API numbers them: every member document, primary first.
    sources: documents.map((d, i) => ({
      n: i + 1,
      kind: "document" as const,
      ref: `doc:${d.id}`,
      title: d.title ?? `Untitled page on ${new URL(d.url).hostname}`,
      url: d.url,
      outlet: d.source_name,
      published_at: d.published_at,
      is_primary: d.is_primary,
    })),
    ...overrides,
  };
}


/**
 * The numbered Sources list of signal 101's analysis, as the API builds it: the two member
 * documents of `signalDetail` (primary first), then a cited JFrog docs page, a cited signal
 * the reader may open (104) and one hidden from them (shown as "Signal not available", no
 * link). Every `n` and every ref is distinct, and no signal id repeats the story's own.
 */
export const analysisSources: CitedSourceOut[] = [
  {
    n: 1,
    kind: "document",
    ref: "doc:9001",
    title: "Introducing Nexus One with Repository Firewall",
    url: "https://www.sonatype.com/blog/nexus-one",
    outlet: "Sonatype blog",
    published_at: "2026-10-17T04:10:00Z",
    is_primary: true,
  },
  {
    n: 2,
    kind: "document",
    ref: "doc:9002",
    title: "Sonatype folds its firewall into Nexus One",
    url: "https://devops.com/sonatype-nexus-one",
    outlet: "DevOps.com",
    published_at: "2026-10-17T07:30:00Z",
    is_primary: false,
  },
  {
    n: 3,
    kind: "kb",
    ref: "kb:31",
    title: "JFrog Curation overview",
    url: "https://jfrog.com/help/r/jfrog-curation",
    outlet: "JFrog documentation",
    published_at: null,
    is_primary: false,
  },
  {
    n: 4,
    kind: "signal",
    ref: "signal:104",
    title: "GitLab tests a dependency proxy for Maven",
    url: "/signals/104",
    outlet: null,
    published_at: null,
    is_primary: false,
  },
  {
    n: 5,
    kind: "signal",
    ref: "signal:88",
    title: "Signal not available",
    url: null,
    outlet: null,
    published_at: null,
    is_primary: false,
  },
];

/** A full stage-B analysis of signal 101 (Sonatype bundles Repository Firewall), cited into `analysisSources`. */
export function signalAnalysis(overrides: Partial<SignalAnalysisOut> = {}): SignalAnalysisOut {
  return {
    summary: {
      text: "Sonatype now includes Repository Firewall in every Nexus One subscription at no extra cost. Nexus customers get pre-download malware blocking without a second purchase, which removes the price gap JFrog Curation has used in competitive deals.",
      cites: [1, 2],
    },
    what_happened: [
      {
        text: "Sonatype announced on 17 October that Repository Firewall, until now a separate add-on, is part of every Nexus One subscription. The bundle covers npm, PyPI and Maven Central, and existing customers get it at their next renewal.",
        cites: [1],
      },
      {
        text: "DevOps.com reports that Sonatype will not raise Nexus One list prices this year, and quotes an analyst who expects the bundle to pressure Artifactory renewals in the mid-market.",
        cites: [2],
      },
    ],
    background: [
      {
        text: "GitLab is testing a dependency proxy for Maven, another rival folding supply-chain controls into its registry instead of selling them separately.",
        cites: [4],
      },
      { text: "An earlier report described a registry vendor adding malware scanning to its base tier.", cites: [5] },
    ],
    what_changed: [
      { text: "Malware blocking moves from a paid Nexus add-on into the base Nexus One subscription.", cites: [1] },
      { text: "Sonatype now competes with Curation on bundle value rather than as a separate line item.", cites: [1, 3] },
    ],
    jfrog_impact: [
      {
        product: "Curation",
        effect: "threat",
        severity: "high",
        rationale: "Firewall in the base bundle removes the separate purchase that made Curation's price advantage visible in Nexus deals.",
        cites: [1, 3],
      },
      {
        product: "Artifactory",
        effect: "threat",
        severity: "medium",
        rationale: "Nexus One renewals become cheaper to keep than an Artifactory migration that adds Curation.",
        cites: [2],
      },
      {
        product: "Xray",
        effect: "neutral",
        severity: "low",
        rationale: "Firewall blocks packages before download; scanning what is already stored is untouched by the bundle.",
        cites: [1, 3],
      },
    ],
    competitive_implications: [
      {
        text: "The bundle puts Nexus One in direct competition with Artifactory plus Curation on total price for teams most worried about malicious packages, such as regulated enterprises with heavy npm and PyPI use.",
        cites: [1, 2],
      },
      {
        text: "Sonatype gains a renewal argument across its installed base. JFrog keeps its edge where buyers need more than npm, PyPI and Maven, since the bundle names only those three ecosystems.",
        cites: [1, 3],
      },
    ],
    recommended_actions: {
      sales: {
        talk_track: ["We block malicious packages before they reach developers, and Curation keeps a policy audit trail for every decision."],
        discovery_questions: [
          "How do you block malicious packages today, and who reviews the exceptions?",
          "Which ecosystems beyond npm, PyPI and Maven do your teams pull from?",
        ],
        objections: [
          {
            objection: "Nexus One now includes a firewall, so why would we pay for Curation?",
            response: "Curation's policies cover every package type Artifactory proxies, not only npm, PyPI and Maven Central.",
          },
        ],
        landmines: ["Ask Sonatype which ecosystems Repository Firewall covers beyond npm, PyPI and Maven Central."],
      },
      pm: {
        assessment: ["Parity on pre-download blocking for npm, PyPI and Maven; Curation's wider ecosystem coverage is not matched by the bundle."],
        follow_ups: ["Compare Curation's supported ecosystems with Firewall's published list and share the gap with Sales."],
      },
      exec: {
        implications: ["Bundling turns malicious-package blocking into a base registry feature, which pressures Curation's pricing as an add-on."],
        decisions: ["Decide whether Curation needs a packaging answer before the Q1 renewal cycle."],
        risk_level: "medium",
      },
      pmm_devrel: {
        messaging: ["For Nexus shops: coverage beyond three ecosystems, with an audit trail for every block."],
        content_ideas: ["Blog: What a dependency firewall should block beyond npm and PyPI", "Demo: Curation policies on Docker and Go packages"],
      },
    },
    risks_opportunities: [
      {
        kind: "risk",
        text: "Mid-market Nexus customers may drop Curation from Artifactory migration plans now that Firewall costs them nothing extra.",
        cites: [2],
      },
      { kind: "opportunity", text: "Teams pulling from ecosystems the bundle does not name are open to a broader policy story.", cites: [1, 3] },
    ],
    what_to_watch: [
      { text: "Existing Nexus One customers receive Firewall at renewal.", date: "2026-11-01", cites: [1] },
      { text: "Whether Sonatype extends Firewall to Go modules and container images.", date: null, cites: [1] },
    ],
    confidence_note:
      "Confirmed by Sonatype's own announcement and one independent outlet. The effect on renewals is an analyst's expectation, not yet observed.",
    ...overrides,
  };
}

/** Signal 101 with a current analysis, its numbered sources and two related signals. */
export function analysedSignal(overrides: Partial<SignalDetail> = {}): SignalDetail {
  const analysis = overrides.analysis === undefined ? signalAnalysis() : overrides.analysis;
  return signalDetail({
    ...briefingSignals[0]!,
    summary: analysis?.summary.text ?? null,
    analysis,
    analysis_state: "current",
    sources: analysisSources,
    related: [briefingSignals[1]!, briefingSignals[4]!],
    ...overrides,
  });
}

/** The signals the fixture briefing files (ids 101-109). */
export const briefingSignals: SignalCard[] = [
  signalCard({
    id: 101,
    title: "Sonatype bundles Repository Firewall into Nexus One",
    signal_type: "pricing_change",
    stance: "threat",
    importance: 88,
    grade: "B1",
    reliability: "B",
    credibility: 1,
    status: "approved",
    competitors: ["sonatype"],
    fact: "Sonatype now includes Repository Firewall in every Nexus One subscription at no extra cost.",
    impact: "Nexus shops get pre-download malware blocking without a second purchase, the gap JFrog Curation fills today.",
    role_actions: {
      sales: "In Nexus renewals, ask how they block malicious packages today and show Curation's policy audit trail.",
      pm: "Compare Curation's coverage of npm and PyPI with Firewall's published ecosystem list.",
      exec: "",
      pmm_devrel: "Publish a side-by-side of Curation and Firewall policy controls before KubeCon.",
    },
    primary_source: "Sonatype blog",
  }),
  signalCard({
    id: 102,
    title: "GitHub Packages adds a self-hosted registry for Actions runners",
    signal_type: "product_launch",
    stance: "threat",
    importance: 74,
    grade: "A2",
    reliability: "A",
    credibility: 2,
    competitors: ["github"],
    primary_source: "GitHub Changelog",
  }),
  signalCard({
    id: 103,
    title: "Docker Hardened Images reach general availability",
    signal_type: "product_launch",
    stance: "opportunity",
    importance: 61,
    grade: "B2",
    competitors: ["docker"],
    primary_source: "Docker blog",
  }),
  signalCard({
    id: 104,
    title: "GitLab tests a dependency proxy for Maven",
    signal_type: "feature_update",
    stance: "neutral",
    importance: 47,
    grade: "C3",
    reliability: "C",
    credibility: 3,
    competitors: ["gitlab"],
    primary_source: "GitLab issue tracker",
  }),
  signalCard({
    id: 105,
    title: "GitHub raises Packages storage limits for Enterprise Cloud",
    signal_type: "pricing_change",
    stance: "threat",
    importance: 58,
    grade: "A1",
    reliability: "A",
    credibility: 1,
    competitors: ["github"],
    primary_source: "GitHub Changelog",
  }),
  signalCard({
    id: 106,
    title: "OpenSSF publishes a draft attestation format for package registries",
    scope: "industry",
    signal_type: "standard_spec",
    stance: "opportunity",
    importance: 41,
    grade: "B2",
    competitors: [],
    primary_source: "OpenSSF",
  }),
  signalCard({
    id: 107,
    title: "Harness adds a built-in artifact cache to its CI pipelines",
    signal_type: "feature_update",
    stance: "threat",
    importance: 52,
    grade: "B2",
    competitors: ["harness"],
    primary_source: "Harness blog",
  }),
  signalCard({
    id: 108,
    title: "Socket raises a Series C to expand its dependency firewall",
    signal_type: "funding",
    stance: "neutral",
    importance: 44,
    grade: "B3",
    reliability: "B",
    credibility: 3,
    competitors: ["socket"],
    primary_source: "TechCrunch",
  }),
  signalCard({
    id: 109,
    title: "GitLab repositions its package registry as an artifact platform",
    signal_type: "messaging_change",
    stance: "threat",
    importance: 55,
    grade: "B2",
    competitors: ["gitlab"],
    primary_source: "GitLab blog",
  }),
];

/** One run's funnel: the stages narrow, and fewer signals are analysed than were filed. */
export const FUNNEL = {
  fetched: 412,
  documents_created: 180,
  prefilter_passed: 96,
  triaged: 96,
  relevant: 61,
  signals_new: 14,
  signals_analyzed: 9,
  signals_breaking: 1,
  briefing_status: "published",
  runs: 1,
};

/*
 * Funnels shaped like the live data, where the stages do not line up as neatly:
 * the first run filed 224 new signals but its cap analysed 120, and its briefing placed 2;
 * the same-day re-run read 153 items, judged none relevant, filed no new signal and spent
 * its money analysing the 104 carried over. A briefing's funnel is its DAY's.
 */
export const LIVE_FUNNEL_RUN1 = {
  fetched: 5139,
  documents_created: 4810,
  prefilter_passed: 4659,
  triaged: 310,
  relevant: 233,
  signals_new: 224,
  signals_analyzed: 120,
  signals_breaking: 0,
  briefing_status: "published",
  runs: 1,
};
export const LIVE_FUNNEL_RUN2 = {
  fetched: 153,
  documents_created: 2,
  prefilter_passed: 2,
  triaged: 2,
  relevant: 0,
  signals_new: 0,
  signals_analyzed: 104,
  signals_breaking: 0,
  briefing_status: "published",
  runs: 1,
};
export const LIVE_DAY_FUNNEL = {
  fetched: 5292,
  documents_created: 4812,
  prefilter_passed: 4661,
  triaged: 312,
  relevant: 233,
  signals_new: 224,
  signals_analyzed: 224,
  signals_breaking: 0,
  briefing_status: "published",
  runs: 2,
};

type BriefingContent = BriefingOut["content"];
type Takeaway = BriefingContent["top_takeaways"][number];
type Theme = BriefingContent["sections"][number]["theme"];
type SectionItem = BriefingContent["sections"][number]["items"][number];
type ActionRole = Schemas["RoleAction"]["role"];
type Edition = import("@/lib/roles").Edition;
type Level = NonNullable<Takeaway["risk_level"]>;

const ACTION_ROLES: ActionRole[] = ["sales", "pm", "exec", "pmm_devrel"];

/** The section order (backend briefing_schema.THEMES). */
const THEMES: Theme[] = ["Competitive moves", "Product & releases", "Market & money", "Industry pulse", "Community pulse"];

/**
 * Up to 3 actions for one edition, as the newsroom copies them (backend newsroom/fill.py): the
 * edition role's own in reading order, or on the master the first of each role that has one.
 */
function editionActions(byRole: Partial<Record<ActionRole, string[]>>, edition: string): Takeaway["actions"] {
  if (edition === "master") {
    return ACTION_ROLES.flatMap((role) => (byRole[role]?.[0] ? [{ role, text: byRole[role]![0]! }] : [])).slice(0, 3);
  }
  const role = edition as ActionRole;
  return (byRole[role] ?? []).slice(0, 3).map((text) => ({ role, text }));
}

/** Signal 101's analysis actions, flattened per role in the newsroom's order. */
const SONATYPE_ACTIONS: Record<ActionRole, string[]> = {
  sales: [
    "We block malicious packages before they reach developers, and Curation keeps a policy audit trail for every decision.",
    "How do you block malicious packages today, and who reviews the exceptions?",
    "Which ecosystems beyond npm, PyPI and Maven do your teams pull from?",
    "Objection: Nexus One now includes a firewall, so why would we pay for Curation? → Response: Curation's policies cover every package type Artifactory proxies, not only npm, PyPI and Maven Central.",
  ],
  pm: [
    "Parity on pre-download blocking for npm, PyPI and Maven; Curation's wider ecosystem coverage is not matched by the bundle.",
    "Compare Curation's supported ecosystems with Firewall's published list and share the gap with Sales.",
  ],
  exec: [
    "Bundling turns malicious-package blocking into a base registry feature, which pressures Curation's pricing as an add-on.",
    "Decide whether Curation needs a packaging answer before the Q1 renewal cycle.",
  ],
  pmm_devrel: [
    "For Nexus shops: coverage beyond three ecosystems, with an audit trail for every block.",
    "Blog: What a dependency firewall should block beyond npm and PyPI",
    "Demo: Curation policies on Docker and Go packages",
  ],
};

/** Signal 102's: no sales objection, so the Sales card leads with its talk track. */
const GITHUB_ACTIONS: Record<ActionRole, string[]> = {
  sales: [
    "Say: “Artifactory keeps every format your teams build beside the runners that build them, in any cloud.”",
    "Which package types beyond npm and container images do your teams publish today?",
  ],
  pm: [
    "Gap: Packages now sits beside Actions runners; Artifactory needs its own runner integration to match the setup.",
    "Scope a self-hosted runner integration for Artifactory against the Packages beta.",
  ],
  exec: [
    "GitHub is making Packages the default home for build output, which narrows Artifactory's entry point in GitHub-first teams.",
    "Decide whether an Actions-native Artifactory setup is a funded priority this quarter.",
  ],
  pmm_devrel: [
    "Artifactory goes where your runners go: every format, replicated to every cloud.",
    "Show an Actions workflow that publishes to Artifactory and replicates to a second cloud.",
  ],
};

/** Signal 103 has no analysis yet: its stage-A role lines (no exec line). */
const DOCKER_STAGE_A: Partial<Record<ActionRole, string[]>> = {
  sales: ["Ask which base images the team trusts today and how they verify them."],
  pm: ["Check which Docker Hardened Images signatures Xray can verify today."],
  pmm_devrel: ["Write up how to mirror Docker Hardened Images into Artifactory."],
};

/**
 * One selected story as the newsroom fills it (backend newsroom/fill.py): its card body (the
 * JFrog line, `why`, on the master), its row as a section item (the master's in every edition),
 * and per role its analysis actions in reading order. `angle` points at the action a role's
 * card leads with instead of the JFrog line: the first objection (else talk-track line) for
 * Sales, the first assessment, implication or messaging line for the others; that action then
 * leaves the card's "What to do". A story without an analysis (stage A) has no angle and no
 * risk level.
 */
type Story = {
  id: number;
  /** The takeaway title the editor (or an edition's adapter) wrote. */
  title: string;
  confidence: Takeaway["confidence"];
  summary: string;
  why: string;
  products: Takeaway["products"];
  fromAnalysis: boolean;
  actions: Partial<Record<ActionRole, string[]>>;
  angle?: Partial<Record<ActionRole, number>>;
  /** The analysis's exec risk level: Exec cards show it as a chip. */
  risk?: Level;
  theme: Theme;
};

const STORIES: Story[] = [
  {
    id: 101,
    title: "Sonatype makes its dependency firewall part of Nexus One",
    confidence: "high",
    summary: signalAnalysis().summary.text,
    why: "Firewall in the base bundle removes the separate purchase that made Curation's price advantage visible in Nexus deals.",
    products: [
      { product: "Curation", effect: "threat", severity: "high" },
      { product: "Artifactory", effect: "threat", severity: "medium" },
      { product: "Xray", effect: "neutral", severity: "low" },
    ],
    fromAnalysis: true,
    actions: SONATYPE_ACTIONS,
    angle: { sales: 3, pm: 0, exec: 0, pmm_devrel: 0 },
    risk: "medium",
    theme: "Competitive moves",
  },
  {
    id: 102,
    title: "GitHub opens Packages to self-hosted runners",
    confidence: "medium",
    summary:
      "GitHub Packages can now run as a self-hosted registry beside Actions runners, in public beta for Enterprise Cloud. Teams that build on Actions can keep artifacts next to the runners that build them, which removes one reason to add a separate repository manager.",
    why: "It competes for Artifactory's entry point in GitHub-first teams, though it stores only the formats Packages already supports.",
    products: [
      { product: "Artifactory", effect: "threat", severity: "high" },
      { product: "Distribution", effect: "opportunity", severity: "medium" },
    ],
    fromAnalysis: true,
    actions: GITHUB_ACTIONS,
    angle: { sales: 0, pm: 0, exec: 0, pmm_devrel: 0 },
    risk: "high",
    theme: "Product & releases",
  },
  {
    id: 103,
    title: "Docker Hardened Images reach general availability",
    confidence: "medium",
    summary: "Docker Hardened Images, a catalogue of minimal, signed base images, are generally available on Docker Business.",
    why: "Image provenance becomes a buying criterion, which favours registries that store and verify attestations.",
    products: [],
    fromAnalysis: false,
    actions: DOCKER_STAGE_A,
    theme: "Product & releases",
  },
  {
    id: 105,
    title: "GitHub gives Enterprise Cloud more free Packages storage",
    confidence: "medium",
    summary:
      "GitHub raised the Packages storage included with Enterprise Cloud plans. More included storage weakens the cost case for moving GitHub-hosted artifacts into Artifactory.",
    why: "More included storage weakens the cost case for moving GitHub-hosted artifacts into Artifactory.",
    products: [{ product: "Artifactory", effect: "threat", severity: "medium" }],
    fromAnalysis: true,
    actions: {
      sales: [
        "Say: “Included storage covers GitHub's own formats; Artifactory keeps every format and replicates it across clouds.”",
        "How much of your build output lives outside GitHub Packages today?",
        "Objection: GitHub now includes more storage, so why pay for Artifactory? → Response: Storage is the cheap part; Artifactory adds every package type, promotion and replication.",
      ],
      pm: ["Parity on storage only: nothing changes for formats, promotion or replication."],
      exec: ["Free storage lowers the cost of staying on GitHub, a renewal argument against moving artifacts into Artifactory."],
      pmm_devrel: ["Price per gigabyte is not the comparison: show what a stored artifact carries in Artifactory."],
    },
    angle: { sales: 2, pm: 0, exec: 0, pmm_devrel: 0 },
    theme: "Competitive moves",
  },
  {
    id: 107,
    title: "Harness CI caches build output without a repository",
    confidence: "medium",
    summary:
      "Harness CI now caches build dependencies and outputs between pipeline runs without a separate repository. The cache covers the simplest reason teams add a binary repository, though it keeps no promotion history or metadata.",
    why: "A cache inside CI covers the simplest reason teams add a binary repository.",
    products: [{ product: "Artifactory", effect: "threat", severity: "low" }],
    fromAnalysis: true,
    actions: {
      sales: ["Which teams build on Harness CI, and where do their release artifacts go after the pipeline?"],
      pm: [
        "Gap is narrow: the cache matches remote-repository caching for CI, but keeps no promotion, retention or build info.",
        "Benchmark a cached Harness pipeline against Artifactory remote repositories and share the numbers with Sales.",
      ],
    },
    angle: { pm: 0 },
    theme: "Competitive moves",
  },
  {
    id: 109,
    title: "GitLab pitches its registry against Artifactory by name",
    confidence: "medium",
    summary:
      "GitLab now markets its package registry as an artifact management platform, with a landing page that compares it with Artifactory by name. The registry's features are unchanged; the change is in positioning, not capability.",
    why: "The comparison reaches GitLab-first teams before they ever evaluate Artifactory.",
    products: [{ product: "Artifactory", effect: "threat", severity: "medium" }],
    fromAnalysis: true,
    actions: {
      sales: ["Say: “GitLab renamed its registry; it stores what it stored last month.”"],
      pmm_devrel: [
        "Answer the comparison with proof: the formats, replication and promotion history GitLab's page leaves out.",
        "Publish a format-by-format comparison page before GitLab's claim settles in search results.",
        "Brief DevRel to answer the comparison in community threads with a working demo.",
      ],
    },
    angle: { pmm_devrel: 0 },
    theme: "Competitive moves",
  },
  {
    id: 104,
    title: "GitLab tests a dependency proxy for Maven",
    confidence: "low",
    summary: "GitLab opened an issue to proxy Maven dependencies through its package registry.",
    why: "If it ships, GitLab customers could cache Maven Central without adding Artifactory.",
    products: [],
    fromAnalysis: false,
    actions: {},
    theme: "Product & releases",
  },
  {
    id: 108,
    title: "Socket raises a Series C for its dependency firewall",
    confidence: "medium",
    summary:
      "Socket raised a Series C to extend its dependency firewall beyond npm and PyPI. A funded specialist keeps malicious-package defence a crowded category for Curation.",
    why: "A funded specialist keeps malicious-package defence a crowded category for Curation.",
    products: [{ product: "Curation", effect: "threat", severity: "low" }],
    fromAnalysis: true,
    actions: {
      exec: [
        "A funded specialist keeps dependency firewalls a crowded category, though Socket sells to security teams rather than registry owners.",
        "Track whether Socket adds a registry proxy, which would make it a direct Curation rival.",
      ],
    },
    angle: { exec: 0 },
    risk: "low",
    theme: "Market & money",
  },
  {
    id: 106,
    title: "OpenSSF drafts an attestation format for package registries",
    confidence: "medium",
    summary:
      "The OpenSSF published a draft attestation format that package registries could use to record build provenance. A shared format favours registries that already store it, as Artifactory does with build info.",
    why: "A shared format favours registries that already store build provenance.",
    products: [{ product: "Artifactory", effect: "opportunity", severity: "low" }],
    fromAnalysis: true,
    actions: {},
    theme: "Industry pulse",
  },
];

const story = (id: number) => STORIES.find((s) => s.id === id)!;

/**
 * A story as one edition's top takeaway (backend fill.fill_from_analysis): on the master, or
 * when the role has no text of its own (a stage-A story), the JFrog line under angle "jfrog";
 * otherwise the role's angle, verbatim (a Sales objection reads "Objection: … — Response: …"),
 * and that action leaves "What to do". Exec cards with an analysis carry its risk level.
 */
function takeawayCard(s: Story, edition: string, { angled = true } = {}): Takeaway {
  const role = edition === "master" ? null : (edition as ActionRole);
  const at = role && angled ? s.angle?.[role] : undefined;
  const own = role ? (s.actions[role] ?? []) : [];
  const text = at !== undefined ? own[at] : undefined;
  return {
    title: s.title,
    so_what: "",
    now_what: "",
    signal_ids: [s.id],
    confidence: s.confidence,
    summary: s.summary,
    why_it_matters: text ? text.replace(" → Response: ", " — Response: ") : s.why,
    products: s.products,
    actions:
      role && text
        ? editionActions({ [role]: own.filter((_, i) => i !== at) }, role)
        : editionActions(s.actions, edition),
    from_analysis: s.fromAnalysis,
    angle: role && text ? role : "jfrog",
    risk_level: role === "exec" && angled && s.fromAnalysis ? (s.risk ?? null) : null,
  };
}

/** A story as a section row: its signal's title, summary and JFrog line, the master's in every edition. */
function sectionItem(s: Story): SectionItem {
  const signal = briefingSignals.find((c) => c.id === s.id)!;
  return { signal_id: s.id, one_liner: signal.title, why_it_matters: s.why, summary: s.summary, from_analysis: s.fromAnalysis };
}

/** Rows under their themes, in the section order; a theme with no row is left out. */
function sectionsOf(ids: number[]): BriefingContent["sections"] {
  return THEMES.map((theme) => ({ theme, items: STORIES.filter((s) => s.theme === theme && ids.includes(s.id)).map(sectionItem) })).filter(
    (s) => s.items.length > 0,
  );
}

/**
 * What each edition leads with, as the newsroom chooses: the master's top three by
 * importance; each role edition's top three of the master's stories by its role score (backend
 * scoring_config.role_score, Exec scaled by the risk level), among those with an action for the
 * role and at most two per company. Sales leads with pricing (101, 105) and a launch; Product
 * with launches and features (103 is a stage-A card: the JFrog line); Exec with the high-risk
 * launch, then the medium-risk bundle and the low-risk funding round; PMM & DevRel with GitLab's
 * messaging change. Every other story of the day is a row, once (Exec keeps at most five).
 */
const EDITION_PLAN: Record<Edition, { headline: string; lead: string; takeaways: number[]; items: number[] }> = {
  master: {
    headline: "Sonatype and GitHub both move onto JFrog's registry turf",
    lead: "Sonatype is folding its dependency firewall into Nexus One, and GitHub is opening Packages to self-hosted runners. Neither changes list prices yet; both aim at mid-market Artifactory renewals.",
    takeaways: [101, 102, 103],
    items: [105, 107, 109, 104, 108, 106],
  },
  sales: {
    headline: "Sonatype and GitHub both move onto JFrog's registry turf",
    lead: "Sonatype is folding its dependency firewall into Nexus One, and GitHub is giving Enterprise Cloud more free Packages storage. Both hand Nexus and GitHub shops a reason to stay put at renewal.",
    takeaways: [101, 105, 102],
    items: [107, 109, 103, 104, 108, 106],
  },
  pm: {
    headline: "GitHub puts a package registry beside its runners",
    lead: "GitHub Packages now runs self-hosted next to Actions runners, Docker's signed base images are generally available, and Harness CI caches build output without a repository.",
    takeaways: [102, 103, 107],
    items: [101, 105, 109, 104, 108, 106],
  },
  exec: {
    headline: "GitHub and Sonatype press on Artifactory's way in",
    lead: "GitHub is making Packages the default home for build output, and Sonatype is bundling its firewall into Nexus One. Socket's Series C keeps the firewall category crowded.",
    takeaways: [102, 101, 108],
    items: [105, 109, 107, 103, 104],
  },
  pmm_devrel: {
    headline: "GitLab takes aim at Artifactory in its own marketing",
    lead: "GitLab now markets its package registry as an artifact platform and names Artifactory in the comparison, while GitHub and Sonatype each fold a JFrog selling point into their own bundles.",
    takeaways: [109, 102, 101],
    items: [105, 107, 103, 104, 108, 106],
  },
};

/** Each edition's top takeaways, by signal id (the plan above). */
export const EDITION_TAKEAWAYS = Object.fromEntries(Object.entries(EDITION_PLAN).map(([e, p]) => [e, p.takeaways])) as Record<Edition, number[]>;

/**
 * A briefing as the newsroom files it now (one edition per role): every takeaway and
 * section item is ONE signal, no signal appears twice, and card bodies are copied
 * from each signal's analysis (`from_analysis`), or from its stage-A text when it has none
 * (signals 103 and 104, marked on screen). Each edition leads with its own stories and angles
 * (EDITION_PLAN); the master's cards read the JFrog line, its actions the first of each role.
 */
export function briefing(overrides: Partial<BriefingOut> = {}): BriefingOut {
  const edition = overrides.edition ?? "sales";
  const plan = EDITION_PLAN[edition as Edition] ?? EDITION_PLAN.master;
  return {
    id: overrides.id ?? nextId++,
    date: "2026-10-17",
    edition,
    requested_edition: edition,
    version: 1,
    status: "published",
    content: {
      headline: plan.headline,
      lead: plan.lead,
      top_takeaways: plan.takeaways.map((id) => takeawayCard(story(id), edition)),
      sections: sectionsOf(plan.items),
      quiet_competitors: ["cloudsmith"],
      watchlist: [{ signal_id: 104, note: "Whether GitLab's Maven proxy ships in the 18.6 milestone." }],
    },
    funnel: { ...FUNNEL },
    signals: briefingSignals,
    published_at: "2026-10-17T03:12:00Z",
    agent_run_id: 77,
    ...overrides,
  };
}

/** A row as the API returned it before cards had an angle: no `angle` or `risk_level` key. */
function withoutAngle(t: Takeaway): Takeaway {
  const row: Partial<Takeaway> = { ...t };
  delete row.angle;
  delete row.risk_level;
  return row as Takeaway;
}

/**
 * A role edition filed before cards had an angle (before the current card shape): the role's own
 * actions, but every card under the JFrog line, and its rows carry no `angle` or `risk_level`.
 */
export function preAngleBriefing(overrides: Partial<BriefingOut> = {}): BriefingOut {
  const edition = overrides.edition ?? "sales";
  const { content } = briefing({ edition: "master" });
  const top_takeaways = EDITION_PLAN.master.takeaways.map((id) => withoutAngle(takeawayCard(story(id), edition, { angled: false })));
  return briefing({ edition, content: { ...content, top_takeaways }, ...overrides });
}

/**
 * An older briefing: `so_what`/`now_what` only (the new
 * fields at their defaults, `from_analysis` false, no `angle` or `risk_level`), a takeaway
 * resting on two signals, and the duplicates those briefings had: signals 101-103 are
 * takeaways AND section items.
 */
export function legacyBriefing(overrides: Partial<BriefingOut> = {}): BriefingOut {
  const legacy = (t: Pick<Takeaway, "title" | "so_what" | "now_what" | "signal_ids" | "confidence">): Takeaway =>
    withoutAngle({ ...t, summary: "", why_it_matters: "", products: [], actions: [], from_analysis: false, angle: "jfrog", risk_level: null });
  const item = (signal_id: number, one_liner: string, why_it_matters: string) => ({
    signal_id,
    one_liner,
    why_it_matters,
    summary: "",
    from_analysis: false,
  });
  return briefing({
    date: "2026-10-16",
    content: {
      headline: "Sonatype and GitHub both move onto JFrog's registry turf",
      lead: "Sonatype is folding its dependency firewall into Nexus One, and GitHub is opening Packages to self-hosted runners.",
      top_takeaways: [
        legacy({
          title: "Sonatype makes its dependency firewall part of Nexus One",
          so_what: "Nexus customers now get pre-download malware blocking in the base bundle, the gap Curation has filled in competitive deals.",
          now_what: "In Nexus renewals, ask how they block malicious packages today and show Curation's policy audit trail.",
          signal_ids: [101],
          confidence: "high",
        }),
        legacy({
          title: "GitHub opens Packages to self-hosted runners",
          so_what: "Teams that build on GitHub Actions can keep artifacts next to their runners, removing one reason to add Artifactory.",
          now_what: "Lead with multi-format coverage and replication across clouds, which GitHub Packages doesn't offer.",
          signal_ids: [102, 105],
          confidence: "medium",
        }),
        legacy({
          title: "Docker Hardened Images reach general availability",
          so_what: "Curated base images move into Docker's paid tiers, which makes image provenance a buying criterion.",
          now_what: "",
          signal_ids: [103],
          confidence: "medium",
        }),
      ],
      sections: [
        {
          theme: "Competitive moves",
          items: [
            item(101, "Sonatype bundles Repository Firewall into every Nexus One subscription", "Firewall was a paid add-on; bundling it removes Curation's clearest price advantage."),
            item(102, "GitHub Packages adds a self-hosted registry for Actions runners", "Artifacts can now stay on-premises next to the runners that build them."),
          ],
        },
        {
          theme: "Product & releases",
          items: [
            item(103, "Docker Hardened Images reach general availability", "Docker now sells curated, signed base images on its Business tier."),
            item(104, "GitLab tests a dependency proxy for Maven", "An early issue-tracker signal; it would extend GitLab's proxy beyond containers."),
          ],
        },
        {
          theme: "Industry pulse",
          items: [item(106, "OpenSSF publishes a draft attestation format for package registries", "A shared format favours registries that already store build provenance.")],
        },
      ],
      quiet_competitors: ["cloudsmith"],
      watchlist: [{ signal_id: 104, note: "Whether GitLab's Maven proxy ships in the 18.6 milestone." }],
    },
    signals: briefingSignals.slice(0, 6),
    ...overrides,
  });
}

/** A day with nothing that cleared the bar (status quiet). */
export function quietBriefing(overrides: Partial<BriefingOut> = {}): BriefingOut {
  return briefing({
    date: "2026-10-18",
    status: "quiet",
    content: {
      headline: "No significant moves",
      lead: "No new signals cleared the briefing bar since yesterday's briefing.",
      top_takeaways: [],
      sections: [],
      quiet_competitors: ["sonatype", "github", "gitlab"],
      watchlist: [],
    },
    funnel: { ...FUNNEL, relevant: 9, signals_new: 0, briefing_status: "quiet" },
    signals: [],
    ...overrides,
  });
}

/** The master-only placeholder filed when the LLM budget ran out. */
export function skippedBriefing(overrides: Partial<BriefingOut> = {}): BriefingOut {
  return briefing({
    date: "2026-10-18",
    edition: "master",
    requested_edition: "sales",
    status: "skipped_budget",
    content: {
      headline: "Briefing skipped — LLM budget reached",
      lead: "Today's briefing was not written because the LLM budget was reached. Its signals carry over to the next run.",
      top_takeaways: [],
      sections: [],
      quiet_competitors: [],
      watchlist: [],
    },
    signals: [],
    published_at: null,
    ...overrides,
  });
}

export function briefingSummary(overrides: Partial<BriefingSummary> = {}): BriefingSummary {
  return {
    id: overrides.id ?? nextId++,
    date: "2026-10-17",
    edition: "sales",
    requested_edition: "sales",
    version: 1,
    status: "published",
    headline: "Sonatype and GitHub both move onto JFrog's registry turf",
    published_at: "2026-10-17T03:12:00Z",
    ...overrides,
  };
}

export function agentRun(overrides: Partial<AgentRunOut> = {}): AgentRunOut {
  return {
    id: overrides.id ?? nextId++,
    graph: "daily_intel",
    trigger: "schedule",
    status: "succeeded",
    started_at: "2026-10-17T03:00:00Z",
    finished_at: "2026-10-17T03:12:00Z",
    tokens_in: 182_000,
    tokens_out: 21_000,
    cost_usd: 0.31,
    notes: null,
    error: null,
    pipeline_run_id: 12,
    funnel: { ...FUNNEL },
    briefing_date: "2026-10-17",
    ...overrides,
  };
}

/** One step of an agent run's trace; an LLM call unless overridden. */
export function agentStep(overrides: Partial<AgentStepOut> = {}): AgentStepOut {
  const id = overrides.id ?? nextId++;
  return {
    id,
    step_no: overrides.step_no ?? id,
    node: "triage",
    agent: "triage",
    kind: "llm",
    tool_name: null,
    args: null,
    result_summary: null,
    status: "ok",
    error_kind: null,
    model: "gpt-5-nano",
    tokens_in: 1_204,
    cached_tokens: 0,
    tokens_out: 88,
    cost_usd: 0.000096,
    latency_ms: 820,
    created_at: "2026-10-17T03:02:00Z",
    ...overrides,
  };
}

export function costs(overrides: Partial<CostsOut> = {}): CostsOut {
  return {
    today_usd: 0.75,
    total_usd: 4.1,
    daily_budget_usd: 0.75,
    total_budget_usd: 9,
    by_task: [],
    by_model: [],
    last_7_days: [],
    ...overrides,
  };
}

/** A slice of the seeded capability taxonomy (GET /capabilities). */
export const capabilityCategories: CapabilityCategoryOut[] = [
  {
    slug: "artifact-management",
    name: "Artifact management",
    capabilities: [
      { slug: "container-registry", name: "Container registry" },
      { slug: "remote-proxy-caching", name: "Remote proxy & caching" },
    ],
  },
  {
    slug: "ai-ml-supply-chain",
    name: "AI/ML supply chain",
    capabilities: [
      { slug: "model-registry", name: "Model registry" },
      { slug: "model-scanning", name: "Model scanning" },
    ],
  },
];

/** The seeded KITs, highest priority first (GET /kits). */
export const kits: KitOut[] = [
  {
    slug: "ai-supply-chain",
    title: "AI supply chain",
    question: "AI supply chain — how are competitors securing models, agents, MCP servers?",
  },
  {
    slug: "malicious-package-defense",
    title: "Malicious-package defense",
    question: "Malicious-package defense — momentum of Socket, Sonatype Firewall, dependency firewalls (Harness, GitLab).",
  },
  { slug: "pricing-pressure", title: "Pricing & packaging pressure", question: "Pricing & packaging pressure — cloud registries, bundles, free tiers." },
];

/** One GET /search hit; `kind` is article_chunk | signal | jfrog_kb. */
export function searchHit(overrides: Partial<SearchHitOut> = {}): SearchHitOut {
  const id = overrides.ref_id ?? nextId++;
  return {
    kind: "article_chunk",
    ref_id: id,
    title: `Result ${id}`,
    url: `https://example.com/news/${id}`,
    snippet: "Sonatype folds Repository Firewall into every Nexus One subscription.",
    published_at: "2026-10-17T06:30:00Z",
    competitors: ["sonatype"],
    score: 0.031,
    ...overrides,
  };
}

// ---- competitor intelligence (GET /competitors/overview) ------------------------------------

type CompetitorSummary = Schemas["CompetitorSummary"];
type ThreatOut = Schemas["ThreatOut"];
type TimelineEventOut = Schemas["TimelineEventOut"];

const DAY_MS = 86_400_000;

/**
 * The app-zone calendar day `offset` days from `now` ("2026-10-20" is today in Israel). The
 * overview's days are local days, and "this week" is the last 7 of them, so fixtures that
 * must fall inside or outside the week are built relative to the clock, not hard-coded.
 */
export function localDay(offset = 0, now: number = Date.now()): string {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone: APP_TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit" })
      .formatToParts(new Date(now))
      .map((x) => [x.type, x.value]),
  );
  return new Date(Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day)) + offset * DAY_MS).toISOString().slice(0, 10);
}

/** Weekly momentum, oldest first: 7-day windows ending today, each keyed by its first day. */
export function momentumWeeks(values: number[], now: number = Date.now()): NonNullable<CompetitorSummary["momentum"]> {
  return values.map((value, i) => ({ week_start: localDay(-6 - 7 * (values.length - 1 - i), now), value }));
}

/** A threat as the API serves it; "insufficient" carries no score, overlap or trends. */
export function threat(level: ThreatOut["level"], overrides: Partial<ThreatOut> = {}): ThreatOut {
  if (level === "insufficient") {
    return {
      level,
      score: null,
      overlap: null,
      momentum_trend: null,
      hiring_trend: null,
      explanation: "Insufficient data: 1 visible signal in the last 180 days (3 needed) and no public job board found.",
      ...overrides,
    };
  }
  return {
    level,
    score: 6.4,
    overlap: 5,
    momentum_trend: 1.28,
    hiring_trend: null,
    explanation:
      "Medium: score 6.4 = overlap 5 × momentum 1.28 × hiring 1.00 (high from 12, medium from 4). Overlap 5 is the severity-weighted sum (high 3, medium 2, low 1) of threat impacts on JFrog products, from 4 current analyses of 9 visible signals in 180 days. Hiring is not counted: no public job board found.",
    ...overrides,
  };
}

/** One competitor's move: its newest visible signal. */
export function move(overrides: Partial<TimelineEventOut> = {}): TimelineEventOut {
  return { signal_id: nextId++, day: "2026-10-19", lane: "Launches", importance: 60, title: "A product launch", ...overrides };
}

/**
 * One overview row: a scored tier-1 competitor with a fetched job board (read for months, so its
 * 30-day change is measured) and a known total raised. A row without an open-roles count has no
 * board history (`board_since` null) unless it says otherwise.
 */
export function competitorSummary(overrides: Partial<CompetitorSummary> = {}): CompetitorSummary {
  const row: CompetitorSummary = {
    slug: "sonatype",
    name: "Sonatype",
    tier: 1,
    relationship: "competitor",
    jfrog_products: ["Artifactory", "Xray", "Curation"],
    product_categories: ["Artifact repository", "Software composition analysis", "Dependency firewall"],
    threat: threat("medium"),
    momentum: momentumWeeks([40, 52, 38, 61, 44, 70, 58, 66, 49, 80, 92, 75, 104]),
    overlap: 5,
    open_roles: 64,
    delta_30d: 4,
    has_board: true,
    board_checked_at: null,
    board_state: "measured",
    open_roles_note: null,
    open_roles_as_of: `${localDay(0)}T03:00:00Z`,
    funding_total: { amount: 154_000_000, currency: "USD", rounds: 4, undated_rounds: 0, excluded_rounds: 0, as_of: "2024-03-12" },
    latest_move: move(),
    board_since: localDay(-150),
    ...overrides,
  };
  if (row.open_roles === null && overrides.board_since === undefined) row.board_since = null;
  if (row.open_roles === null && overrides.open_roles_as_of === undefined) row.open_roles_as_of = null;
  if (overrides.board_state === undefined) row.board_state = row.open_roles !== null ? "measured" : row.has_board ? "pending" : "none";
  return row;
}

const NO_BOARD = "No public Greenhouse, Lever or Ashby board found";
const PENDING = "Job board found — first snapshot pending";

const why = (level: string, score: number, overlap: number, momentum: string, hiring: string, extra = "") =>
  `${level}: score ${score.toFixed(1)} = overlap ${overlap} × momentum ${momentum} × hiring ${hiring} (high from 12, medium from 4). ` +
  `Overlap ${overlap} is the severity-weighted sum (high 3, medium 2, low 1) of threat impacts on JFrog products in 180 days.${extra}`;

/**
 * A realistic overview, in the API's order (threat level, then score, then tier and name): two
 * high threats, three medium, three low, and three with too little evidence to score. Six moves
 * fall in the last 7 days (the strip shows the top 5 by importance), three are older. Boards:
 * measured, missing ("No public Greenhouse, Lever or Ashby board found") and found but not fetched yet (pending).
 */
export function competitorOverview(now: number = Date.now()): CompetitorSummary[] {
  const day = (offset: number) => localDay(offset, now);
  const weeks = (values: number[]) => momentumWeeks(values, now);
  const scored = (level: ThreatOut["level"], score: number, overlap: number, trend: number | null, hiring: number | null, explanation: string) =>
    threat(level, { score, overlap, momentum_trend: trend, hiring_trend: hiring, explanation });
  return withCategories([
    competitorSummary({
      slug: "sonatype",
      name: "Sonatype",
      jfrog_products: ["Artifactory", "Xray", "Curation", "Advanced Security"],
      threat: scored("high", 18.4, 11, 1.6, 0.05, why("High", 18.4, 11, "1.60", "1.05")),
      overlap: 11,
      momentum: weeks([62, 48, 71, 55, 66, 59, 74, 80, 69, 98, 112, 104, 131]),
      open_roles: 142,
      delta_30d: 12,
      funding_total: { amount: 154_000_000, currency: "USD", rounds: 4, undated_rounds: 0, excluded_rounds: 0, as_of: "2024-03-12" },
      latest_move: move({ signal_id: 9101, day: day(-1), lane: "Pricing", importance: 88, title: "Sonatype bundles Repository Firewall into every Nexus One plan" }),
    }),
    competitorSummary({
      slug: "github",
      name: "GitHub",
      relationship: "frenemy",
      jfrog_products: ["Artifactory", "Container Registry", "Advanced Security", "Platform"],
      threat: scored("high", 13.1, 11, 1.19, null, why("High", 13.1, 11, "1.19", "1.00", " Hiring is not counted: no public job board found.")),
      overlap: 11,
      momentum: weeks([90, 104, 86, 97, 110, 95, 88, 101, 93, 120, 108, 115, 126]),
      open_roles: null,
      delta_30d: null,
      has_board: false,
      open_roles_note: NO_BOARD,
      funding_total: null,
      latest_move: move({ signal_id: 9102, day: day(-3), lane: "Launches", importance: 79, title: "GitHub Packages adds self-hosted registries for Actions runners" }),
    }),
    competitorSummary({
      slug: "gitlab",
      name: "GitLab",
      jfrog_products: ["Artifactory", "Container Registry", "Advanced Security"],
      threat: scored("medium", 7.5, 6, 1.25, -0.05, why("Medium", 7.5, 6, "1.25", "0.95")),
      overlap: 6,
      momentum: weeks([30, 41, 36, 28, 44, 39, 35, 40, 33, 47, 52, 38, 49]),
      open_roles: 318,
      delta_30d: -14,
      funding_total: null,
      latest_move: move({ signal_id: 9103, day: day(-12), lane: "Launches", importance: 47, title: "GitLab tests a dependency proxy for Maven" }),
    }),
    competitorSummary({
      slug: "chainguard",
      name: "Chainguard",
      tier: 2,
      relationship: "frenemy",
      jfrog_products: ["Curation", "Container Registry"],
      threat: scored("medium", 6, 5, 1.1, 0.09, why("Medium", 6, 5, "1.10", "1.09")),
      overlap: 5,
      momentum: weeks([22, 18, 30, 26, 19, 33, 28, 24, 31, 27, 35, 29, 34]),
      open_roles: 58,
      delta_30d: 6,
      funding_total: { amount: 612_000_000, currency: "USD", rounds: 4, undated_rounds: 1, excluded_rounds: 0, as_of: "2024-03-12" },
      latest_move: move({ signal_id: 9104, day: day(-6), lane: "Money", importance: 58, title: "Chainguard raises a $356M Series D" }),
    }),
    competitorSummary({
      slug: "snyk",
      name: "Snyk",
      jfrog_products: ["Xray", "Advanced Security", "Curation"],
      threat: scored("medium", 5.2, 2, 2.4, 0.11, why("Medium", 5.2, 2, "2.00", "1.11")),
      overlap: 2,
      momentum: weeks([12, 9, 15, 11, 8, 14, 10, 13, 16, 30, 41, 38, 52]),
      open_roles: 96,
      delta_30d: 9,
      funding_total: { amount: 1_100_000_000, currency: "USD", rounds: 9, undated_rounds: 1, excluded_rounds: 1, as_of: "2024-03-12" },
      latest_move: move({ signal_id: 9105, day: day(-2), lane: "Money", importance: 71, title: "Snyk acquires an AI-native code security startup" }),
    }),
    competitorSummary({
      slug: "cloudsmith",
      name: "Cloudsmith",
      jfrog_products: ["Artifactory", "Curation"],
      threat: scored("low", 3.1, 4, 0.78, null, why("Low", 3.1, 4, "0.78", "1.00", " Hiring is not counted: the job board has no completed fetch yet.")),
      overlap: 4,
      momentum: weeks([20, 26, 18, 24, 22, 19, 25, 21, 23, 14, 17, 12, 19]),
      open_roles: null,
      delta_30d: null,
      has_board: true,
      open_roles_note: PENDING,
      funding_total: { amount: 38_100_000, currency: "USD", rounds: 3, undated_rounds: 0, excluded_rounds: 0, as_of: "2024-03-12" },
      latest_move: move({ signal_id: 9106, day: day(-5), lane: "Launches", importance: 52, title: "Cloudsmith launches an enterprise policy engine" }),
    }),
    competitorSummary({
      slug: "docker",
      name: "Docker",
      tier: 2,
      jfrog_products: ["Container Registry"],
      threat: scored("low", 2, 2, null, 0, why("Low", 2, 2, "1.00", "1.00", " Momentum has no baseline yet (no visible signals in the 9 weeks before the last 4), so its factor is 1.00.")),
      overlap: 2,
      momentum: weeks([0, 0, 0, 0, 0, 0, 0, 0, 0, 21, 34, 28, 40]),
      open_roles: 41,
      delta_30d: 0,
      funding_total: { amount: 435_000_000, currency: "USD", rounds: 5, undated_rounds: 0, excluded_rounds: 0, as_of: "2024-03-12" },
      latest_move: move({ signal_id: 9107, day: day(-4), lane: "Launches", importance: 64, title: "Docker Hardened Images reach general availability" }),
    }),
    competitorSummary({
      slug: "aws",
      name: "AWS",
      tier: 2,
      jfrog_products: ["Artifactory", "Container Registry"],
      threat: scored("low", 1.2, 1, 0.9, null, why("Low", 1.2, 1, "0.90", "1.00", " Hiring is not counted: no public job board found.")),
      overlap: 1,
      momentum: weeks([14, 18, 11, 16, 20, 13, 17, 15, 12, 16, 14, 11, 15]),
      open_roles: null,
      delta_30d: null,
      has_board: false,
      open_roles_note: NO_BOARD,
      funding_total: null,
      latest_move: move({ signal_id: 9108, day: day(-9), lane: "Launches", importance: 40, title: "AWS CodeArtifact adds Cargo support" }),
    }),
    competitorSummary({
      slug: "socket",
      name: "Socket",
      tier: 2,
      jfrog_products: ["Curation", "Xray"],
      threat: threat("insufficient"),
      overlap: null,
      momentum: null,
      open_roles: null,
      delta_30d: null,
      has_board: false,
      open_roles_note: NO_BOARD,
      funding_total: { amount: 65_000_000, currency: "USD", rounds: 2, undated_rounds: 0, excluded_rounds: 0, as_of: "2024-03-12" },
      latest_move: move({ signal_id: 9109, day: day(-20), lane: "Market", importance: 33, title: "Socket publishes its npm malware report" }),
    }),
    competitorSummary({
      slug: "anchore",
      name: "Anchore",
      tier: 3,
      jfrog_products: ["Xray"],
      threat: threat("insufficient", { explanation: "Insufficient data: 0 visible signals in the last 180 days (3 needed) and no public job board found." }),
      overlap: null,
      momentum: null,
      open_roles: null,
      delta_30d: null,
      has_board: false,
      open_roles_note: NO_BOARD,
      funding_total: null,
      latest_move: null,
    }),
    competitorSummary({
      slug: "wiz",
      name: "Wiz",
      tier: 3,
      relationship: "partner",
      jfrog_products: ["Runtime", "Advanced Security"],
      threat: threat("insufficient", {
        explanation: "Insufficient data: 2 visible signals in the last 180 days (3 needed) and the job board found has no completed fetch yet.",
      }),
      overlap: null,
      momentum: null,
      open_roles: null,
      delta_30d: null,
      has_board: true,
      open_roles_note: PENDING,
      funding_total: null,
      latest_move: null,
    }),
  ]);
}

/** Each competitor's seeded product categories, in seed order (the list's category filter). */
const CATEGORIES: Record<string, string[]> = {
  sonatype: ["Artifact repository", "Software composition analysis", "Dependency firewall", "SBOM management"],
  github: ["Artifact repository", "Container registry", "Code security"],
  gitlab: ["Artifact repository", "Container registry", "Code security"],
  chainguard: ["Hardened images", "Container registry"],
  snyk: ["Software composition analysis", "Code security", "Dependency firewall"],
  cloudsmith: ["Artifact repository", "Dependency firewall"],
  docker: ["Container registry", "Hardened images"],
  aws: ["Artifact repository", "Container registry"],
  socket: ["Dependency firewall", "Software composition analysis"],
  anchore: ["SBOM management", "Software composition analysis"],
  wiz: ["Runtime security", "Code security"],
};

const withCategories = (rows: CompetitorSummary[]) => rows.map((r) => ({ ...r, product_categories: CATEGORIES[r.slug] ?? r.product_categories }));

/**
 * Few data (a young collection): three scored competitors, one with a fetched board, one with
 * none and one with a board found but not fetched yet and no momentum baseline (Docker); five
 * with too little evidence to score, their boards missing or pending; one move this week.
 */
export function sparseOverview(now: number = Date.now()): CompetitorSummary[] {
  const insufficient = (slug: string, name: string, tier: number, relationship: string, products: string[], pending: boolean, extra: Partial<CompetitorSummary> = {}) =>
    competitorSummary({
      slug,
      name,
      tier,
      relationship,
      jfrog_products: products,
      threat: threat("insufficient"),
      overlap: null,
      momentum: null,
      open_roles: null,
      delta_30d: null,
      has_board: pending,
      open_roles_note: pending ? PENDING : NO_BOARD,
      funding_total: null,
      latest_move: null,
      ...extra,
    });
  return [
    competitorSummary({
      slug: "sonatype",
      name: "Sonatype",
      jfrog_products: ["Artifactory", "Xray", "Curation"],
      threat: threat("medium", { score: 4.4, overlap: 4, momentum_trend: 1.1, hiring_trend: null }),
      overlap: 4,
      momentum: momentumWeeks([3, 0, 4, 2, 0, 5, 3, 0, 2, 4, 3, 0, 6], now),
      open_roles: 142,
      delta_30d: 12,
      latest_move: move({ signal_id: 9101, day: localDay(-2, now), lane: "Pricing", importance: 74, title: "Sonatype bundles Repository Firewall into every Nexus One plan" }),
    }),
    competitorSummary({
      slug: "github",
      name: "GitHub",
      relationship: "frenemy",
      jfrog_products: ["Artifactory", "Container Registry"],
      threat: threat("low", { score: 2.6, overlap: 3, momentum_trend: 0.86, hiring_trend: null }),
      overlap: 3,
      momentum: momentumWeeks([5, 4, 6, 3, 5, 4, 6, 5, 3, 4, 2, 5, 3], now),
      open_roles: null,
      delta_30d: null,
      has_board: false,
      open_roles_note: NO_BOARD,
      funding_total: null,
      latest_move: move({ signal_id: 9102, day: localDay(-16, now), lane: "Launches", importance: 51, title: "GitHub Packages adds self-hosted registries for Actions runners" }),
    }),
    competitorSummary({
      slug: "docker",
      name: "Docker",
      tier: 2,
      jfrog_products: ["Container Registry"],
      threat: threat("low", { score: 1, overlap: 1, momentum_trend: null, hiring_trend: null }),
      overlap: 1,
      momentum: momentumWeeks([0, 0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 6, 3], now),
      open_roles: null,
      delta_30d: null,
      has_board: true,
      open_roles_note: PENDING,
      funding_total: null,
      latest_move: move({ signal_id: 9107, day: localDay(-9, now), lane: "Launches", importance: 44, title: "Docker Hardened Images reach general availability" }),
    }),
    insufficient("cloudsmith", "Cloudsmith", 1, "competitor", ["Artifactory", "Curation"], true),
    insufficient("chainguard", "Chainguard", 2, "frenemy", ["Curation", "Container Registry"], false, {
      funding_total: { amount: 612_000_000, currency: "USD", rounds: 4, undated_rounds: 1, excluded_rounds: 1, as_of: "2024-03-12" },
    }),
    insufficient("socket", "Socket", 2, "competitor", ["Curation", "Xray"], true, {
      latest_move: move({ signal_id: 9109, day: localDay(-40, now), lane: "Market", importance: 33, title: "Socket publishes its npm malware report" }),
    }),
    insufficient("anchore", "Anchore", 3, "competitor", ["Xray"], false),
    insufficient("wiz", "Wiz", 3, "partner", ["Runtime"], true),
  ];
}

/** The overview before anything is collected: every seeded competitor, none scored, no board, no move. */
export function emptyOverview(): CompetitorSummary[] {
  return competitors
    .filter((c) => !c.is_self)
    .map((c) =>
      competitorSummary({
        slug: c.slug,
        name: c.name,
        tier: c.tier,
        relationship: c.relationship,
        jfrog_products: [],
        threat: threat("insufficient", { explanation: "Insufficient data: 0 visible signals in the last 180 days (3 needed) and no public job board found." }),
        momentum: null,
        overlap: null,
        open_roles: null,
        delta_30d: null,
        has_board: false,
        open_roles_note: NO_BOARD,
        funding_total: null,
        latest_move: null,
      }),
    );
}

// ---- the competitor deep dive (GET /competitors/{slug}/profile) ------------------------------

type CompetitorProfile = Schemas["CompetitorProfile"];
type FactOut = Schemas["FactOut"];
type FactSourceOut = Schemas["FactSourceOut"];
type PostingOut = Schemas["PostingOut"];
type IntelSourceOut = Schemas["IntelSourceOut"];
type DayKeyCount = Schemas["DayKeyCountOut"];
type VoicePoint = Schemas["VoicePointOut"];
type OverlapLevel = Schemas["ProductOverlapOut"]["level"];

/** One source's statement of a fact: an article by default (TechCrunch, 3 Jun 2026). */
export function factSource(overrides: Partial<FactSourceOut> = {}): FactSourceOut {
  const id = overrides.fact_id ?? nextId++;
  return {
    fact_id: id,
    value: null,
    unit: null,
    currency: null,
    as_of: null,
    quote: "The company said the figure in a statement.",
    source_kind: "document",
    title: `Article ${id}`,
    url: `https://example.com/news/${id}`,
    outlet: "TechCrunch",
    published_at: "2026-06-03T09:00:00Z",
    ...overrides,
  };
}

/** A fact group as the API serves it: its value is its newest source's, every source listed (newest first). */
export function fact(kind: FactOut["kind"], overrides: Partial<FactOut> = {}): FactOut {
  const sources =
    overrides.sources ?? [factSource({ value: overrides.value ?? null, unit: overrides.unit ?? null, currency: overrides.currency ?? null, as_of: overrides.as_of ?? null })];
  return { kind, label: kind, value: null, unit: null, currency: null, as_of: null, sources_disagree: false, ...overrides, sources };
}

/** An open posting on a competitor's job board. */
export function posting(overrides: Partial<PostingOut> = {}): PostingOut {
  const id = overrides.id ?? nextId++;
  return {
    id,
    title: `Senior Software Engineer ${id}`,
    department: "Engineering",
    seniority: "Senior",
    location: "Fulton, MD",
    country: "United States",
    remote: false,
    url: `https://boards.greenhouse.io/sonatype/jobs/${id}`,
    first_seen_at: "2026-10-12T06:00:00Z",
    closed_at: null,
    tags: [],
    // a posting with tags was tagged; one without was tagged with none, unless told otherwise
    tag_state: (overrides.tags ?? []).length > 0 ? "tagged" : "untagged",
    ...overrides,
  };
}

/** Every local day from `from` days ago to today, oldest first. */
function daysBack(from: number, now: number): string[] {
  return Array.from({ length: from + 1 }, (_, i) => localDay(i - from, now));
}

/**
 * Open roles per key per day over 90 days, as the board's daily snapshots record them: each key
 * moves linearly from `count - delta` 30 days ago to `count` today and was steady before that.
 * Every key has a point on every day (the API's density).
 */
function keySeries(rows: [key: string, count: number, delta: number][], now: number): DayKeyCount[] {
  const days = daysBack(89, now);
  return rows.flatMap(([key, count, delta]) =>
    days.map((day, i) => {
      const back = days.length - 1 - i;
      return { day, key, count: back >= 30 ? count - delta : Math.round(count - (delta * back) / 30) };
    }),
  );
}

const DEPARTMENTS: [string, number, number][] = [
  ["Engineering", 48, 5],
  ["Sales", 22, 3],
  ["Security", 14, 2],
  ["Product", 11, 0],
  ["Solutions & SE", 10, 1],
  ["Customer success", 9, -1],
  ["Marketing", 8, 1],
  ["Data & ML", 7, 2],
  ["DevRel & community", 5, 0],
  ["Design", 3, -1],
  ["G&A", 3, 0],
  ["Other", 2, 0],
];
const SENIORITIES: [string, number, number][] = [
  ["Senior", 46, 5],
  ["Mid", 38, 3],
  ["Staff & Principal", 18, 3],
  ["Manager", 14, 1],
  ["Junior", 10, 0],
  ["Director", 9, 1],
  ["VP & C-level", 4, 0],
  ["Intern", 3, -1],
];
const LOCATIONS: [string, number, number][] = [
  ["United States", 41, 4],
  ["Remote", 30, 5],
  ["United Kingdom", 16, 1],
  ["Germany", 12, 0],
  ["Canada", 10, 1],
  ["Israel", 9, 2],
  ["India", 8, 0],
  ["Australia", 6, -1],
  ["Other", 10, 0],
];

/** The brief's numbered sources: signals, a fact, a posting, a page change, a signal hidden from readers, another signal and a fact. */
function profileSources(now: number): IntelSourceOut[] {
  return [
    { n: 1, kind: "signal", ref: "signal:9101", title: "Sonatype bundles Repository Firewall into every Nexus One plan", url: "/signals/9101", outlet: null, day: localDay(-1, now), excerpt: null },
    { n: 2, kind: "signal", ref: "signal:9110", title: "Nexus Repository adds an AI model registry in beta", url: "/signals/9110", outlet: null, day: localDay(-9, now), excerpt: null },
    {
      n: 3,
      kind: "fact",
      ref: "fact:501",
      title: "Sonatype raises $45M Series C to expand its supply chain platform",
      url: "https://techcrunch.com/2024/03/12/sonatype-series-c",
      outlet: "TechCrunch",
      day: "2024-03-12",
      excerpt: "Sonatype has raised a $45 million Series C round to expand its software supply chain platform.",
    },
    {
      n: 4,
      kind: "posting",
      ref: "posting:7001",
      title: "Staff Engineer, AI Model Security",
      url: "https://boards.greenhouse.io/sonatype/jobs/7001",
      outlet: "Greenhouse",
      day: localDay(-12, now),
      excerpt: null,
    },
    {
      n: 5,
      kind: "page",
      ref: "page:801",
      title: "Sonatype pricing page",
      url: "https://www.sonatype.com/pricing",
      outlet: null,
      day: localDay(-1, now),
      excerpt: "Repository Firewall moved into every Nexus One plan; the separate Firewall add-on is gone.",
    },
    { n: 6, kind: "signal", ref: "signal:88", title: "Signal not available", url: null, outlet: null, day: null, excerpt: null },
    { n: 7, kind: "signal", ref: "signal:9112", title: "Sonatype lists Nexus One on AWS Marketplace", url: "/signals/9112", outlet: null, day: localDay(-28, now), excerpt: null },
    {
      n: 8,
      kind: "fact",
      ref: "fact:502",
      title: "Sonatype about page",
      url: "https://www.sonatype.com/about",
      outlet: null,
      day: "2026-06-10",
      excerpt: "More than 1,300 people work at Sonatype across 12 countries.",
    },
  ];
}

type Lane = CompetitorProfile["timeline"][number]["lane"];

/** The swimlane: 21 public signals over 180 days, the newest a pricing move yesterday. */
function profileTimeline(now: number): CompetitorProfile["timeline"] {
  const events: [number, number, Lane, number, string][] = [
    [9130, -171, "Launches", 41, "Sonatype ships Nexus Repository 3.70 with Cargo support"],
    [9129, -158, "Market", 33, "Sonatype publishes its State of the Software Supply Chain report"],
    [9128, -149, "People", 46, "Sonatype names a new Chief Product Officer"],
    [9127, -137, "Launches", 52, "Lifecycle adds reachability analysis for Java"],
    [9126, -121, "Partners", 38, "Sonatype and Google Cloud expand their Assured OSS work"],
    [9125, -110, "Market", 29, "Sonatype at Black Hat: malicious packages up 150%"],
    [9124, -98, "Security", 57, "A Nexus Repository path traversal flaw is patched"],
    [9123, -90, "Launches", 61, "Repository Firewall blocks AI model downloads with known malware"],
    [9122, -77, "Money", 35, "Sonatype reports 30% ARR growth"],
    [9121, -66, "Launches", 48, "Nexus Repository adds Hugging Face proxy support"],
    [9120, -58, "Market", 36, "Analysts rank Sonatype a leader in SCA"],
    [9119, -51, "People", 40, "Sonatype opens an engineering hub in Bengaluru"],
    [9118, -44, "Pricing", 58, "Nexus Repository Pro list price rises 12%"],
    [9117, -37, "Partners", 44, "Sonatype joins the OpenSSF model signing working group"],
    [9116, -30, "Launches", 55, "SBOM Manager adds VEX export"],
    [9112, -28, "Partners", 62, "Sonatype lists Nexus One on AWS Marketplace"],
    [9115, -19, "Market", 47, "Sonatype's npm malware tally passes 800,000 packages"],
    [9114, -14, "Launches", 66, "Lifecycle adds AI-generated fix pull requests"],
    [9110, -9, "Launches", 79, "Nexus Repository adds an AI model registry in beta"],
    [9113, -5, "Money", 51, "Sonatype extends its credit facility"],
    [9101, -1, "Pricing", 88, "Sonatype bundles Repository Firewall into every Nexus One plan"],
  ];
  return events.map(([signal_id, offset, lane, importance, title]) => ({ signal_id, day: localDay(offset, now), lane, importance, title }));
}

/**
 * Share of voice: 13 weeks of articles and signals for the competitor and its 4 peers. The third
 * peer appears from week 4 and has no row for week 8 (a sparse series, densified on screen).
 */
function shareOfVoice(now: number, own: string, peers: string[]): VoicePoint[] {
  const weeks = momentumWeeks(Array.from({ length: 13 }, () => 0), now).map((w) => w.week_start);
  const docs: [string, number[]][] = [
    [own, [28, 31, 25, 34, 29, 36, 33, 38, 30, 41, 45, 39, 52]],
    [peers[0]!, [44, 47, 40, 52, 49, 45, 51, 48, 46, 55, 50, 53, 58]],
    [peers[1]!, [22, 25, 19, 27, 24, 21, 26, 23, 20, 28, 25, 22, 27]],
    [peers[2]!, [0, 0, 0, 9, 12, 8, 14, 0, 11, 16, 13, 18, 15]],
    [peers[3]!, [35, 38, 33, 40, 36, 39, 42, 37, 41, 44, 40, 46, 43]],
  ];
  return docs.flatMap(([slug, values]) =>
    values.flatMap((documents, i) =>
      slug === peers[2] && (i < 3 || i === 7) ? [] : [{ week_start: weeks[i]!, competitor_slug: slug, documents, signals: Math.round(documents / 6) }],
    ),
  );
}

/** Weekly stars (or forks) of a GitHub repository over 180 days, rising from `from` to `to`. */
function weekly(from: number, to: number, now: number): Schemas["DayValueOut"][] {
  return Array.from({ length: 26 }, (_, i) => ({ day: localDay(-175 + i * 7, now), value: Math.round(from + ((to - from) * i) / 25) }));
}

const hoursAgo = (now: number, hours: number) => new Date(now - hours * 3_600_000).toISOString();

type Round = Schemas["FundingRoundOut"];

function round(label: string, as_of: string, amount: number | null, source: Partial<FactSourceOut>, extra: Partial<Round> = {}): Round {
  return {
    as_of,
    amount,
    currency: amount === null ? null : "USD",
    label,
    sources_disagree: false,
    sources: [factSource({ value: amount, currency: amount === null ? null : "USD", as_of, published_at: `${as_of}T12:00:00Z`, ...source })],
    ...extra,
  };
}

/**
 * A rich deep dive (Sonatype): a scored high threat, a fetched job board, a current brief citing
 * every ref kind (one cited signal hidden from readers, one cite with no source), dated and
 * undated facts, two headcount figures from the same month that disagree, a disputed and an
 * undisclosed round, share of voice with a sparse peer, two pricing changes with their diffs,
 * GitHub and Hacker News, and six signals.
 */
export function competitorProfile(now: number = Date.now()): CompetitorProfile {
  const day = (offset: number) => localDay(offset, now);
  const aboutPage = factSource({
    value: 1300,
    unit: "employees",
    as_of: "2026-06-10",
    source_kind: "page_snapshot",
    title: "Sonatype about page",
    url: "https://www.sonatype.com/about",
    outlet: null,
    published_at: "2026-06-10T04:00:00Z",
    quote: "More than 1,300 people work at Sonatype across 12 countries.",
  });
  const cpoStory = factSource({
    value: 1250,
    unit: "employees",
    as_of: "2026-06-03",
    title: "Sonatype hires a CPO as it nears 1,250 staff",
    url: "https://techcrunch.com/2026/06/03/sonatype-cpo",
    outlet: "TechCrunch",
    quote: "The company now employs about 1,250 people.",
  });
  const seriesC = round("Series C", "2024-03-12", 45_000_000, {
    title: "Sonatype raises $45M Series C to expand its supply chain platform",
    url: "https://techcrunch.com/2024/03/12/sonatype-series-c",
    outlet: "TechCrunch",
    quote: "Sonatype has raised a $45 million Series C round to expand its software supply chain platform.",
  });
  const seriesB: Round = {
    ...round("Series B", "2021-09-08", 35_000_000, { title: "Sonatype closes a $35M Series B", url: "https://techcrunch.com/2021/09/08/sonatype-b", outlet: "TechCrunch", quote: "Sonatype has closed a $35 million Series B." }),
    sources_disagree: true,
  };
  seriesB.sources.push(
    factSource({
      value: 33_000_000,
      currency: "USD",
      as_of: "2021-09-07",
      title: "Sonatype lands $33M",
      url: "https://siliconangle.com/2021/09/07/sonatype-33m",
      outlet: "SiliconANGLE",
      published_at: "2021-09-07T12:00:00Z",
      quote: "The $33 million round was led by existing investors.",
    }),
  );
  const products: { product: string; category: string; url: string | null; jfrog_products: string[]; level: OverlapLevel; evidence: number; signal_ids: number[] }[] = [
    {
      product: "Nexus Repository",
      category: "Artifact repository",
      url: "https://www.sonatype.com/products/sonatype-nexus-repository",
      jfrog_products: ["Artifactory", "Container Registry"],
      level: "high",
      evidence: 9,
      signal_ids: [9101, 9110, 9121, 9130],
    },
    {
      product: "Repository Firewall",
      category: "Malicious-package defense",
      url: "https://www.sonatype.com/products/sonatype-repository-firewall",
      jfrog_products: ["Curation"],
      level: "high",
      evidence: 7,
      signal_ids: [9101, 9123, 9115],
    },
    {
      product: "Lifecycle",
      category: "Software composition analysis",
      url: "https://www.sonatype.com/products/open-source-security",
      jfrog_products: ["Xray", "Advanced Security"],
      level: "medium",
      evidence: 4,
      signal_ids: [9114, 9127],
    },
    { product: "SBOM Manager", category: "SBOM management", url: null, jfrog_products: ["Xray"], level: "none", evidence: 0, signal_ids: [] },
  ];
  const edges = [
    {
      product: "Nexus Repository",
      jfrog_product: "Artifactory",
      edge: { text: "Nexus One bundles Firewall, so a price comparison with Artifactory Pro will leave out what Curation costs.", cites: [1, 5] },
    },
    {
      product: "Repository Firewall",
      jfrog_product: "Curation",
      edge: { text: "Firewall now blocks AI model downloads with known malware, which Curation covers only for packages today.", cites: [1] },
    },
  ];
  return {
    competitor: {
      slug: "sonatype",
      name: "Sonatype",
      tier: 1,
      relationship: "competitor",
      description:
        "Sonatype makes Nexus Repository and the Nexus One platform for software supply chain management: an artifact repository, open-source risk analysis, malicious-package defense and SBOMs.",
      website: "https://www.sonatype.com",
      ticker: null,
    },
    kpis: {
      threat: threat("high", {
        score: 18.4,
        overlap: 11,
        momentum_trend: 1.6,
        hiring_trend: 0.09,
        explanation:
          "High: score 18.4 = overlap 11 × momentum 1.60 × hiring 1.05 (high from 12, medium from 4). Overlap 11 is the severity-weighted sum (high 3, medium 2, low 1) of threat impacts on JFrog products, from 9 current analyses of 21 public signals in 180 days. Momentum: the last 4 weeks average 1.60× the 9 weeks before. Hiring: open roles up 9% in 30 days.",
      }),
      has_board: true,
      open_roles: 142,
      delta_30d: 12,
      latest_headcount: fact("headcount", { label: "employees", value: 1300, unit: "employees", as_of: "2026-06-10", sources_disagree: true, sources: [aboutPage, cpoStory] }),
      funding_total: { amount: 154_000_000, currency: "USD", rounds: 4, undated_rounds: 1, excluded_rounds: 1, as_of: "2024-03-12" },
      last_round: seriesC,
    },
    brief: {
      version: 3,
      created_at: hoursAgo(now, 28),
      bottom_line: [
        {
          text: "Sonatype is turning Nexus One into a bundle aimed at Artifactory renewals: Repository Firewall now ships in every plan, so the malicious-package defense JFrog sells as Curation comes free in Sonatype's core tier.",
          cites: [1, 5],
        },
        {
          text: "It is also moving into AI artifacts: a model registry in Nexus Repository and a staff opening for model security point at a JFrog ML competitor within two quarters.",
          cites: [2, 4, 6],
        },
        { text: "A $45M Series C and steady hiring fund the push; expect the bundle in competitive deals before the end of the year.", cites: [3, 8, 99] },
      ],
      product_edges: edges,
      watch: [
        { text: "Whether the bundled Firewall reaches Nexus Repository Pro customers on premises at renewal.", cites: [1] },
        { text: "A general-availability date for the model registry.", cites: [2, 7] },
      ],
      evidence: { signals: 9, facts: 3, postings: 2, pages: 1 },
      sources: profileSources(now),
    },
    products: products.map((p) => ({ ...p, edges: edges.filter((e) => e.product === p.product) })),
    momentum: momentumWeeks([62, 48, 71, 55, 66, 59, 74, 80, 69, 98, 112, 104, 131], now),
    timeline: profileTimeline(now),
    hiring: {
      has_board: true,
      open_now: 142,
      delta_30d: 12,
      board_since: localDay(-89, now),
      as_of: hoursAgo(now, 6),
      not_readable_since: null,
      board_checked_at: null,
      tag_coverage: { tagged: 120, pending: 14, untagged: 8, gave_up: 0 },
      by_department: DEPARTMENTS.map(([key, count]) => ({ key, count })),
      by_seniority: SENIORITIES.map(([key, count]) => ({ key, count })),
      by_location: LOCATIONS.map(([key, count]) => ({ key, count })),
      tags: [
        { slug: "container-registry", open: 21, delta_30d: 4 },
        { slug: "remote-proxy-caching", open: 17, delta_30d: 2 },
        { slug: "software-composition-analysis", open: 15, delta_30d: -2 },
        { slug: "model-scanning", open: 12, delta_30d: 6 },
        { slug: "model-registry", open: 6, delta_30d: 3 },
        { slug: "sbom-management", open: 5, delta_30d: 0 },
      ],
      recent: [
        posting({
          id: 7001,
          title: "Staff Engineer, AI Model Security",
          department: "Security",
          seniority: "Staff & Principal",
          location: null,
          country: "United States",
          remote: true,
          first_seen_at: hoursAgo(now, 12 * 24),
          tags: ["model-scanning", "model-registry"],
        }),
        posting({ id: 7002, title: "Senior Product Manager, Nexus Repository", department: "Product", seniority: "Senior", first_seen_at: hoursAgo(now, 3 * 24), tags: ["container-registry"] }),
        posting({ id: 7003, title: "Solutions Engineer, EMEA", department: "Solutions & SE", seniority: "Mid", location: "London", country: "United Kingdom", first_seen_at: hoursAgo(now, 4 * 24) }),
        posting({ id: 7004, title: "Account Executive, Strategic Accounts", department: "Sales", seniority: "Senior", location: "New York, NY", first_seen_at: hoursAgo(now, 5 * 24) }),
        posting({
          id: 7005,
          title: "Software Engineer II, Repository Firewall",
          department: "Engineering",
          seniority: "Mid",
          location: "Bengaluru",
          country: "India",
          first_seen_at: hoursAgo(now, 6 * 24),
          tags: ["remote-proxy-caching", "software-composition-analysis"],
        }),
        posting({ id: 7006, title: "Developer Advocate", department: "DevRel & community", seniority: "Senior", location: null, country: "Germany", remote: true, first_seen_at: hoursAgo(now, 8 * 24) }),
      ],
      series: {
        total: daysBack(89, now).map((d, i) => ({ day: d, count: i < 60 ? 130 : Math.round(130 + ((i - 59) * 12) / 30) })),
        department: keySeries(DEPARTMENTS, now),
        seniority: keySeries(SENIORITIES, now),
        location: keySeries(LOCATIONS, now),
        tag: [],
      },
    },
    headcount: {
      points: [
        {
          kind: "layoff",
          label: "layoff",
          as_of: "2025-01-20",
          value: 10,
          unit: "%",
          currency: null,
          sources_disagree: false,
          layoff: true,
          sources: [
            factSource({
              value: 10,
              unit: "%",
              as_of: "2025-01-20",
              outlet: "The Information",
              title: "Sonatype cuts 10% of staff",
              url: "https://example.com/news/sonatype-layoff",
              published_at: "2025-01-20T12:00:00Z",
              quote: "Sonatype cut about 10% of its staff this week.",
            }),
          ],
        },
        {
          kind: "headcount",
          label: "employees",
          as_of: "2025-02-10",
          value: 1050,
          unit: "employees",
          currency: null,
          sources_disagree: false,
          layoff: false,
          sources: [
            factSource({
              value: 1050,
              unit: "employees",
              as_of: "2025-02-10",
              outlet: "SiliconANGLE",
              title: "Sonatype trims staff after a slow quarter",
              url: "https://siliconangle.com/2025/02/10/sonatype",
              published_at: "2025-02-10T12:00:00Z",
              quote: "Sonatype, which now has about 1,050 employees, expects to hire again this year.",
            }),
          ],
        },
        {
          kind: "headcount",
          label: "employees",
          as_of: "2025-09-15",
          value: 1180,
          unit: "employees",
          currency: null,
          sources_disagree: false,
          layoff: false,
          sources: [
            factSource({
              value: 1180,
              unit: "employees",
              as_of: "2025-09-15",
              outlet: "VentureBeat",
              title: "Sonatype is hiring again",
              url: "https://venturebeat.com/2025/09/15/sonatype",
              published_at: "2025-09-15T12:00:00Z",
              quote: "The company's headcount is back to about 1,180.",
            }),
          ],
        },
        { kind: "headcount", label: "employees", as_of: "2026-06-03", value: 1250, unit: "employees", currency: null, sources_disagree: true, layoff: false, sources: [cpoStory] },
        { kind: "headcount", label: "employees", as_of: "2026-06-10", value: 1300, unit: "employees", currency: null, sources_disagree: true, layoff: false, sources: [aboutPage] },
      ],
      undated: [
        fact("headcount", {
          label: "employees",
          value: 1000,
          unit: "employees",
          sources: [
            factSource({
              value: 1000,
              unit: "employees",
              outlet: "DevOps.com",
              title: "Inside Sonatype's Fulton campus",
              url: "https://devops.com/sonatype-campus",
              published_at: null,
              quote: "Sonatype's 1,000 or so employees split their time between Fulton and remote work.",
            }),
          ],
        }),
      ],
    },
    money: {
      funding_rounds: [
        round("Seed", "2017-05-02", null, { title: "Sonatype's early backers", url: "https://example.com/news/sonatype-seed", outlet: "The Information", quote: "Sonatype raised an undisclosed seed round in May 2017." }),
        round("Series A", "2019-04-16", 20_000_000, { title: "Sonatype raises $20M", url: "https://venturebeat.com/2019/04/16/sonatype-20m", outlet: "VentureBeat", quote: "Sonatype raised $20 million in a Series A round." }),
        seriesB,
        seriesC,
      ],
      valuations: [
        {
          kind: "valuation",
          label: "valuation",
          as_of: "2024-03-12",
          value: 1_200_000_000,
          unit: null,
          currency: "USD",
          sources_disagree: false,
          sources: [
            factSource({
              value: 1_200_000_000,
              currency: "USD",
              as_of: "2024-03-12",
              outlet: "TechCrunch",
              title: "Sonatype raises $45M Series C to expand its supply chain platform",
              url: "https://techcrunch.com/2024/03/12/sonatype-series-c",
              published_at: "2024-03-12T12:00:00Z",
              quote: "The round values Sonatype at $1.2 billion.",
            }),
          ],
        },
      ],
      revenue: [
        {
          kind: "arr",
          label: "ARR",
          as_of: "2025-01-30",
          value: 150_000_000,
          unit: null,
          currency: "USD",
          sources_disagree: false,
          sources: [
            factSource({
              value: 150_000_000,
              currency: "USD",
              as_of: "2025-01-30",
              outlet: "Sonatype newsroom",
              title: "Sonatype closes FY2024",
              url: "https://www.sonatype.com/press/fy2024",
              published_at: "2025-01-30T12:00:00Z",
              quote: "Annual recurring revenue passed $150 million.",
            }),
          ],
        },
        {
          kind: "arr",
          label: "ARR",
          as_of: "2026-02-02",
          value: 185_000_000,
          unit: null,
          currency: "USD",
          sources_disagree: false,
          sources: [
            factSource({
              value: 185_000_000,
              currency: "USD",
              as_of: "2026-02-02",
              outlet: "Sonatype newsroom",
              title: "Sonatype closes FY2025",
              url: "https://www.sonatype.com/press/fy2025",
              published_at: "2026-02-02T12:00:00Z",
              quote: "ARR grew to $185 million.",
            }),
          ],
        },
      ],
      undated: [
        fact("funding_round", {
          label: "Debt financing",
          value: 54_000_000,
          currency: "USD",
          sources: [
            factSource({
              value: 54_000_000,
              currency: "USD",
              outlet: "Forbes",
              title: "How Sonatype funds its growth",
              url: "https://example.com/news/sonatype-debt",
              published_at: null,
              quote: "Sonatype has also drawn $54 million in debt financing.",
            }),
          ],
        }),
      ],
    },
    market: {
      market_share: [
        fact("market_share", {
          label: "software composition analysis, IDC",
          value: 18,
          unit: "%",
          as_of: "2026-04-01",
          sources: [
            factSource({
              value: 18,
              unit: "%",
              as_of: "2026-04-01",
              outlet: "SiliconANGLE",
              title: "IDC: the SCA market grows 21%",
              url: "https://siliconangle.com/2026/04/01/idc-sca",
              published_at: "2026-04-01T12:00:00Z",
              quote: "IDC puts Sonatype's share of the software composition analysis market at 18%.",
            }),
          ],
        }),
      ],
      undated: [],
      peers: ["github", "gitlab", "chainguard", "snyk"],
      share_of_voice: shareOfVoice(now, "sonatype", ["github", "gitlab", "chainguard", "snyk"]),
    },
    customers: {
      facts: [
        fact("customer", {
          label: "Acme Bank",
          as_of: "2026-08-14",
          sources: [
            factSource({
              as_of: "2026-08-14",
              source_kind: "page_snapshot",
              outlet: null,
              title: "Sonatype customers page",
              url: "https://www.sonatype.com/customers",
              published_at: "2026-08-14T04:00:00Z",
              quote: "Acme Bank secures 4,000 developers with Nexus One.",
            }),
          ],
        }),
        fact("customer_count", {
          label: "customers",
          value: 2000,
          unit: "customers",
          as_of: "2026-02-02",
          sources: [
            factSource({
              value: 2000,
              unit: "customers",
              as_of: "2026-02-02",
              outlet: "Sonatype newsroom",
              title: "Sonatype closes FY2025",
              url: "https://www.sonatype.com/press/fy2025",
              published_at: "2026-02-02T12:00:00Z",
              quote: "More than 2,000 customers now run Nexus One.",
            }),
          ],
        }),
      ],
      undated: [
        fact("customer", {
          label: "Globex Insurance",
          sources: [
            factSource({
              outlet: "DevOps.com",
              title: "Globex standardises on Nexus",
              url: "https://devops.com/globex-nexus",
              published_at: null,
              quote: "Globex Insurance runs Nexus Repository for its 900 engineers.",
            }),
          ],
        }),
      ],
      signals: [
        signalCard({
          id: 9140,
          title: "Globex moves its artifact management to Nexus One",
          signal_type: "customer_win_loss",
          stance: "threat",
          importance: 72,
          summary: "A JFrog customer of six years moved 900 engineers to Nexus One, citing the bundled Firewall.",
          first_seen_at: hoursAgo(now, 6 * 24),
          primary_source: "DevOps.com",
        }),
      ],
    },
    pricing: [
      {
        snapshot_id: 801,
        day: day(-1),
        page_kind: "pricing",
        change_kind: "packaging",
        summary: "Repository Firewall moved into every Nexus One plan; the separate Firewall add-on is gone.",
        diff: [
          "@@ -12,6 +12,6 @@",
          " Nexus One Starter",
          " Unlimited repositories and formats",
          "-Repository Firewall: add-on, from $12 per developer per month",
          "+Repository Firewall: included",
          " Lifecycle: add-on",
          "@@ -40,4 +40,5 @@",
          " Nexus One Enterprise",
          "-Contact sales for Firewall pricing",
          "+Firewall, Lifecycle and SBOM Manager included",
          "+AI model registry (beta)",
        ].join("\n"),
        truncated: false,
        url: "https://www.sonatype.com/pricing",
      },
      {
        snapshot_id: 790,
        day: day(-44),
        page_kind: "pricing",
        change_kind: "pricing",
        summary: "Nexus Repository Pro rose from $1,200 to $1,350 a year for 25 users.",
        diff: ["@@ -8,3 +8,3 @@", " Nexus Repository Pro", "-$1,200 per year, 25 users", "+$1,350 per year, 25 users", " Annual billing only"].join("\n"),
        truncated: false,
        url: "https://www.sonatype.com/pricing",
      },
    ],
    community: {
      repos: [
        { repo: "sonatype/nexus-public", stars: weekly(3410, 3620, now), forks: weekly(1040, 1081, now) },
        { repo: "sonatype-nexus-community/nancy", stars: weekly(2010, 2052, now), forks: weekly(91, 94, now) },
      ],
      mentions: momentumWeeks([4, 2, 5, 3, 1, 6, 4, 3, 5, 7, 4, 9, 6], now).map((w) => ({ week_start: w.week_start, count: w.value })),
    },
    people_partners: {
      facts: [
        fact("partnership", {
          label: "AWS",
          as_of: day(-28),
          sources: [
            factSource({
              as_of: day(-28),
              outlet: "AWS News Blog",
              title: "Nexus One arrives on AWS Marketplace",
              url: "https://aws.amazon.com/blogs/sonatype",
              published_at: hoursAgo(now, 28 * 24),
              quote: "Sonatype and AWS today made Nexus One available on AWS Marketplace.",
            }),
          ],
        }),
        fact("office", {
          label: "Bengaluru, India",
          as_of: day(-51),
          sources: [
            factSource({
              as_of: day(-51),
              outlet: "Economic Times",
              title: "Sonatype opens a Bengaluru hub",
              url: "https://example.com/news/sonatype-bengaluru",
              published_at: hoursAgo(now, 51 * 24),
              quote: "Sonatype opened an engineering hub in Bengaluru, India.",
            }),
          ],
        }),
        fact("leadership_change", {
          label: "Chief Product Officer appointed",
          as_of: day(-149),
          sources: [
            factSource({
              as_of: day(-149),
              outlet: "TechCrunch",
              title: "Sonatype hires a CPO as it nears 1,250 staff",
              url: "https://techcrunch.com/2026/06/03/sonatype-cpo",
              published_at: hoursAgo(now, 149 * 24),
              quote: "Sonatype has appointed a new Chief Product Officer.",
            }),
          ],
        }),
      ],
      undated: [],
      signals: [
        signalCard({
          id: 9112,
          title: "Sonatype lists Nexus One on AWS Marketplace",
          signal_type: "partnership",
          stance: "threat",
          importance: 62,
          summary: "AWS customers can now buy Nexus One against their committed spend, a channel JFrog already uses.",
          first_seen_at: hoursAgo(now, 28 * 24),
          primary_source: "AWS News Blog",
        }),
      ],
    },
    latest_signals: [
      signalCard({
        id: 9101,
        title: "Sonatype bundles Repository Firewall into every Nexus One plan",
        signal_type: "pricing_change",
        stance: "threat",
        importance: 88,
        summary: "Firewall is now included in every Nexus One plan, removing a separate line item that JFrog's Curation competes with.",
        first_seen_at: hoursAgo(now, 26),
        primary_source: "Sonatype blog",
        grade: "A2",
        reliability: "A",
      }),
      signalCard({
        id: 9113,
        title: "Sonatype extends its credit facility",
        signal_type: "funding",
        stance: "neutral",
        importance: 51,
        summary: "A larger credit line gives Sonatype room to keep hiring without a new round.",
        first_seen_at: hoursAgo(now, 5 * 24),
        primary_source: "Reuters",
      }),
      signalCard({
        id: 9110,
        title: "Nexus Repository adds an AI model registry in beta",
        signal_type: "product_launch",
        stance: "threat",
        importance: 79,
        summary: "A model registry inside Nexus Repository puts Sonatype against JFrog ML for teams that already run Nexus.",
        first_seen_at: hoursAgo(now, 9 * 24),
        primary_source: "Sonatype blog",
        competitors: ["sonatype", "github"],
      }),
      signalCard({ id: 9114, title: "Lifecycle adds AI-generated fix pull requests", signal_type: "feature_update", importance: 66, first_seen_at: hoursAgo(now, 14 * 24), primary_source: "DevOps.com" }),
      signalCard({
        id: 9115,
        title: "Sonatype's npm malware tally passes 800,000 packages",
        signal_type: "industry_trend",
        stance: "opportunity",
        importance: 47,
        summary: "The rising count keeps malicious-package defense a board-level topic, which helps Curation conversations.",
        first_seen_at: hoursAgo(now, 19 * 24),
        primary_source: "The Register",
      }),
      signalCard({
        id: 9112,
        title: "Sonatype lists Nexus One on AWS Marketplace",
        signal_type: "partnership",
        stance: "threat",
        importance: 62,
        summary: "AWS customers can now buy Nexus One against their committed spend.",
        first_seen_at: hoursAgo(now, 28 * 24),
        primary_source: "AWS News Blog",
      }),
    ],
    collected_since: null,
    sources_freshness: {
      sources: [
        { kind: "rss", name: "Sonatype blog", health: "ok", last_fetched_at: hoursAgo(now, 2) },
        { kind: "google_news", name: "Google News: Sonatype", health: "ok", last_fetched_at: hoursAgo(now, 3) },
        { kind: "job_board", name: "Sonatype on Greenhouse", health: "ok", last_fetched_at: hoursAgo(now, 6) },
        { kind: "page_watch", name: "Sonatype pricing page", health: "degraded", last_fetched_at: hoursAgo(now, 30) },
        { kind: "github_repo_stats", name: "GitHub: sonatype", health: "ok", last_fetched_at: hoursAgo(now, 5) },
        { kind: "hn", name: "Hacker News: Sonatype", health: "ok", last_fetched_at: hoursAgo(now, 4) },
      ],
      notes: [],
    },
  };
}

/** The coverage notes the API writes for a competitor it knows nothing about yet, in section order. */
export const SPARSE_NOTES: CompetitorProfile["sources_freshness"]["notes"] = [
  { section: "overview", text: "Not enough evidence for a brief yet: 0 public signals in the last 180 days (3 needed)" },
  { section: "products", text: "No products of this competitor are mapped to JFrog products" },
  { section: "momentum", text: "No public signals about it in the last 180 days" },
  { section: "hiring", text: "No public Greenhouse, Lever or Ashby board found" },
  { section: "headcount", text: "No headcount or layoff figures have been reported in its sources yet" },
  { section: "money", text: "No funding, valuation or revenue figures have been reported in its sources yet" },
  { section: "market", text: "No market share figure has been reported in its sources yet" },
  { section: "customers", text: "No customers or customer wins and losses have been reported in its sources yet" },
  { section: "pricing", text: "No pricing page is watched for this competitor" },
  { section: "community", text: "No GitHub repositories are tracked for this competitor" },
  { section: "community", text: "No Hacker News search runs for this competitor" },
  { section: "people_partners", text: "No leadership changes, acquisitions, partnerships or offices have been reported in its sources yet" },
  { section: "latest_signals", text: "No public signals about it yet" },
  { section: "sources", text: "No news sources of its own: it is covered through industry feeds only" },
];

/**
 * A competitor FrogEye knows almost nothing about (Anchore): no job board, no facts, no signals,
 * no brief, no mapped products, no watched page and no repositories. Its threat is insufficient
 * and every section has a coverage note. Share of voice is measured: its peers have articles,
 * it has none.
 */
export function sparseProfile(now: number = Date.now()): CompetitorProfile {
  const peers = ["sonatype", "chainguard", "snyk", "docker"];
  return {
    competitor: { slug: "anchore", name: "Anchore", tier: 3, relationship: "competitor", description: null, website: "https://anchore.com", ticker: null },
    kpis: {
      threat: threat("insufficient", { explanation: "Insufficient data: 0 public signals in the last 180 days (3 needed) and no public job board found." }),
      has_board: false,
      open_roles: null,
      delta_30d: null,
      latest_headcount: null,
      funding_total: null,
      last_round: null,
    },
    brief: null,
    products: [],
    momentum: null,
    timeline: [],
    hiring: {
      has_board: false,
      open_now: null,
      delta_30d: null,
      board_since: null,
      as_of: null,
      not_readable_since: null,
      board_checked_at: null,
      tag_coverage: null,
      by_department: [],
      by_seniority: [],
      by_location: [],
      tags: [],
      recent: [],
      series: { total: [], department: [], seniority: [], location: [], tag: [] },
    },
    headcount: { points: [], undated: [] },
    money: { funding_rounds: [], valuations: [], revenue: [], undated: [] },
    market: {
      market_share: [],
      undated: [],
      peers,
      share_of_voice: shareOfVoice(now, "anchore", peers).map((p) => (p.competitor_slug === "anchore" ? { ...p, documents: 0, signals: 0 } : p)),
    },
    customers: { facts: [], undated: [], signals: [] },
    pricing: [],
    community: { repos: [], mentions: [] },
    people_partners: { facts: [], undated: [], signals: [] },
    latest_signals: [],
    collected_since: null,
    sources_freshness: { sources: [{ kind: "google_news", name: "Google News: software supply chain", health: "ok", last_fetched_at: hoursAgo(now, 3) }], notes: SPARSE_NOTES },
  };
}

/**
 * The rich profile on a board first read 10 days ago: the same open roles, but no 30-day
 * change anywhere (total, departments, tags, the threat's hiring trend), and every series starting
 * at the board's first snapshot, as the API serves it.
 */
export function youngProfile(now: number = Date.now()): CompetitorProfile {
  const p = competitorProfile(now);
  const since = localDay(-10, now);
  const from = <T extends { day: string }>(points: T[]) => points.filter((x) => x.day >= since);
  p.kpis.delta_30d = null;
  p.kpis.threat = {
    ...p.kpis.threat,
    score: 17.6,
    hiring_trend: null,
    explanation:
      "High: score 17.6 = overlap 11 × momentum 1.60 × hiring 1.00 (high from 12, medium from 4). Overlap 11 is the severity-weighted sum (high 3, medium 2, low 1) of threat impacts on JFrog products, from 9 current analyses of 21 public signals in 180 days. Hiring is not counted: the job board has under 30 days of history.",
  };
  const s = p.hiring.series;
  p.hiring = {
    ...p.hiring,
    delta_30d: null,
    board_since: since,
    tags: p.hiring.tags.map((t) => ({ ...t, delta_30d: null })),
    recent: p.hiring.recent.map((r) => (r.id === 7001 ? { ...r, first_seen_at: hoursAgo(now, 9 * 24) } : r)),
    series: { total: from(s.total), department: from(s.department), seniority: from(s.seniority), location: from(s.location), tag: from(s.tag) },
  };
  return p;
}

// ---- JFrog products (GET /products, GET /products/{slug}) -----------------------------------------

type ProductSummary = Schemas["ProductSummary"];
type ProductProfile = Schemas["ProductProfile"];
type ProductTimelineEvent = ProductProfile["timeline"][number];
type Pressure = ProductSummary["pressure"];

/** Each product's line in the JFrog context pack's portfolio, as the API serves it (capabilities only). */
export const PRODUCT_DESCRIPTIONS: Record<string, string> = {
  Artifactory:
    "Universal artifact repository and the core of the platform; 60+ artifact types (packages, files and AI assets), remote repositories that proxy public registries and AI model hubs, virtual repositories, multi-site sync and promotion across environments.",
  Xray: "Software composition analysis; continuously scans repositories, builds and container images for open-source vulnerabilities and license compliance, with remediation in the IDE and CLI.",
  "Advanced Security":
    "Contextual analysis that checks whether a vulnerability is actually reachable or exploitable in the application, plus secrets detection and infrastructure-as-code scanning.",
  Curation:
    "Vets open-source packages, AI models and IDE extensions at the point of request and blocks risky ones before they reach developers (a “dependency firewall”), serving the next policy-compliant version instead.",
  Distribution: "Secure, fast delivery of release bundles to remote sites, clouds, edges, embedded devices and IoT fleets, going beyond a traditional CDN or P2P.",
  AppTrust: "Release governance: policy gates across the SDLC that block non-compliant releases, using evidence of security, quality and compliance, for human- and AI-written software.",
  Runtime: "Security for what is running in production, tracing runtime findings back to the artifact.",
  "JFrog ML": "MLOps platform to build, deploy, manage and monitor AI workflows, from GenAI and LLM applications to classic ML.",
  "AI Catalog":
    "One registry of approved AI assets, stored as artifacts in Artifactory; it exposes shadow AI, checks AI assets for vulnerabilities and malicious code, and blocks risky ones before they are pulled.",
  "MCP Registry": "Governed registry of pre-vetted MCP servers, with access control down to the MCP tool level, integrated with Curation to block malicious or unverified servers.",
  "Container Registry": "Private registry for Docker container images and Helm charts, part of Artifactory, deployable on-prem or in the cloud.",
  Connect:
    "IoT device management on the JFrog Platform: over-the-air updates (commands, scripts, artifacts, files or containers) to device fleets, fleet grouping and continuous monitoring, from developer to device.",
  Platform: "The end-to-end JFrog Platform, the single source of truth for every software package, ML model and agent asset; enterprise setups offer a 99.99% uptime SLA.",
};

const PRODUCT_ROWS: [string, string, Pressure, string[]][] = [
  ["artifactory", "Artifactory", "rising", ["sonatype", "github", "cloudsmith"]],
  ["xray", "Xray", "steady", ["snyk", "sonatype", "github"]],
  ["advanced-security", "Advanced Security", "rising", ["snyk", "github"]],
  ["curation", "Curation", "easing", ["sonatype", "socket"]],
  ["distribution", "Distribution", "none", ["cloudsmith"]],
  ["apptrust", "AppTrust", "steady", ["chainguard"]],
  ["runtime", "Runtime", "none", []],
  ["jfrog-ml", "JFrog ML", "rising", ["github"]],
  ["ai-catalog", "AI Catalog", "steady", ["docker"]],
  ["mcp-registry", "MCP Registry", "none", ["docker", "github"]],
  ["container-registry", "Container Registry", "steady", ["docker", "github", "gitlab"]],
  ["connect", "Connect", "none", []],
  ["platform", "Platform", "easing", ["gitlab", "github"]],
];

/** The 13 products as GET /products serves them (JFROG_PRODUCTS order): 3 rising, 4 steady, 2 easing, 4 under no pressure. */
export function productSummaries(): ProductSummary[] {
  return PRODUCT_ROWS.map(([slug, name, pressure, top]) => ({ slug, name, description: PRODUCT_DESCRIPTIONS[name] ?? null, pressure, pressure_baseline: true, top_competitors: top }));
}

/** Before anything is collected: every product under no pressure, its seeded competitors still listed. */
export function quietProductSummaries(): ProductSummary[] {
  return productSummaries().map((p) => ({ ...p, pressure: "none" }));
}

/** Every day from `from` to `to` days from now (app zone), oldest first. */
function dayRange(from: number, to: number, now: number): string[] {
  return Array.from({ length: to - from + 1 }, (_, i) => localDay(from + i, now));
}

/**
 * Artifactory under rising pressure (threat in most weeks, heaviest in the last four), five
 * competitors (a high, two medium, a low and a seeded one with no recent evidence), a cited
 * brief (one source a hidden signal, one cite unknown), every role's actions (PMM & DevRel has
 * none, and two lines test the quote rule), hiring in its area on six competitors' boards
 * (Cloudsmith's read for 12 days only, yet the fourth most open roles today, so it is named and its
 * line starts there; Chainguard, read for 30 days, and Docker, for 60, fold into one Other that
 * starts once both are read; a seventh board lists nothing), and 12 moves (one about two
 * competitors, so in two lanes).
 */
export function productProfile(now: number = Date.now()): ProductProfile {
  const day = (offset: number) => localDay(offset, now);
  const weeks = momentumWeeks(new Array(13).fill(0), now).map((w) => w.week_start);
  const threat = [2, 0, 3, 1, 2, 0, 3, 2, 1, 6, 5, 8, 7];
  const opportunity = [0, 1, 0, 0, 2, 0, 0, 1, 0, 0, 2, 1, 3];
  const event = (
    signal_id: number,
    offset: number,
    lane: ProductTimelineEvent["lane"],
    importance: number,
    title: string,
    competitor_slug: string,
  ): ProductTimelineEvent => ({ signal_id, day: day(offset), lane, importance, title, competitor_slug });
  const timeline = [
    event(9310, -170, "Market", 41, "Cloudsmith is named a leader in a package-management report", "cloudsmith"),
    event(9311, -150, "Launches", 55, "GitLab adds a virtual registry for Maven", "gitlab"),
    event(9312, -120, "Pricing", 62, "GitHub Packages drops egress fees for public packages", "github"),
    event(9313, -95, "Launches", 70, "Sonatype ships Nexus Repository 4 with a cloud tier", "sonatype"),
    event(9314, -60, "Partners", 48, "Cloudsmith partners with Chainguard on hardened images", "cloudsmith"),
    event(9315, -40, "Launches", 52, "GitLab ships a package registry for Terraform modules", "gitlab"),
    event(9317, -21, "Launches", 74, "GitHub previews artifact attestations for container images", "github"),
    event(9318, -14, "Security", 66, "A malicious-package wave hits public npm mirrors", "github"),
    event(9318, -14, "Security", 66, "A malicious-package wave hits public npm mirrors", "sonatype"),
    event(9303, -9, "Partners", 58, "Cloudsmith comes to the AWS Marketplace", "cloudsmith"),
    event(9302, -3, "Pricing", 77, "GitHub cuts Packages storage prices for Enterprise Cloud", "github"),
    event(9301, -1, "Launches", 88, "Sonatype folds Repository Firewall into Nexus One", "sonatype"),
  ];
  const lastMove = (id: number) => {
    const e = timeline.find((t) => t.signal_id === id)!;
    return { signal_id: e.signal_id, day: e.day, lane: e.lane, importance: e.importance, title: e.title };
  };
  const TYPE: Record<string, SignalCard["signal_type"]> = {
    Launches: "product_launch",
    Pricing: "pricing_change",
    Partners: "partnership",
    Security: "security_incident",
    Market: "analyst_recognition",
  };
  const card = (id: number, competitors: string[], stance: SignalCard["stance"], summary: string, source: string) => {
    const e = timeline.find((t) => t.signal_id === id)!;
    return signalCard({
      id,
      title: e.title,
      importance: e.importance,
      competitors,
      stance,
      summary,
      signal_type: TYPE[e.lane] ?? "other",
      first_seen_at: `${e.day}T06:00:00Z`,
      primary_source: source,
    });
  };

  // Hiring in the area: each competitor's board from its first snapshot, never before it.
  const start = -89;
  const steps: Record<string, [number, number][]> = {
    sonatype: [
      [-89, 9],
      [-60, 11],
      [-30, 13],
      [-8, 14],
    ],
    github: [
      [-89, 6],
      [-45, 5],
    ],
    gitlab: [
      [-89, 2],
      [-20, 4],
    ],
    docker: [[-60, 1]],
    chainguard: [[-30, 2]],
    cloudsmith: [[-12, 3]],
  };
  const by_competitor: ProductProfile["hiring_in_area"]["by_competitor"] = [];
  const total: ProductProfile["hiring_in_area"]["total"] = [];
  for (const [i, d] of dayRange(start, 0, now).entries()) {
    const offset = start + i;
    let sum = 0;
    for (const key of Object.keys(steps).sort()) {
      const changes = steps[key]!;
      if (offset < changes[0]![0]) continue;
      const count = [...changes].reverse().find(([from]) => offset >= from)![1];
      by_competitor.push({ day: d, key, count });
      sum += count;
    }
    total.push({ day: d, count: sum });
  }

  return {
    slug: "artifactory",
    name: "Artifactory",
    description: PRODUCT_DESCRIPTIONS.Artifactory!,
    pressure: "rising",
    pressure_baseline: true,
    pressure_series: weeks.map((week_start, i) => ({ week_start, threat: threat[i]!, opportunity: opportunity[i]! })),
    collected_since: null,
    competitors: [
      { competitor_slug: "sonatype", their_products: ["Nexus Repository"], threat_level: "high", evidence: 14, last_move: lastMove(9301) },
      { competitor_slug: "github", their_products: ["GitHub Packages"], threat_level: "medium", evidence: 7, last_move: lastMove(9302) },
      { competitor_slug: "cloudsmith", their_products: ["Cloudsmith"], threat_level: "medium", evidence: 5, last_move: lastMove(9303) },
      { competitor_slug: "gitlab", their_products: ["GitLab Package Registry"], threat_level: "low", evidence: 2, last_move: lastMove(9315) },
      { competitor_slug: "docker", their_products: ["Docker Hub"], threat_level: "none", evidence: 0, last_move: null },
    ],
    brief: {
      version: 2,
      created_at: `${day(-1)}T04:10:00Z`,
      bottom_line: [
        {
          text: "Sonatype is turning Nexus One into the default bundle for Artifactory renewals, with Repository Firewall included at no extra cost.",
          cites: [1, 2],
        },
        { text: "GitHub's storage price cut makes GitHub Packages the cheapest home for build artifacts on Enterprise Cloud.", cites: [3] },
        { text: "No competitor matches multi-site replication yet, so the pressure is on price and bundling, not capability.", cites: [1, 4, 99] },
      ],
      gaps_parity: [
        { text: "Gap: Nexus One bundles malicious-package blocking with the repository, while Artifactory customers buy Curation on its own.", cites: [1] },
        { text: "Parity: GitHub's artifact attestations match Artifactory's evidence collection for container images.", cites: [5] },
        { text: "Edge: no competitor federates repositories across self-managed and SaaS sites.", cites: [4, 6] },
      ],
      actions: {
        sales: [
          "“Lead renewals with multi-site replication and federation, which Nexus One lacks.”",
          "Ask which repositories must stay self-managed before a Nexus One price comparison.",
          '"Total cost" and "time to migrate" are the two questions to raise in every Sonatype bake-off.',
        ],
        pm: ["Size the gap between Curation as an add-on and Nexus One's bundled firewall.", "Track GitHub's attestation roadmap against Artifactory's evidence collection."],
        exec: ["Decide whether a Curation-included Artifactory tier answers the Nexus One bundle."],
        pmm_devrel: [],
      },
      evidence: { signals: 9 },
      sources: [
        { n: 1, kind: "signal", ref: "signal:9301", title: "Sonatype folds Repository Firewall into Nexus One", url: "/signals/9301", outlet: "Sonatype blog", day: day(-1), excerpt: null },
        { n: 2, kind: "signal", ref: "signal:9313", title: "Sonatype ships Nexus Repository 4 with a cloud tier", url: "/signals/9313", outlet: "DevOps.com", day: day(-95), excerpt: null },
        { n: 3, kind: "signal", ref: "signal:9302", title: "GitHub cuts Packages storage prices for Enterprise Cloud", url: "/signals/9302", outlet: "GitHub blog", day: day(-3), excerpt: null },
        { n: 4, kind: "signal", ref: "signal:9303", title: "Cloudsmith comes to the AWS Marketplace", url: "/signals/9303", outlet: "The New Stack", day: day(-9), excerpt: null },
        { n: 5, kind: "signal", ref: "signal:9317", title: "GitHub previews artifact attestations for container images", url: "/signals/9317", outlet: "GitHub changelog", day: day(-21), excerpt: null },
        { n: 6, kind: "signal", ref: "signal:9399", title: "Signal not available", url: null, outlet: null, day: null, excerpt: null },
      ],
    },
    hiring_in_area: {
      capabilities: ["container-registry", "remote-proxy-caching", "package-promotion"],
      boards: [
        { competitor_slug: "sonatype", board_since: day(-150), last_listed: day(0), stale: false },
        { competitor_slug: "github", board_since: day(-120), last_listed: day(0), stale: false },
        { competitor_slug: "gitlab", board_since: day(-90), last_listed: day(0), stale: false },
        { competitor_slug: "docker", board_since: day(-60), last_listed: day(0), stale: false },
        { competitor_slug: "chainguard", board_since: day(-30), last_listed: day(0), stale: false },
        { competitor_slug: "cloudsmith", board_since: day(-12), last_listed: day(0), stale: false },
        { competitor_slug: "snyk", board_since: null, last_listed: day(0), stale: false },
      ],
      total,
      by_competitor,
    },
    timeline,
    signals: [
      card(9301, ["sonatype"], "threat", "Sonatype now includes Repository Firewall in every Nexus One plan, aimed at Artifactory renewals.", "Sonatype blog"),
      card(9302, ["github"], "threat", "GitHub halved Packages storage prices for Enterprise Cloud customers.", "GitHub blog"),
      card(9303, ["cloudsmith"], "neutral", "Cloudsmith is now sold through the AWS Marketplace, with AWS billing.", "The New Stack"),
      card(9318, ["sonatype", "github"], "opportunity", "A wave of malicious packages on public npm mirrors renews demand for vetted proxies.", "BleepingComputer"),
      card(9317, ["github"], "threat", "GitHub previews signed attestations for container images in Packages.", "GitHub changelog"),
      card(9315, ["gitlab"], "neutral", "GitLab's package registry now stores Terraform modules.", "GitLab blog"),
      card(9314, ["cloudsmith"], "neutral", "Cloudsmith and Chainguard bundle hardened images with the registry.", "Cloudsmith blog"),
      card(9313, ["sonatype"], "threat", "Nexus Repository 4 adds a hosted cloud tier.", "DevOps.com"),
      card(9312, ["github"], "threat", "GitHub dropped egress fees for public packages.", "GitHub blog"),
    ],
  };
}

/**
 * Connect, with no competitive signal at all: no pressure, no competitor, no brief, no move, and
 * its area's hiring read on two boards with nothing counted (a measured nothing, not a 0 line).
 */
export function quietProduct(now: number = Date.now()): ProductProfile {
  return {
    slug: "connect",
    name: "Connect",
    description: PRODUCT_DESCRIPTIONS.Connect!,
    pressure: "none",
    pressure_baseline: true,
    pressure_series: [],
    collected_since: null,
    competitors: [],
    brief: null,
    hiring_in_area: {
      capabilities: ["edge-distribution"],
      boards: [
        { competitor_slug: "sonatype", board_since: localDay(-150, now), last_listed: localDay(0, now), stale: false },
        { competitor_slug: "github", board_since: localDay(-120, now), last_listed: localDay(0, now), stale: false },
      ],
      total: dayRange(-89, 0, now).map((day) => ({ day, count: 0 })),
      by_competitor: [],
    },
    timeline: [],
    signals: [],
  };
}

// ---- the intelligence pages' rules (GET /intel-reference) -------------------------------

type IntelReference = Schemas["IntelReferenceOut"];

/** JFrog's products in the backend's order (`JFROG_PRODUCTS`), as GET /intel-reference serves them. */
export const JFROG_PRODUCT_NAMES = [
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

/**
 * The rules the competitor and product pages quote, with the backend's default settings and
 * constants (app/core/config.py, app/intel/profile.py, app/intel/hiring.py). `collected_since` is
 * null (nothing bounds the weeks) unless a test serves a young deployment.
 */
export function intelReference(overrides: Partial<IntelReference> = {}): IntelReference {
  return {
    jfrog_products: [...JFROG_PRODUCT_NAMES],
    severity_weights: { high: 3, medium: 2, low: 1 },
    overlap: { high_evidence: 6, medium_evidence: 3, lookback_days: 180 },
    threat: {
      high_score: 12,
      medium_score: 4,
      min_signals: 3,
      lookback_days: 180,
      momentum_factor_min: 0.5,
      momentum_factor_max: 2,
      hiring_factor_bound: 0.25,
      hiring_history_days: 30,
    },
    trend: { weeks: 13, recent_weeks: 4, baseline_weeks: 9, cap: 3, rising_ratio: 1.25, easing_ratio: 0.8 },
    briefs: { min_signals: 3, lookback_days: 180 },
    collected_since: null,
    ...overrides,
  };
}

// ---- a young deployment ----------------------------------------------------------
//
// FrogEye filed its first document 20 days ago, so the weekly series hold 3 collected weeks and
// every trend has no baseline yet; the job boards were found and first listed in full today.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** The backend's own day words in an explanation ("30 Sep 2026"). */
const apiDay = (day: string) => {
  const d = new Date(`${day}T00:00:00Z`);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
};

/** The day collection began on day 1: 20 days before `now` (3 collected weekly windows). */
export const dayOneSince = (now: number = Date.now()) => localDay(-20, now);

/** The rules on day 1: the backend's defaults, collecting since 20 days ago. */
export function dayOneReference(now: number = Date.now()): IntelReference {
  return intelReference({ collected_since: dayOneSince(now) });
}

const youngMomentum = (since: string) => `Momentum has no baseline yet: FrogEye has collected since ${apiDay(since)}, under the 13 weeks a trend compares, so its factor is 1.00.`;
const LEVEL_RANK = { high: 3, medium: 2, low: 1, insufficient: 0 } as const;

/**
 * The overview on day 1: the realistic overview with only its 3 collected weeks of momentum, no
 * trend anywhere (every momentum factor 1.00, so scores are the overlap), and every measured board
 * first listed today (no 30-day change). In the API's order: level, then score, tier and name.
 */
export function dayOneOverview(now: number = Date.now()): CompetitorSummary[] {
  const since = dayOneSince(now);
  return competitorOverview(now)
    .map((r) => {
      const momentum = r.momentum ? r.momentum.slice(-3) : null;
      const board = r.open_roles !== null ? { delta_30d: null, board_since: localDay(0, now) } : {};
      if (r.threat.level === "insufficient") return { ...r, momentum, ...board };
      const score = r.threat.overlap ?? 0;
      const level = score >= 12 ? "high" : score >= 4 ? "medium" : "low";
      const hiring = r.open_roles !== null ? "Hiring is not counted: the job board has under 30 days of history." : r.has_board ? "Hiring is not counted: the job board has not been listed in full yet." : "Hiring is not counted: no public job board found.";
      const explanation = `${level.charAt(0).toUpperCase()}${level.slice(1)}: score ${score.toFixed(1)} = overlap ${score} × momentum 1.00 × hiring 1.00 (high from 12, medium from 4). ${youngMomentum(since)} ${hiring}`;
      return { ...r, momentum, ...board, threat: { ...r.threat, level, score, momentum_trend: null, hiring_trend: null, explanation } as ThreatOut };
    })
    .sort((a, b) => LEVEL_RANK[b.threat.level] - LEVEL_RANK[a.threat.level] || (b.threat.score ?? 0) - (a.threat.score ?? 0) || a.tier - b.tier || a.name.localeCompare(b.name));
}

/**
 * Sonatype on day 1: 3 collected weeks of momentum, share of voice and Hacker News, no trend (so
 * a medium threat at its overlap), its moves of the 20 days collected, and its board first listed
 * today: one snapshot, no 30-day change anywhere.
 */
export function dayOneProfile(now: number = Date.now()): CompetitorProfile {
  const p = competitorProfile(now);
  const since = dayOneSince(now);
  const today = localDay(0, now);
  const collected = <T extends { week_start: string }>(weeks: T[]) => weeks.filter((w) => w.week_start >= since);
  const todayOnly = <T extends { day: string }>(points: T[]) => points.filter((x) => x.day === today);
  const s = p.hiring.series;
  const explanation = `Medium: score 11.0 = overlap 11 × momentum 1.00 × hiring 1.00 (high from 12, medium from 4). Overlap 11 is the severity-weighted sum (high 3, medium 2, low 1) of threat impacts on JFrog products, from 9 current analyses of 21 public signals in 180 days. ${youngMomentum(since)} Hiring is not counted: the job board has under 30 days of history.`;
  return {
    ...p,
    collected_since: since,
    kpis: { ...p.kpis, threat: { ...p.kpis.threat, level: "medium", score: 11, momentum_trend: null, hiring_trend: null, explanation }, delta_30d: null },
    momentum: p.momentum ? p.momentum.slice(-3) : null,
    timeline: p.timeline.filter((e) => e.day >= since),
    hiring: {
      ...p.hiring,
      delta_30d: null,
      board_since: today,
      tags: p.hiring.tags.map((t) => ({ ...t, delta_30d: null })),
      series: { total: todayOnly(s.total), department: todayOnly(s.department), seniority: todayOnly(s.seniority), location: todayOnly(s.location), tag: todayOnly(s.tag) },
    },
    market: { ...p.market, share_of_voice: collected(p.market.share_of_voice) },
    community: { ...p.community, mentions: collected(p.community.mentions) },
  };
}

/** The products on day 1: every product with threat in the collected weeks reads new pressure with no baseline. */
export function dayOneProducts(): ProductSummary[] {
  return productSummaries().map((p) => ({ ...p, pressure: p.pressure === "none" ? "none" : "rising", pressure_baseline: false }));
}

/**
 * Artifactory on day 1: 3 collected weeks of pressure (no baseline, so "rising" as new pressure),
 * its moves of the 20 days collected, and every competitor board first listed today.
 */
export function dayOneProduct(now: number = Date.now()): ProductProfile {
  const p = productProfile(now);
  const since = dayOneSince(now);
  const today = localDay(0, now);
  const timeline = p.timeline.filter((e) => e.day >= since);
  const ids = new Set(timeline.map((e) => e.signal_id));
  const area = p.hiring_in_area;
  return {
    ...p,
    pressure: "rising",
    pressure_baseline: false,
    collected_since: since,
    pressure_series: p.pressure_series.filter((w) => w.week_start >= since),
    competitors: p.competitors.map((c) => (c.last_move && !ids.has(c.last_move.signal_id) ? { ...c, last_move: null } : c)),
    timeline,
    signals: p.signals.filter((sig) => ids.has(sig.id)),
    hiring_in_area: {
      ...area,
      boards: area.boards.map((b) => ({ ...b, board_since: b.board_since === null ? null : today })),
      total: area.total.filter((t) => t.day === today),
      by_competitor: area.by_competitor.filter((t) => t.day === today),
    },
  };
}
