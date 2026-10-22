import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { Schemas } from "@/lib/api/client";
import { agentRun, agentStep, costs, LIVE_FUNNEL_RUN1, LIVE_FUNNEL_RUN2 } from "@/test/fixtures";
import { MockEventSource, server } from "@/test/msw";
import { renderAs } from "@/test/render";
import { agentRunRefetchInterval, agentRunsRefetchInterval } from "./useAgentRuns";

type AgentRunOut = Schemas["AgentRunOut"];
type AgentStepOut = Schemas["AgentStepOut"];

/** Serve one run and its trace; `steps` sees the request's after_step (the live-append cursor). */
function serveRun(run: AgentRunOut, steps: AgentStepOut[] | ((after: number) => AgentStepOut[])) {
  server.use(
    http.get("/api/v1/agent-runs/:id", ({ params }) =>
      Number(params.id) === run.id ? HttpResponse.json(run) : HttpResponse.json({ detail: "Agent run not found" }, { status: 404 }),
    ),
    http.get("/api/v1/agent-runs/:id/steps", ({ request }) => {
      const after = Number(new URL(request.url).searchParams.get("after_step") ?? 0);
      const all = typeof steps === "function" ? steps(after) : steps;
      return HttpResponse.json(all.filter((s) => s.step_no > after));
    }),
  );
}

function nodeGroups(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>("[data-node]")];
}

const TRACE = [
  agentStep({ step_no: 1, node: "triage", agent: "triage", result_summary: "relevant: Sonatype firewall bundle" }),
  agentStep({ step_no: 2, node: "triage", agent: "triage", result_summary: "irrelevant: hiring post" }),
  agentStep({
    step_no: 3,
    node: "analyze",
    agent: "analyst",
    kind: "tool",
    tool_name: "search_intel",
    model: null,
    args: { query: "dependency firewall", k: 8 },
    result_summary: "3 documents about Repository Firewall",
    tokens_in: 0,
    tokens_out: 0,
    cost_usd: 0,
    latency_ms: 140,
  }),
  agentStep({ step_no: 4, node: "analyze", agent: "embed", model: "text-embedding-3-small", tokens_out: 0, cost_usd: 0.00001 }),
  agentStep({
    step_no: 5,
    node: "analyze",
    agent: "analyst",
    model: "gpt-6-luna",
    tokens_in: 2_400,
    cost_usd: 0.00032,
    args: { task: "analyst", tier: "smart", prompt: "analyst", version: "3" },
  }),
];

it("lists each run with its status, duration, tokens, cost and funnel", async () => {
  server.use(
    http.get("/api/v1/agent-runs", () =>
      HttpResponse.json([
        agentRun({ id: 77 }),
        agentRun({
          id: 76,
          status: "failed",
          started_at: "2026-10-16T03:00:00Z",
          finished_at: "2026-10-16T03:04:10Z",
          notes: "triage: error: TimeoutError",
          funnel: { fetched: 80 },
          cost_usd: 0.02,
          briefing_date: null,
        }),
      ]),
    ),
  );
  renderAs("pm", "/admin/agents");

  const row = (await screen.findByRole("link", { name: /^Run 77,/ })).closest("tr")!;
  expect(within(row).getByText("Succeeded")).toBeInTheDocument();
  expect(within(row).getByText("Daily intelligence")).toBeInTheDocument(); // the graph
  expect(row).toHaveTextContent("12m 00s");
  expect(row).toHaveTextContent("182K / 21K");
  expect(row).toHaveTextContent("$0.31");
  expect(row).toHaveTextContent(/412 read.*61 relevant.*14 new signals.*9 analysed/);
  expect(within(row).getByRole("link", { name: /Published/ })).toHaveAttribute("href", "/briefing/2026-10-17");
  expect(within(row).getByRole("link", { name: /^Run 77,/ })).toHaveAttribute("href", "/admin/agents/77");

  const failed = screen.getByRole("link", { name: /^Run 76,/ }).closest("tr")!;
  expect(within(failed).getByText("Failed")).toBeInTheDocument();
  expect(failed).toHaveTextContent("4m 10s");
  expect(failed).toHaveTextContent("1 stage error");
  expect(failed).toHaveTextContent("None");
});

it("groups the run's steps by node, then by agent, in the order they ran", async () => {
  serveRun(agentRun({ id: 77 }), TRACE);
  renderAs("analyst", "/admin/agents/77");

  await userEvent.click(await screen.findByRole("button", { name: /expand all/i }));
  const [triage, analyze, ...rest] = nodeGroups();
  expect(rest).toHaveLength(0);
  expect(within(triage!).getByRole("button", { name: /Triaging documents/ })).toHaveAttribute("aria-expanded", "true");
  expect(within(analyze!).getByRole("button", { name: /Analysing signals/ })).toBeInTheDocument();

  // node -> agent: the analyst's two steps sit together although an embedding ran between them
  const agents = within(analyze!).getAllByRole("heading", { level: 4 });
  expect(agents.map((h) => h.textContent)).toEqual([expect.stringMatching(/^Analyst/), expect.stringMatching(/^Embeddings/)]);
  const analyst = agents[0]!.parentElement!;
  expect(analyst).toHaveTextContent(/#3.*#5/);
  expect(within(triage!).queryByText("search_intel")).not.toBeInTheDocument();

  // a tool step: its name, arguments and result; an LLM step: its model, tokens and cost
  expect(within(analyst).getByText("search_intel")).toBeInTheDocument();
  expect(within(analyst).getByText(/query: "dependency firewall"/)).toBeInTheDocument();
  expect(within(analyst).getByText("3 documents about Repository Firewall")).toBeInTheDocument();
  expect(within(analyst).getByText("gpt-6-luna")).toBeInTheDocument();
  expect(analyst).toHaveTextContent("2.4K in · 88 out · $0.00032 · 820ms");

  // an LLM call's arguments are bookkeeping: one click away, not on every row
  expect(within(analyst).queryByText(/tier: "smart"/)).not.toBeInTheDocument();
  await userEvent.click(within(analyst).getByRole("button", { name: "Show arguments" }));
  expect(within(analyst).getByText(/"tier": "smart"/)).toBeInTheDocument();
});

it("never shows call arguments or results to non-analysts, even if the API sent them", async () => {
  serveRun(agentRun({ id: 77 }), TRACE);
  renderAs("pm", "/admin/agents/77");

  await userEvent.click(await screen.findByRole("button", { name: /Analysing signals/ }));
  expect(screen.getByText("search_intel")).toBeInTheDocument();
  // a result can quote a signal the reader may not see; arguments likewise.
  expect(screen.queryByText("3 documents about Repository Firewall")).not.toBeInTheDocument();
  expect(screen.queryByText(/dependency firewall/)).not.toBeInTheDocument();
  expect(screen.queryByText("Args")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Show arguments" })).not.toBeInTheDocument();
  expect(screen.getByText(/Call arguments and results are shown to CI analysts only/)).toBeInTheDocument();
});

it("tells everyone which steps failed and why in a word, without their results", async () => {
  const failing = [
    agentStep({ step_no: 1, node: "analyze", agent: "analyst", status: "error", error_kind: "LLMError", result_summary: null }),
    agentStep({ step_no: 2, node: "newsroom", agent: "editor", kind: "node", status: "budget", result_summary: null }),
    agentStep({ step_no: 3, node: "triage", agent: "triage", status: "refused", result_summary: null }),
  ];
  serveRun(agentRun({ id: 77, notes: "stage_errors" }), failing);
  renderAs("sales", "/admin/agents/77");

  // nodes with failed steps open by themselves and say so
  expect(await screen.findByText("Failed: LLMError")).toBeInTheDocument();
  expect(screen.getAllByText("Errors").length).toBeGreaterThan(0);
  await userEvent.click(screen.getByRole("button", { name: /expand all/i }));
  expect(screen.getByText("Stopped at the budget cap")).toBeInTheDocument();
  expect(screen.getByText("Refused by the model")).toBeInTheDocument();
  // the run's notes: the flags, and that stages failed, with the details for analysts
  expect(screen.getByText("Some stages reported errors")).toBeInTheDocument();
  expect(screen.getByText(/CI analysts can see each error/)).toBeInTheDocument();
});

it("labels a step with no agent by its node, never as anonymous plumbing", async () => {
  // Older runs carry no agent on the triage, disambiguation and analyst steps.
  serveRun(agentRun({ id: 77 }), [
    agentStep({ step_no: 1, node: "triage", agent: null }),
    agentStep({ step_no: 2, node: "disambiguate", agent: null }),
    agentStep({ step_no: 3, node: "analyze", agent: null }),
    agentStep({ step_no: 4, node: "score", agent: null, kind: "node" }),
  ]);
  renderAs("analyst", "/admin/agents/77");
  await userEvent.click(await screen.findByRole("button", { name: /expand all/i }));
  const labels = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
  expect(labels).toEqual([
    expect.stringMatching(/^Triage/),
    expect.stringMatching(/^Disambiguate/),
    expect.stringMatching(/^Analyst/),
    expect.stringMatching(/^Pipeline/),
  ]);
});

it("says what a run's money went on, analysed signals included", async () => {
  serveRun(agentRun({ id: 79, funnel: { ...LIVE_FUNNEL_RUN2 }, cost_usd: 0.158 }), TRACE);
  renderAs("pm", "/admin/agents/79");
  // the re-run filed no new signal: its money went on the 104 carried over
  expect(await screen.findByText(/filed 0 new signals and analysed 104/)).toBeInTheDocument();
  expect(screen.getByText(/items read/).closest("p")).toHaveTextContent(/153 items read.*0 new signals.*104 analysed/);
});

const DEEP_RUN = agentRun({ id: 83, funnel: { ...LIVE_FUNNEL_RUN2, signals_deep_analyzed: 19 }, cost_usd: 0.077 });

it("counts the full analyses a run wrote, in its lead and its funnel line", async () => {
  serveRun(DEEP_RUN, TRACE);
  renderAs("analyst", "/admin/agents/83");
  expect(await screen.findByText(/analysed 104 and wrote 19 full analyses/)).toBeInTheDocument();
  expect(screen.getByText(/items read/).closest("p")).toHaveTextContent(/104 analysed.*19 full analyses/);
});

it("counts them in the runs list and its lead too", async () => {
  server.use(http.get("/api/v1/agent-runs", () => HttpResponse.json([DEEP_RUN])));
  renderAs("analyst", "/admin/agents");
  expect(await screen.findByText(/104 analysed, 19 full analyses/)).toBeInTheDocument();
  const cell = screen.getAllByText(/full analyses/).find((el) => el.closest("tr"));
  expect(cell?.closest("tr")).toHaveTextContent(/104\s*analysed.*19\s*full analyses/);
});

it("says nothing of full analyses when a run wrote none", async () => {
  serveRun(agentRun({ id: 84, funnel: { ...LIVE_FUNNEL_RUN2, signals_deep_analyzed: 0 } }), TRACE);
  renderAs("analyst", "/admin/agents/84");
  expect(await screen.findByText(/analysed 104/)).toBeInTheDocument();
  expect(screen.queryByText(/full analys/)).not.toBeInTheDocument();
});

it("titles and times a run on the app's calendar, not the browser's", async () => {
  // 22:25 UTC on Sunday is 01:25 on Monday in Asia/Jerusalem; the suite runs in UTC.
  serveRun(agentRun({ id: 80, started_at: "2026-10-18T22:25:00Z", finished_at: "2026-10-18T22:51:00Z", funnel: { ...LIVE_FUNNEL_RUN1 } }), TRACE);
  renderAs("pm", "/admin/agents/80");
  expect(await screen.findByRole("heading", { name: /Daily run, Monday 19 October/ })).toBeInTheDocument();
  expect(screen.getByText("Started").parentElement).toHaveTextContent("01:25");
});

it("appends new steps live when the stream reports progress for the open run", async () => {
  const run = agentRun({ id: 77, status: "running", finished_at: null, funnel: null, briefing_date: null, cost_usd: 0, tokens_in: 0, tokens_out: 0 });
  const later = agentStep({ step_no: 3, node: "triage", result_summary: "third, arrived live" });
  let released = false;
  const cursors: number[] = [];
  serveRun(run, (after) => {
    cursors.push(after);
    return [TRACE[0]!, TRACE[1]!, ...(released ? [later] : [])];
  });
  renderAs("analyst", "/admin/agents/77");

  // while live, the node the run is in stays open
  expect(await screen.findByText("irrelevant: hiring post")).toBeInTheDocument();
  expect(screen.getByText(/New steps appear here as each stage finishes/)).toBeInTheDocument();

  released = true;
  act(() => MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 99, stage: "triage", counts: {} }));
  expect(cursors).toEqual([0]); // another run's progress fetches nothing

  act(() => MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 77, stage: "triage", counts: { fetched: 412 } }));
  expect(await screen.findByText("third, arrived live")).toBeInTheDocument();
  expect(cursors).toEqual([0, 2]); // only the steps after the last one seen
  expect(screen.getByText("New")).toBeInTheDocument();
  expect(screen.getAllByText("irrelevant: hiring post")).toHaveLength(1);
});

it("links the run to the briefing it filed", async () => {
  serveRun(agentRun({ id: 77 }), TRACE);
  renderAs("sales", "/admin/agents/77");
  expect(await screen.findByRole("link", { name: /open the briefing/i })).toHaveAttribute("href", "/briefing/2026-10-17");
  expect(screen.getByRole("link", { name: /Published · Sat 17 Oct/ })).toHaveAttribute("href", "/briefing/2026-10-17");
});

it("shows today's spend against the daily cap and the total against the total cap", async () => {
  const week = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18"];
  server.use(
    http.get("/api/v1/costs", () =>
      HttpResponse.json(
        costs({
          today_usd: 0.62,
          daily_budget_usd: 0.75,
          total_usd: 4.1,
          total_budget_usd: 9,
          last_7_days: week.map((date, i) => ({ date, cost_usd: [0.31, 0, 0.42, 0.28, 0.55, 0.47, 0.62][i]! })),
          by_task: [{ task: "analyst", calls: 120, cost_usd: 2.6 }],
          by_model: [{ model: "gpt-6-luna", calls: 140, cost_usd: 2.9, cached_calls: 12 }],
        }),
      ),
    ),
  );
  renderAs("sales", "/admin/agents");

  const today = await screen.findByRole("meter", { name: "Today" });
  expect(today).toHaveAttribute("aria-valuenow", "0.62");
  expect(today).toHaveAttribute("aria-valuemax", "0.75");
  expect(today).toHaveAttribute("aria-valuetext", "$0.62 of $0.75, 83% of the daily cap");
  expect(today.parentElement).toHaveTextContent("$0.62 of $0.75");
  expect(screen.getByText("Near the cap")).toBeInTheDocument(); // never colour alone
  expect(screen.getByRole("meter", { name: "All time" })).toHaveAttribute("aria-valuetext", "$4.10 of $9.00, 46% of the total cap");

  // the week: one column per day, each value printed and on hover
  expect(screen.getByRole("button", { name: "Today: $0.62, 83% of the $0.75 daily cap" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Wednesday 14 Oct: \$0\.42/ })).toBeInTheDocument();
  expect(screen.getByRole("figure", { name: /the last 7 days/ })).toHaveTextContent("$2.65 in all");
  expect(screen.getByRole("row", { name: "Analyst 120 $2.60 63%" })).toBeInTheDocument();
  expect(screen.getByRole("row", { name: "gpt-6-luna 140 12 $2.90" })).toBeInTheDocument();
});

it("a reload mid-run still shows the run pill, linking to the live trace", async () => {
  const running = agentRun({ id: 81, status: "running", started_at: new Date(Date.now() - 4 * 60_000).toISOString(), finished_at: null, funnel: null });
  server.use(http.get("/api/v1/agent-runs", () => HttpResponse.json([running])));
  renderAs("exec", "/briefing");
  expect(await screen.findByRole("link", { name: /Daily run in progress/ })).toHaveAttribute("href", "/admin/agents/81");
});

describe("when a live run ends", () => {
  // the pages poll every 3s; the clock is advanced instead of waited on, so these run in well
  // under the 5s default, the same way every time. It still moves in real time too, for MSW.
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());
  const pause = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
  const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

  it("polls only while a run is recent and running", () => {
    const running = agentRun({ status: "running", started_at: minutesAgo(4), finished_at: null });
    expect(agentRunRefetchInterval(running)).toBe(3_000);
    expect(agentRunRefetchInterval(agentRun({ status: "succeeded" }))).toBe(false);
    expect(agentRunRefetchInterval(agentRun({ status: "running", started_at: minutesAgo(7 * 60), finished_at: null }))).toBe(false);
    expect(agentRunsRefetchInterval([agentRun(), running])).toBe(3_000);
    expect(agentRunsRefetchInterval([agentRun(), agentRun({ status: "failed" })])).toBe(false);
  });

  it("leaves Live and freezes its totals once the API says the run finished, even when the last event came first", async () => {
    const started = minutesAgo(13);
    const live = agentRun({ id: 77, status: "running", started_at: started, finished_at: null, funnel: null, briefing_date: null, cost_usd: 0, tokens_in: 0, tokens_out: 0 });
    const done = agentRun({ id: 77, started_at: started, finished_at: minutesAgo(1), cost_usd: 0.31 });
    const missed = agentStep({ step_no: 3, node: "triage", result_summary: "written after the last event" });
    let finished = false;
    serveRun(live, () => [TRACE[0]!, TRACE[1]!, ...(finished ? [missed] : [])]);
    server.use(http.get("/api/v1/agent-runs/:id", () => HttpResponse.json(finished ? done : live)));
    renderAs("analyst", "/admin/agents/77");
    expect(await screen.findByText(/New steps appear here/)).toBeInTheDocument();

    // The final stage's event lands before the runner commits the finished run.
    act(() => MockEventSource.emit("run.progress", { pipeline_run_id: 12, agent_run_id: 77, stage: "publish", counts: {} }));
    await pause(100);
    expect(screen.getByText(/New steps appear here/)).toBeInTheDocument();

    finished = true;
    await pause(3_000); // the next poll
    await waitFor(() => expect(screen.queryByText(/New steps appear here/)).not.toBeInTheDocument());
    expect(screen.getByText("Succeeded")).toBeInTheDocument();
    const duration = screen.getByText("Duration").parentElement!;
    expect(duration).toHaveTextContent("12m 00s");
    expect(screen.getByText("Cost").parentElement).toHaveTextContent("$0.31");
    expect(screen.queryByText(/so far/)).not.toBeInTheDocument();
    // one last catch-up picks up the steps written after the last event; the node stays open
    expect(await screen.findByText("written after the last event")).toBeInTheDocument();
    await pause(1_100);
    expect(duration).toHaveTextContent("12m 00s");
  });

  it("flips a run that died without any event, clears the run pill, then stops polling", async () => {
    const started = minutesAgo(4);
    const live = agentRun({ id: 78, status: "running", started_at: started, finished_at: null, funnel: null, briefing_date: null, cost_usd: 0 });
    const dead = agentRun({ id: 78, status: "failed", started_at: started, finished_at: minutesAgo(1), error: "RuntimeError: ingest failed", funnel: null, briefing_date: null });
    let died = false;
    let detailCalls = 0;
    serveRun(live, [TRACE[0]!]);
    server.use(
      http.get("/api/v1/agent-runs/:id", () => {
        detailCalls += 1;
        return HttpResponse.json(died ? dead : live);
      }),
      http.get("/api/v1/agent-runs", () => HttpResponse.json([died ? dead : live])),
    );
    renderAs("analyst", "/admin/agents/78");
    expect(await screen.findByRole("link", { name: /Daily run in progress/ })).toBeInTheDocument(); // seeded run
    expect(await screen.findByText(/New steps appear here/)).toBeInTheDocument();

    died = true;
    await pause(3_000); // the next poll
    await waitFor(() => expect(screen.getByText("Failed")).toBeInTheDocument());
    expect(screen.queryByText(/New steps appear here/)).not.toBeInTheDocument();
    expect(screen.getByText("RuntimeError: ingest failed")).toBeInTheDocument();
    await pause(3_000); // the runs list's next poll clears the pill
    await waitFor(() => expect(screen.queryByRole("link", { name: /Daily run in progress/ })).not.toBeInTheDocument());

    const settled = detailCalls;
    await pause(3_500);
    expect(detailCalls).toBe(settled);
  });
});
