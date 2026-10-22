import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { Schemas } from "@/lib/api/client";
import { compareMatrix, COMPARE_COLUMNS, emptyMatrix, quietMatrix } from "@/test/compare";
import { JFROG_PRODUCT_NAMES } from "@/test/fixtures";
import { server } from "@/test/msw";
import { renderAs, selectOption } from "@/test/render";

function serveMatrix(matrix: Schemas["CompareOut"]) {
  server.use(http.get("/api/v1/compare", () => HttpResponse.json(matrix)));
}

async function openPage(matrix = compareMatrix(), route = "/compare") {
  serveMatrix(matrix);
  const app = renderAs("sales", route);
  await screen.findByRole("table", { name: "JFrog vs competitors" });
  return app;
}

const matrix = () => screen.getByRole("table", { name: "JFrog vs competitors" });
const columns = () =>
  within(matrix())
    .getAllByRole("columnheader")
    .slice(1)
    .map((th) => within(th).getByRole("link").textContent);
const rowHeads = () => within(matrix()).getAllByRole("rowheader").map((th) => within(th).getByRole("link").textContent);
/** The matrix cell of a pairing (its button), by its key. */
const cell = (key: string) => matrix().querySelector<HTMLButtonElement>(`button[data-cell="${key}"]`)!;

it("lays JFrog's products against every competitor with seeded products, each pairing with its threat and products", async () => {
  await openPage();
  expect(screen.getByRole("heading", { level: 1, name: "Compare" })).toBeInTheDocument();
  // The lead counts the pairings by their threat.
  expect(screen.getByText(/JFrog's 13 products against 4 competitors' products/)).toHaveTextContent("3 pairings carry a threat on recent evidence: 1 high, 1 medium and 1 low; 3 name no threat.");

  // Rows in the products list's order, each with its pressure; columns as the API orders them.
  expect(rowHeads()).toEqual(JFROG_PRODUCT_NAMES);
  expect(columns()).toEqual(COMPARE_COLUMNS);
  const artifactory = within(matrix()).getAllByRole("rowheader")[0]!;
  expect(within(artifactory).getByRole("link", { name: "Artifactory" })).toHaveAttribute("href", "/products/artifactory");
  expect(within(artifactory).getByText("Rising pressure")).toBeInTheDocument();
  const sonatype = within(matrix()).getAllByRole("columnheader")[1]!;
  expect(within(sonatype).getByRole("link", { name: "Sonatype" })).toHaveAttribute("href", "/competitors/sonatype");
  expect(within(sonatype).getByText("Tier 1 · Competitor")).toBeInTheDocument();
  // the column's overall threat, its drivers one hover away
  expect(within(sonatype).getByRole("button", { name: /^high/i })).toBeInTheDocument();

  // A high threat, its products and the day of the latest move.
  expect(cell("artifactory:sonatype")).toHaveTextContent(/^High.*Nexus Repository$/);
  expect(cell("artifactory:sonatype").querySelector("time")).not.toBeNull();
  // Evidence that names no threat, a seeded product with none, a pairing named only by signals.
  expect(cell("xray:sonatype")).toHaveTextContent("No threat named");
  expect(cell("xray:sonatype")).toHaveTextContent("Sonatype Lifecycle, Sonatype SBOM Manager");
  expect(cell("curation:socket")).toHaveTextContent("No recent evidence");
  expect(cell("xray:cloudsmith")).toHaveTextContent(/Medium.*No product of theirs mapped/);
  // No product mapped: a blank cell, not a control.
  expect(cell("connect:sonatype")).toBeNull();
  expect(within(matrix()).getAllByText("No product of theirs mapped").length).toBeGreaterThan(30);

  // How it is computed, in the API's numbers.
  await userEvent.click(screen.getByText("How this is computed"));
  expect(screen.getByText(/A cell is a JFrog product against one competitor/)).toHaveTextContent("read as high from 6, medium from 3, low from 1");
});

it("opens a pairing in a sheet: their products, the threat with each signal it is summed from, and both deep dives", async () => {
  const app = await openPage();
  await userEvent.click(cell("artifactory:sonatype"));
  const sheet = await screen.findByRole("dialog", { name: "Artifactory vs Sonatype" });
  expect(app.location().search).toBe("?cell=artifactory%3Asonatype");

  expect(within(sheet).getByText("High threat")).toBeInTheDocument();
  expect(within(sheet).getByText(/threat weight 7/)).toBeInTheDocument();
  expect(within(sheet).getByRole("link", { name: /Nexus Repository/ })).toHaveAttribute("href", "https://www.sonatype.com/products/sonatype-nexus-repository");
  const evidence = within(sheet).getByRole("region", { name: /Threat to Artifactory/ });
  expect(within(evidence).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/signals/7001", "/signals/7002", "/signals/7003"]);
  expect(evidence).toHaveTextContent(/Pricing.*Medium severity.*adds 2/);
  expect(within(sheet).getByRole("link", { name: "Sonatype's profile →" })).toHaveAttribute("href", "/competitors/sonatype");
  expect(within(sheet).getByRole("link", { name: "Artifactory deep dive →" })).toHaveAttribute("href", "/products/artifactory");

  // Back closes it (opening pushed an entry).
  await userEvent.click(within(sheet).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(app.location().search).toBe("");
});

it("says plainly when a pairing has no product mapped, or no threat named", async () => {
  // a shared link opens the pairing over the matrix
  serveMatrix(compareMatrix());
  renderAs("sales", "/compare?cell=xray:cloudsmith");
  const sheet = await screen.findByRole("dialog", { name: "Xray vs Cloudsmith" });
  expect(sheet).toHaveTextContent("No product of Cloudsmith's is mapped to Xray.");
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

  await userEvent.click(cell("xray:sonatype"));
  const quiet = await screen.findByRole("dialog", { name: "Xray vs Sonatype" });
  expect(within(quiet).getByText("No threat named")).toBeInTheDocument();
  expect(quiet).toHaveTextContent("Its public signals name Xray (evidence 2), but none as a threat in the last 180 days.");
  // its latest move still links to its analysis
  expect(within(quiet).getByRole("link", { name: "Sonatype partners with an SBOM consortium" })).toHaveAttribute("href", "/signals/7020");
});

it("narrows the competitors by tier and category, in the address", async () => {
  const app = await openPage();
  await selectOption("Tier", "Tier 2");
  expect(columns()).toEqual(["Cloudsmith", "Socket"]);
  expect(app.location().search).toBe("?tier=2");
  expect(screen.getByRole("heading", { name: "JFrog vs competitors" }).parentElement).toHaveTextContent("2 of 4 competitors");

  await selectOption("Category", "Supply chain security");
  expect(columns()).toEqual(["Cloudsmith", "Socket"]);
  await selectOption("Tier", "Tier 1");
  expect(await screen.findByText("No competitor matches these filters")).toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: "Clear filters" })[0]!);
  expect(columns()).toEqual(COMPARE_COLUMNS);
  expect(app.location().search).toBe("");
});

it("drops a filter the data cannot match from the address", async () => {
  const app = await openPage(compareMatrix(), "/compare?tier=7&category=nothing");
  await waitFor(() => expect(app.location().search).toBe(""));
  expect(columns()).toEqual(COMPARE_COLUMNS);
});

it("on a phone lists one product's competitors, each opening the same sheet", async () => {
  await openPage();
  const list = screen.getByRole("list", { name: "Competitors of Artifactory" });
  expect(within(list).getAllByRole("button").map((b) => b.getAttribute("data-cell"))).toEqual(["artifactory:sonatype", "artifactory:github", "artifactory:cloudsmith"]);
  expect(screen.getByText("No product mapped to Artifactory: Socket.")).toBeInTheDocument();
  await selectOption("JFrog product", "Xray");
  const xray = screen.getByRole("list", { name: "Competitors of Xray" });
  await userEvent.click(within(xray).getAllByRole("button")[0]!);
  expect(await screen.findByRole("dialog", { name: "Xray vs Sonatype" })).toBeInTheDocument();
});

it("before any evidence, every seeded pairing reads no recent evidence", async () => {
  await openPage(quietMatrix());
  expect(screen.getByText(/JFrog's 13 products/)).toHaveTextContent("None of the 5 pairings names a threat on recent evidence yet.");
  expect(cell("artifactory:sonatype")).toHaveTextContent("No recent evidence");
  expect(cell("xray:cloudsmith")).toBeNull();
});

it("says so when no competitor product is mapped, and when the read fails", async () => {
  serveMatrix(emptyMatrix());
  renderAs("pm", "/compare");
  expect(await screen.findByText("No competitor product is mapped yet")).toBeInTheDocument();
});

it("offers a retry when the comparison does not load", async () => {
  server.use(http.get("/api/v1/compare", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  renderAs("exec", "/compare");
  expect(await screen.findByText("The comparison didn't load.")).toBeInTheDocument();
  serveMatrix(compareMatrix());
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("table", { name: "JFrog vs competitors" })).toBeInTheDocument();
});

it("is in the main navigation, next to Products", async () => {
  await openPage();
  const nav = screen.getAllByRole("navigation", { name: "Main" })[0]!;
  const labels = within(nav).getAllByRole("link").map((a) => a.textContent);
  expect(labels.slice(labels.indexOf("Competitors"), labels.indexOf("Competitors") + 3)).toEqual(["Competitors", "Products", "Compare"]);
  expect(within(nav).getByRole("link", { name: "Compare" })).toHaveAttribute("aria-current", "page");
});
