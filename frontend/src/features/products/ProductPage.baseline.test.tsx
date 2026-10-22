import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { Schemas } from "@/lib/api/client";
import { productProfile, productSummaries, quietProduct } from "@/test/fixtures";
import { server } from "@/test/msw";
import { renderAs } from "@/test/render";

/*
 * The product pages say why there is no baseline: on a deployment that has
 * collected all 13 weeks, a product whose 9 baseline weeks name no threat has no baseline yet, as
 * a competitor's momentum would, and the page says that, never "rising" and never "FrogEye has
 * collected since…". The formula keeps its two "no baseline" cases apart. Both pages read the
 * same for every persona, and say what is true of job boards.
 */

type ProductProfile = Schemas["ProductProfile"];

function serveProduct(profile: ProductProfile) {
  server.use(http.get("/api/v1/products/:slug", ({ params }) => (params.slug === profile.slug ? HttpResponse.json(profile) : HttpResponse.json({ detail: "Product not found" }, { status: 404 }))));
}

async function openProduct(profile: ProductProfile = productProfile(), role: Parameters<typeof renderAs>[0] = "pm") {
  serveProduct(profile);
  const app = renderAs(role, `/products/${profile.slug}`);
  await screen.findByText(profile.name, { selector: "h1" });
  return app;
}

const section = (name: string) => [...document.querySelectorAll("h2")].find((h) => h.textContent === name)!.closest("section")!;

/** Artifactory with threat only in the last 4 of 13 collected weeks: no baseline to compare with. */
function emptyBaseline(): ProductProfile {
  const p = productProfile();
  const weeks = p.pressure_series.map((w, i, all) => ({ ...w, threat: i < all.length - 4 ? 0 : w.threat || 3 }));
  return { ...p, pressure: "rising", pressure_baseline: false, collected_since: null, pressure_series: weeks };
}

describe("an empty baseline window on a fully collected deployment", () => {
  it("reads 'no baseline yet' with its real reason, never rising and never a young deployment", async () => {
    await openProduct(emptyBaseline());
    const header = screen.getByText("Artifactory", { selector: "h1" }).closest("header")!;
    expect(within(header).getByText("Pressure, no baseline yet")).toHaveAttribute("data-pressure", "baseline");
    expect(header).toHaveTextContent("no threat in the 9 weeks before the last 4");
    expect(header).not.toHaveTextContent(/collected since|Rising pressure/);
    const figures = within(section("Bottom line")).getByRole("list", { name: "Key figures" });
    expect(figures).toHaveTextContent(/threat over the last 4 weeks; none in the 9 before, so no baseline yet/);
    expect(figures).not.toHaveTextContent(/since/);
    const chart = within(section("Pressure over time")).getByRole("figure", { name: "Weekly pressure" });
    expect(chart).toHaveTextContent(/no threat in the 9 weeks before the last 4, so no baseline yet to call it rising or easing/);
    expect(section("Pressure over time")).toHaveTextContent(/13 weeks to/);
  });

  it("keeps the formula's two 'no baseline' cases apart", async () => {
    await openProduct();
    await userEvent.click(within(section("Pressure over time")).getByText("How this is computed"));
    const text = section("Pressure over time").textContent ?? "";
    expect(text).toContain("With no threat in the 9 weeks before the last 4 there is no baseline yet: the page says so instead of a word.");
    expect(text).toContain("Until all 13 weeks have been collected there is no baseline either.");
    expect(text).not.toContain("or when threat appears with no baseline yet");
  });

  it("counts such a product apart in the list's lead, without a collected-since day", async () => {
    const rows = productSummaries().map((r) => (r.slug === "artifactory" ? { ...r, pressure_baseline: false } : r));
    server.use(http.get("/api/v1/products", () => HttpResponse.json(rows)));
    renderAs("pm", "/products");
    await screen.findByRole("list", { name: "JFrog products" });
    const lead = screen.getByText(/^13 JFrog products\./);
    expect(lead).toHaveTextContent("1 faces pressure with no baseline yet");
    expect(lead).not.toHaveTextContent(/since/);
    expect(screen.getByText(/^Pressure, 13 weeks$/)).toBeInTheDocument();
  });
});

describe("the same for every persona", () => {
  it.each(["analyst", "sales", "exec", "pmm_devrel"] as const)("a product's deep dive reads the same for %s as for a PM", async (role) => {
    const pm = await openProduct(productProfile(), "pm");
    const text = document.querySelector("article")!.textContent;
    pm.unmount();
    await openProduct(productProfile(), role);
    expect(document.querySelector("article")!.textContent).toBe(text);
  });

  it.each(["analyst", "sales", "exec", "pmm_devrel"] as const)("the products list reads the same for %s as for a PM", async (role) => {
    server.use(http.get("/api/v1/products", () => HttpResponse.json(productSummaries())));
    const pm = renderAs("pm", "/products");
    await screen.findByRole("list", { name: "JFrog products" });
    const text = screen.getByRole("main").textContent;
    pm.unmount();
    renderAs(role, "/products");
    await screen.findByRole("list", { name: "JFrog products" });
    await waitFor(() => expect(screen.getByRole("main").textContent).toBe(text));
  });
});

it("says what is true of competitor job boards when none has been read", async () => {
  const p = quietProduct();
  p.hiring_in_area = { capabilities: ["edge-distribution"], boards: [], total: [], by_competitor: [] };
  await openProduct(p);
  const hiring = section("Competitor hiring in this area");
  expect(within(hiring).getByText("No competitor job board has been read yet")).toBeInTheDocument();
  expect(hiring).toHaveTextContent(/job-board discovery/);
  expect(document.querySelector("article")).not.toHaveTextContent(/every day/);
});
