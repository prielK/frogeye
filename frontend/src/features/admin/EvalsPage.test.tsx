import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { Schemas } from "@/lib/api/client";
import { evalResults } from "@/test/evals";
import { server } from "@/test/msw";
import { renderAs } from "@/test/render";
import { formatMetric, splitGroups } from "./useEvals";

function serveEvals(results: Schemas["EvalsOut"]) {
  server.use(http.get("/api/v1/evals", () => HttpResponse.json(results)));
}

const suite = (title: string) => screen.getByRole("region", { name: title });

it("shows the run, then each suite with its headline numbers, breakdowns and failures", async () => {
  serveEvals(evalResults());
  renderAs("sales", "/admin/evals");
  expect(await screen.findByRole("heading", { level: 1, name: "Evals" })).toBeInTheDocument();
  expect(await screen.findByText(/measured on 163 labelled cases in 5 suites/)).toHaveTextContent("the app got 2 wrong, each listed under its suite. 1 case was not recorded and left out of the metrics.");

  // The run's facts: mode, models, cost.
  expect(screen.getByText("Recorded")).toBeInTheDocument();
  expect(screen.getByText("Models").nextElementSibling).toHaveTextContent(/^gpt-5-nano · gpt-6-lunafast · smart; embeddings text-embedding-3-small; judge gpt-6-luna$/);
  expect(screen.getByText(/447 calls replayed, \$0\.030 when recorded/)).toBeInTheDocument();

  // Suites in the harness's order, each with what it measures and who labelled it.
  expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
    "Triage",
    "Competitor disambiguation",
    "Retrieval",
    "Deterministic guardrails",
    "Stage-B fact-checker",
  ]);
  const triage = suite("Triage");
  expect(triage).toHaveTextContent("Labels: labelled blind from each document's raw text, never from FrogEye's outputs");
  // A rate with its count, a score without.
  expect(within(triage).getByText("Precision").parentElement).toHaveTextContent("Precision64.4%29/45");
  expect(within(triage).getByText("F1").parentElement).toHaveTextContent("F10.773");

  // Groups that share their measures are bars, one measure at a time.
  const chart = within(triage).getByRole("figure", { name: "Accuracy by group" });
  expect(within(chart).getByRole("img", { name: /^Hard negative: 40\.9% of the group's cases \(9\/22\)$/ })).toBeInTheDocument();
  await userEvent.click(within(triage).getByRole("button", { name: "Accuracy, keyword screen first" }));
  expect(within(triage).getByRole("figure", { name: "Accuracy, keyword screen first by group" })).toBeInTheDocument();

  // Failures, one click away, with what was expected and what came back.
  await userEvent.click(within(triage).getByText("2 failures · each case it got wrong"));
  const failures = within(triage).getByRole("list", { name: "Triage: failures" });
  expect(within(failures).getAllByRole("listitem")).toHaveLength(2);
  expect(failures).toHaveTextContent(/triage-02.*Expectedrelevant \[github\].*Gotrelevant \[\]/);
});

it("draws the retrieval modes side by side on their scores, and lists the sub-breakdowns as a table", async () => {
  serveEvals(evalResults());
  renderAs("pm", "/admin/evals");
  const retrieval = await screen.findByRole("region", { name: "Retrieval" });
  const chart = within(retrieval).getByRole("figure", { name: "Recall@8 by mode" });
  expect(chart).toHaveTextContent("Hybrid 0.967, Keyword only 0.618.");
  expect(within(chart).getByRole("img", { name: /^Hybrid \+ rerank: 0\.962$/ })).toBeInTheDocument();
  await userEvent.click(within(retrieval).getByRole("button", { name: "MRR@10" }));
  expect(within(retrieval).getByRole("figure", { name: "MRR@10 by mode" })).toHaveTextContent("Hybrid 1.000, Keyword only 0.760.");
  expect(within(retrieval).getByText("Hybrid, by kind")).toBeInTheDocument();
  const more = within(retrieval).getByRole("table", { name: "Retrieval: by group" });
  expect(within(more).getByRole("rowheader", { name: "Semantic queries" })).toBeInTheDocument();
  expect(within(retrieval).getByText("No failures: every scored case matched its label.")).toBeInTheDocument();
});

it("lists unlike breakdowns as a table, names unscored cases and notes the fact-checker's experiment", async () => {
  serveEvals(evalResults());
  renderAs("exec", "/admin/evals");
  const guardrails = await screen.findByRole("region", { name: "Deterministic guardrails" });
  const table = within(guardrails).getByRole("table", { name: "Deterministic guardrails: by group" });
  const genuine = within(table).getByRole("rowheader", { name: "Quote genuine" }).closest("tr")!;
  // the pass and catch rates share one column, each named in its cell
  expect(genuine).toHaveTextContent(/^Quote genuine10Pass rate100%10\/10$/);
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Group", "Cases", "Result"]);

  expect(suite("Competitor disambiguation")).toHaveTextContent("27 of 28 cases scored");
  expect(suite("Competitor disambiguation")).toHaveTextContent("1 case not recorded: left out of these numbers until their model calls are recorded (disambig-09).");
  expect(suite("Stage-B fact-checker")).toHaveTextContent("These figures follow an experiment. Three checker variants were run on these 30 claims");
  expect(suite("Triage")).not.toHaveTextContent("These figures follow an experiment");
});

it("says when no results have been written yet, and offers a retry when they do not load", async () => {
  renderAs("analyst", "/admin/evals");
  expect(await screen.findByText("No evaluation results yet")).toBeInTheDocument();

  server.use(http.get("/api/v1/evals", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  renderAs("analyst", "/admin/evals");
  expect(await screen.findByText("The evaluation results didn't load.")).toBeInTheDocument();
});

it("reads a replay with every case scored", async () => {
  serveEvals(evalResults({ mode: "replay", stale_cases: 0, suites: evalResults().suites.filter((s) => s.stale === 0) }));
  renderAs("pm", "/admin/evals");
  expect(await screen.findByText("Replayed")).toBeInTheDocument();
  expect(screen.getByText("every case scored")).toBeInTheDocument();
  expect(screen.getByText(/measured on/)).not.toHaveTextContent("not recorded");
});

it("formats rates with their counts and scores to three places, and charts only like groups", () => {
  expect(formatMetric({ value: 0.6444, numerator: 29, denominator: 45 })).toBe("64.4%");
  expect(formatMetric({ value: 1, numerator: 37, denominator: 37 })).toBe("100%");
  expect(formatMetric({ value: 0.7733, numerator: null, denominator: null })).toBe("0.773");
  expect(formatMetric({ value: null, numerator: 0, denominator: 0 })).toBe("Not computed");
  const [, disambiguation, retrieval, guardrails] = evalResults().suites;
  expect(splitGroups(retrieval!.groups).chart.map((g) => g.name)).toEqual(["keyword", "vector", "hybrid", "hybrid_rerank"]);
  expect(splitGroups(disambiguation!.groups).chart).toEqual([]);
  expect(splitGroups(guardrails!.groups).chart).toEqual([]);
});

it("is in the Admin navigation, after Agent runs", async () => {
  serveEvals(evalResults());
  renderAs("sales", "/admin/evals");
  const nav = (await screen.findAllByRole("navigation", { name: "Main" }))[0]!;
  const admin = within(nav).getByRole("list", { name: "Admin" });
  expect(within(admin).getAllByRole("link").map((a) => a.textContent)).toEqual(["Sources", "Runs", "Agent runs", "Evals"]);
});
