import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { Schemas } from "@/lib/api/client";
import { competitorOverview, competitorSummary, dayOneOverview, dayOneReference, localDay, move } from "@/test/fixtures";
import { server } from "@/test/msw";
import { plainDay } from "@/lib/time";
import { renderAs } from "@/test/render";

/*
 * The competitor list says only what is true: the order its head names, the date
 * and source of each figure, product names that open their pages, one date format, the same text
 * for every persona, and a failed background refetch that keeps the data on screen.
 */

type CompetitorSummary = Schemas["CompetitorSummary"];

function serveOverview(rows: CompetitorSummary[]) {
  server.use(http.get("/api/v1/competitors/overview", () => HttpResponse.json(rows)));
}

const table = () => screen.getByRole("table", { name: "All competitors" });
const bodyRows = () => within(table()).getAllByRole("row").filter((row) => row.querySelector("[data-row-link]"));
const order = () => bodyRows().map((row) => row.querySelector("[data-row-link]")!.textContent);
const rowOf = (name: string) => bodyRows().find((row) => row.querySelector("[data-row-link]")!.textContent === name)!;

async function openPage(rows: CompetitorSummary[] = competitorOverview(), route = "/competitors", role: Parameters<typeof renderAs>[0] = "pm") {
  serveOverview(rows);
  const app = renderAs(role, route);
  await screen.findByRole("table", { name: "All competitors" });
  return app;
}

/** The landscape's y: the mean of the trend's recent 4 weeks (fewer on a young deployment). */
const level = (r: CompetitorSummary) => {
  const recent = r.momentum?.slice(-4) ?? [];
  return recent.length ? recent.reduce((s, w) => s + w.value, 0) / recent.length : null;
};

it("sorts Momentum by the momentum level the landscape draws, even when no trend has a baseline", async () => {
  server.use(http.get("/api/v1/intel-reference", () => HttpResponse.json(dayOneReference())));
  const rows = dayOneOverview();
  expect(rows.every((r) => r.threat.momentum_trend === null)).toBe(true); // day 1: no trend anywhere
  await openPage(rows, "/competitors?sort=momentum");
  const measured = rows.filter((r) => level(r) !== null).sort((a, b) => level(b)! - level(a)!);
  const unmeasured = rows.filter((r) => level(r) === null);
  expect(order()).toEqual([...measured, ...unmeasured].map((r) => r.name));
  expect(screen.getByText(/highest momentum first/)).toBeInTheDocument();
  // The level leads the cell; the trend is the secondary text.
  const top = rowOf(measured[0]!.name);
  expect(within(top).getByText(`${Math.round(level(measured[0]!)!)} a week`)).toBeInTheDocument();
  expect(within(top).getByText("no baseline yet")).toBeInTheDocument();
});

it("gives each live figure its as-of date and source", async () => {
  const rows = competitorOverview();
  const sonatype = rows.find((r) => r.slug === "sonatype")!;
  sonatype.funding_total = { ...sonatype.funding_total!, as_of: "2024-03-12" };
  sonatype.open_roles_as_of = `${localDay(-2)}T03:00:00Z`;
  await openPage(rows);
  const row = rowOf("Sonatype");
  // Total raised: its rounds and the newest one's month, so an old total never reads as current.
  expect(within(row).getByText("4 rounds · latest Mar 2024")).toBeInTheDocument();
  // Open roles: the board's last complete listing.
  expect(within(row).getByText(`board read ${plainDay(localDay(-2), "dayMonth")}`)).toBeInTheDocument();
  // A board read today says so.
  const gitlab = rows.find((r) => r.slug === "gitlab")!;
  expect(gitlab.open_roles).not.toBeNull();
  expect(within(rowOf("GitLab")).getByText("board read today")).toBeInTheDocument();
});

it("links each JFrog product it competes with to that product's page", async () => {
  await openPage();
  const row = rowOf("Sonatype");
  for (const [name, slug] of [
    ["Artifactory", "artifactory"],
    ["Xray", "xray"],
    ["Curation", "curation"],
  ]) {
    expect(within(row).getByRole("link", { name })).toHaveAttribute("href", `/products/${slug}`);
  }
});

it("titles the threat tooltip by what it says, and keeps one date format for lane · day", async () => {
  const rows = competitorOverview();
  const day = localDay(-1);
  rows[0]!.latest_move = move({ day, title: "A move from yesterday" });
  await openPage(rows);
  await userEvent.hover(within(rowOf(rows[0]!.name)).getByRole("button", { name: /^(high|medium|low)/i }));
  const tip = await screen.findByRole("tooltip");
  expect(tip).toHaveTextContent(/^Why this level/);
  expect(tip).not.toHaveTextContent("How this is computed");
  const dayMonth = (d: string) => plainDay(d, "dayMonth");
  const week = screen.getByRole("region", { name: "This week" });
  expect(within(week).getByText(dayMonth(day))).toBeInTheDocument();
  expect(within(rowOf(rows[0]!.name)).getByText(new RegExp(`· ${dayMonth(day)}$`))).toBeInTheDocument();
});

it("says what is true of a competitor with no board: none found, and when discovery checked", async () => {
  const rows = [
    competitorSummary({
      slug: "nojobs",
      name: "Nojobs",
      has_board: false,
      open_roles: null,
      delta_30d: null,
      board_state: "none",
      board_checked_at: "2026-10-12T04:00:00Z",
      open_roles_note: "No public Greenhouse, Lever or Ashby board found (checked 12 Oct 2026)",
    }),
  ];
  await openPage(rows);
  expect(within(rowOf("Nojobs")).getByText("No public Greenhouse, Lever or Ashby board found")).toBeInTheDocument();
  expect(within(rowOf("Nojobs")).getByText("checked 12 Oct 2026")).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent(/every day/);
});

it.each(["analyst", "sales", "exec", "pmm_devrel"] as const)("reads the same for %s as for a PM", async (role) => {
  const pm = await openPage();
  const text = (await screen.findByRole("main")).textContent;
  pm.unmount();
  await openPage(competitorOverview(), "/competitors", role);
  await waitFor(() => expect(screen.getByRole("main").textContent).toBe(text));
});

it("keeps the list on screen when a background refetch fails", async () => {
  const { queryClient } = await openPage();
  server.use(
    http.get("/api/v1/competitors/overview", () => HttpResponse.json({ detail: "down" }, { status: 503 })),
    http.get("/api/v1/intel-reference", () => HttpResponse.json({ detail: "down" }, { status: 503 })),
  );
  await queryClient.refetchQueries();
  await waitFor(() => expect(queryClient.getQueryState(["competitor-overview"])?.status).toBe("error"));
  expect(table()).toBeInTheDocument();
  expect(screen.queryByText("The competitors didn't load.")).not.toBeInTheDocument();
});
