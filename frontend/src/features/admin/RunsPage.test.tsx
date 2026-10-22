import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { run } from "@/test/fixtures";
import { server } from "@/test/msw";
import { renderAs } from "@/test/render";
import { formatClock } from "@/lib/time";
import { prefilterPassRate, runsRefetchInterval } from "./useRuns";

it("Run now posts and handles conflict", async () => {
  server.use(http.post("/api/v1/runs", () => HttpResponse.json({ detail: "A run is already in progress" }, { status: 409 })));
  renderAs("analyst", "/admin/runs");
  await userEvent.click(await screen.findByRole("button", { name: /run now/i }));
  expect(await screen.findByText(/already in progress/i)).toBeInTheDocument();
});

it("Run now shows the cooldown message from a 429", async () => {
  const detail = "A run finished 12 minutes ago. The next manual run is available at 14:05.";
  server.use(http.post("/api/v1/runs", () => HttpResponse.json({ detail }, { status: 429 })));
  renderAs("analyst", "/admin/runs");
  await userEvent.click(await screen.findByRole("button", { name: /run now/i }));
  expect(await screen.findByText(detail)).toBeInTheDocument();
});

it("hints when the next manual run opens, using the server's time", async () => {
  const next = new Date(Date.now() + 20 * 60_000).toISOString();
  server.use(
    http.get("/api/v1/runs", () => HttpResponse.json([run({ id: 1, status: "succeeded", finished_at: new Date().toISOString() })])),
    http.get("/api/v1/runs/cooldown", () => HttpResponse.json({ next_manual_run_at: next })),
  );
  renderAs("analyst", "/admin/runs");
  expect(await screen.findByText(new RegExp(`Next manual run available at ${formatClock(Date.parse(next))}`))).toBeInTheDocument();
});

it("shows no cooldown hint when the server says a run can start now", async () => {
  server.use(http.get("/api/v1/runs", () => HttpResponse.json([run({ id: 1 })])));
  renderAs("analyst", "/admin/runs");
  await screen.findByRole("button", { name: /run now/i });
  expect(screen.queryByText(/Next manual run available/)).not.toBeInTheDocument();
});

it("shows each run's funnel with numeric labels", async () => {
  server.use(http.get("/api/v1/runs", () => HttpResponse.json([run({ id: 9 })])));
  renderAs("pm", "/admin/runs");
  const funnel = await screen.findByRole("img", { name: /1,240 fetched/i });
  expect(funnel).toHaveAccessibleName(/312 new/i);
  expect(funnel).toHaveAccessibleName(/298 documents/i);
  expect(funnel).toHaveAccessibleName(/41 passed/i);
  expect(screen.queryByRole("button", { name: /run now/i })).not.toBeInTheDocument();
});

it("renders a failed run with partial stats", async () => {
  server.use(
    http.get("/api/v1/runs", () =>
      HttpResponse.json([run({ status: "failed", stats: { fetched: 80 }, error: "OperationalError: database is locked" })]),
    ),
  );
  renderAs("analyst", "/admin/runs");
  expect(await screen.findByText("Failed")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: /80 fetched/i })).toHaveAccessibleName(/passed not recorded/i);
});

it("polls every 3 s only while a run is genuinely running", () => {
  expect(runsRefetchInterval([run({ status: "running", finished_at: null, active: true })])).toBe(3000);
  expect(runsRefetchInterval([run(), run({ status: "failed" })])).toBe(false);
  expect(runsRefetchInterval(undefined)).toBe(false);
  // A "running" row left behind by a stopped worker: no lock holder, so nothing to watch.
  expect(runsRefetchInterval([run({ status: "running", finished_at: null, active: false })])).toBe(false);
});

it("treats a running row without a live worker as interrupted and lets the analyst start a run", async () => {
  const startedAt = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  server.use(
    http.get("/api/v1/runs", () =>
      HttpResponse.json([run({ id: 7, status: "running", started_at: startedAt, finished_at: null, stats: {}, active: false })]),
    ),
  );
  renderAs("analyst", "/admin/runs");
  expect(await screen.findByText("Interrupted")).toBeInTheDocument();
  expect(screen.getByText(/stopped before it finished; the next run marks it as failed/i)).toBeInTheDocument();
  expect(screen.queryByText(/the last run finished/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/a run is in progress: it started/i)).not.toBeInTheDocument();
  const button = screen.getByRole("button", { name: /run now/i });
  expect(button).toBeEnabled();
});

it("shows a live run as running and disables Run now", async () => {
  const startedAt = new Date(Date.now() - 60 * 1000).toISOString();
  server.use(
    http.get("/api/v1/runs", () =>
      HttpResponse.json([run({ id: 8, status: "running", started_at: startedAt, finished_at: null, stats: {}, active: true })]),
    ),
  );
  renderAs("analyst", "/admin/runs");
  expect(await screen.findByText(/a run is in progress/i)).toBeInTheDocument();
  expect(screen.getByText("Counts arrive when the run finishes.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /run now/i })).not.toBeInTheDocument();
});

it("doesn't point at an error that a failed run never recorded", async () => {
  server.use(http.get("/api/v1/runs", () => HttpResponse.json([run({ status: "failed", stats: {}, error: null })])));
  renderAs("pm", "/admin/runs");
  expect(await screen.findByText(/the last run failed/i)).toBeInTheDocument();
  expect(screen.queryByText(/error is below/i)).not.toBeInTheDocument();
});

it("points at the error of a failed run that recorded one", async () => {
  server.use(http.get("/api/v1/runs", () => HttpResponse.json([run({ status: "failed", stats: {}, error: "interrupted (worker stopped before finishing)" })])));
  renderAs("pm", "/admin/runs");
  expect(await screen.findByText(/the last run failed .*its error is below\./i)).toBeInTheDocument();
  expect(screen.getByText("interrupted (worker stopped before finishing)")).toBeInTheDocument();
});

it("measures the prefilter against what it screened, not against documents created", () => {
  // Duplicates are dropped before the prefilter runs: 3,917 passed and 307 were rejected.
  const stats = { documents_created: 5152, duplicates: 928, prefilter_passed: 3917, prefilter_rejected: 307 };
  expect(prefilterPassRate(stats)).toBeCloseTo(3917 / 4224);
  expect(prefilterPassRate({ prefilter_passed: 0, prefilter_rejected: 0 })).toBeNull();
  expect(prefilterPassRate({ prefilter_passed: 12 })).toBeNull();
});

it("shows the corrected prefilter pass rate in a run's details", async () => {
  server.use(
    http.get("/api/v1/runs", () =>
      HttpResponse.json([
        run({
          id: 1,
          stats: {
            sources_attempted: 108,
            sources_failed: 15,
            fetched: 5219,
            new_raw: 5219,
            documents_created: 5152,
            duplicates: 928,
            prefilter_passed: 3917,
            prefilter_rejected: 307,
            metrics_written: 20,
          },
        }),
      ]),
    ),
  );
  renderAs("pm", "/admin/runs");
  await userEvent.click(await screen.findByRole("button", { name: /details for run 1/i }));
  expect(screen.getByText(/the prefilter passed 93% of the 4,224 documents it screened/i)).toBeInTheDocument();
  expect(screen.queryByText(/76%/)).not.toBeInTheDocument();
});

// ---- a run's job-board, page and fact counts ----------------------------

it("shows a full run's new and closed postings, page changes and facts in its details", async () => {
  const stats = { ...run().stats, postings_new: 7, postings_closed: 2, page_changes: 3, facts_extracted: 11 };
  server.use(http.get("/api/v1/runs", () => HttpResponse.json([run({ id: 4, kind: "daily", mode: "full", stats })])));
  renderAs("pm", "/admin/runs");
  await userEvent.click(await screen.findByRole("button", { name: /details for run 4/i }));
  const value = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling!.textContent;
  expect(value("New postings")).toBe("7");
  expect(value("Postings closed")).toBe("2");
  expect(value("Page changes filed")).toBe("3");
  expect(value("Facts extracted")).toBe("11");
});

it("shows an ingest-only run's postings, and no intelligence counts it never makes", async () => {
  const stats = { ...run().stats, postings_new: 4, postings_closed: 0 };
  server.use(http.get("/api/v1/runs", () => HttpResponse.json([run({ id: 5, mode: "ingest", stats })])));
  renderAs("pm", "/admin/runs");
  await userEvent.click(await screen.findByRole("button", { name: /details for run 5/i }));
  expect(screen.getByText("New postings", { selector: "dt" }).nextElementSibling).toHaveTextContent("4");
  expect(screen.getByText("Postings closed", { selector: "dt" }).nextElementSibling).toHaveTextContent("0");
  expect(screen.queryByText("Page changes filed", { selector: "dt" })).not.toBeInTheDocument();
  expect(screen.queryByText("Facts extracted", { selector: "dt" })).not.toBeInTheDocument();
});

it("links a full run to its agent run, from agent_run_id", async () => {
  server.use(http.get("/api/v1/runs", () => HttpResponse.json([run({ id: 6, kind: "daily", mode: "full", agent_run_id: 77 })])));
  renderAs("pm", "/admin/runs");
  await userEvent.click(await screen.findByRole("button", { name: /details for run 6/i }));
  expect(screen.getByRole("link", { name: "Its agent run" })).toHaveAttribute("href", "/admin/agents/77");
});
