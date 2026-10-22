import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { competitorProfile, sparseProfile } from "@/test/fixtures";
import { server } from "@/test/msw";
import { menu, openProfile, section, serveProfile } from "@/test/profile";
import { renderAs, selectOption } from "@/test/render";
import { SECTIONS } from "./useCompetitorProfile";

/* The competitor deep dive's section menu and the page itself: one view for every persona, a phone, a 404, a retry. */

describe("the section menu", () => {
  it("highlights the section in view as the page scrolls", async () => {
    await openProfile();
    const tops: Record<string, number> = { overview: -2400, products: -2000, momentum: -1600, hiring: -1200, headcount: -800, money: 60, market: 700 };
    for (const s of SECTIONS) {
      const el = document.getElementById(s.id)!;
      el.getBoundingClientRect = () => ({ top: tops[s.id] ?? 1400, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) });
    }
    act(() => {
      fireEvent.scroll(window);
    });
    await waitFor(() => expect(within(menu()).getByRole("link", { name: "Money" })).toHaveAttribute("aria-current", "location"));
    expect(within(menu()).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
    // The phone's select names the same section.
    expect(screen.getByRole("combobox", { name: "Jump to section" })).toHaveTextContent("Money");
  });

  it("jumps to a section, marks it current and keeps it in the address", async () => {
    const app = await openProfile();
    const scrolled: Element[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    };
    try {
      await userEvent.click(within(menu()).getByRole("link", { name: "Pricing" }));
      expect(scrolled.at(-1)).toBe(document.getElementById("pricing"));
      expect(within(menu()).getByRole("link", { name: "Pricing" })).toHaveAttribute("aria-current", "location");
      expect(app.location().hash).toBe("#pricing");
      expect(within(section("Pricing")).getByRole("heading", { level: 2 })).toHaveFocus();
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });

  it("lets the highlight follow the reader again as soon as they scroll by hand after a jump", async () => {
    await openProfile();
    const scroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = () => {};
    try {
      await userEvent.click(within(menu()).getByRole("link", { name: "Pricing" }));
      expect(within(menu()).getByRole("link", { name: "Pricing" })).toHaveAttribute("aria-current", "location");
      // Right after the jump, the reader wheels back up to Money: the jump's hold gives way at once.
      const tops: Record<string, number> = { overview: -2400, products: -2000, momentum: -1600, hiring: -1200, headcount: -800, money: 60 };
      for (const s of SECTIONS) {
        document.getElementById(s.id)!.getBoundingClientRect = () => ({ top: tops[s.id] ?? 900, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) });
      }
      act(() => {
        fireEvent.wheel(window);
        fireEvent.scroll(window);
      });
      await waitFor(() => expect(within(menu()).getByRole("link", { name: "Money" })).toHaveAttribute("aria-current", "location"));
    } finally {
      Element.prototype.scrollIntoView = scroll;
    }
  });

  it("marks the sections that have no data yet", async () => {
    await openProfile(sparseProfile());
    expect(within(menu()).getByRole("link", { name: "Pricing, no data yet" })).toBeInTheDocument();
    expect(within(menu()).getByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(menu()).toHaveTextContent("3 of 13 sections have data");
  });
});

describe("the page", () => {
  it("is the same for every persona", async () => {
    const first = await openProfile(competitorProfile(), "analyst");
    const settled = () => waitFor(() => expect(within(section("Market")).getByRole("figure", { name: "Share of voice" })).toHaveTextContent("GitHub"));
    await settled();
    const analyst = screen.getByRole("article", { name: "Sonatype" }).textContent;
    first.unmount();
    await openProfile(competitorProfile(), "sales");
    await settled();
    expect(screen.getByRole("article", { name: "Sonatype" }).textContent).toBe(analyst);
  });

  it("keeps long content inside a 390px phone (structural)", async () => {
    const long = "x".repeat(240);
    const profile = competitorProfile();
    profile.products[0]!.product = `Nexus${long}`;
    profile.hiring.recent[0]!.title = `Engineer${long}`;
    profile.pricing[0]!.diff = `@@ -1 +1 @@\n-${long}\n+${long}y`;
    await openProfile(profile);
    const article = screen.getByRole("article", { name: "Sonatype" });
    // Unbroken tokens wrap anywhere, as on the analysis page; nothing sets a fixed width.
    expect(article.className).toMatch(/wrap-anywhere/);
    expect(article.className).toMatch(/min-w-0/);
    // Below `xl` the rail is a select, and the products table reflows into blocks below `md`.
    expect(menu().closest("[data-rail]")!.className).toMatch(/max-xl:hidden/);
    expect(screen.getByRole("combobox", { name: "Jump to section" }).closest("[data-jump]")!.className).toMatch(/xl:hidden/);
    const table = screen.getByRole("table", { name: "Products vs JFrog" });
    expect(table.className).toMatch(/max-md:block/);
    // Diff lines wrap rather than scroll the page sideways.
    for (const line of screen.getAllByText(new RegExp(long)).filter((el) => el.hasAttribute("data-diff"))) expect(line.className).toMatch(/whitespace-pre-wrap/);
    for (const figure of screen.getAllByRole("figure")) expect(figure.className).toMatch(/min-w-0/);
  });

  it("says a competitor that isn't tracked (or JFrog itself) has no profile", async () => {
    renderAs("pm", "/competitors/jfrog");
    expect(await screen.findByText("This competitor isn't tracked")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "All competitors" }).length).toBeGreaterThan(0);
  });

  it("offers a retry when the profile fails to load", async () => {
    server.use(http.get("/api/v1/competitors/:slug/profile", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderAs("pm", "/competitors/sonatype");
    expect(await screen.findByText("The profile didn't load.")).toBeInTheDocument();
    serveProfile(competitorProfile());
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Sonatype" })).toBeInTheDocument();
  });
});

describe("the jump select below xl", () => {
  it("moves focus to the chosen section's head, so Tab continues inside it", async () => {
    await openProfile();
    await selectOption("Jump to section", "Pricing");
    await waitFor(() => expect(within(section("Pricing")).getByRole("heading", { level: 2 })).toHaveFocus());
  });
});
