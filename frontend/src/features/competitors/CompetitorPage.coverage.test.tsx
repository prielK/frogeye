import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { competitorProfile, competitors, localDay, posting, sparseProfile } from "@/test/fixtures";
import { server } from "@/test/msw";
import { openProfile, section, serveProfile } from "@/test/profile";
import { renderAs } from "@/test/render";
import { postingMeta } from "./profileWords";

/*
 * The deep dive says what is true and only what it holds: a board's real state
 * (none found and when discovery checked, a board no longer readable), which open roles are
 * tagged, a tag that opens its postings, a sparse page read as quiet lines rather than a wall of
 * dashed boxes, and the competitor's product chips in its header.
 */

const STALE = "Job board not readable since 15 Oct 2026";

/** Sonatype's board, last listed in full on 15 Oct and failing since. */
function staleProfile() {
  const p = competitorProfile();
  const last = "2026-10-15";
  const total = p.hiring.series.total.filter((pt) => pt.day <= last).map((pt) => ({ ...pt, count: 131 }));
  p.kpis = { ...p.kpis, open_roles: null, delta_30d: null };
  p.hiring = {
    ...p.hiring,
    open_now: null,
    delta_30d: null,
    as_of: null,
    not_readable_since: last,
    by_department: [],
    by_seniority: [],
    by_location: [],
    tags: [],
    tag_coverage: null,
    recent: [],
    series: { total, department: [], seniority: [], location: [], tag: [] },
  };
  p.sources_freshness.notes = [...p.sources_freshness.notes.filter((n) => n.section !== "hiring"), { section: "hiring", text: STALE }];
  return p;
}

describe("job boards", () => {
  it("says no board was found and when discovery checked, never that FrogEye looks every day", async () => {
    const profile = sparseProfile();
    profile.hiring.board_checked_at = "2026-10-12T04:00:00Z";
    profile.sources_freshness.notes = profile.sources_freshness.notes.map((n) =>
      n.section === "hiring" ? { ...n, text: "No public Greenhouse, Lever or Ashby board found (checked 12 Oct 2026)" } : n,
    );
    await openProfile(profile);
    const hiring = section("People & hiring");
    expect(within(hiring).getByText(/No public Greenhouse, Lever or Ashby board found \(checked 12 Oct 2026\)/)).toBeInTheDocument();
    expect(within(hiring).getByText(/next time job-board discovery runs/)).toBeInTheDocument();
    // the job-board copy only (a Hacker News search does run every day)
    expect(document.querySelector("article")).not.toHaveTextContent(/(looks|checks) (for a public board|for one|Greenhouse)[^.]*every day/);
  });

  it("serves a stale board's history to its last listing and no current count", async () => {
    await openProfile(staleProfile());
    const hiring = section("People & hiring");
    expect(within(hiring).getByText(new RegExp(`^${STALE}`))).toBeInTheDocument();
    const chart = within(hiring).getByRole("figure", { name: "Open roles" });
    expect(chart).toHaveTextContent(/last complete listing, on 15 Oct, had 131 open roles/);
    expect(within(hiring).queryByRole("figure", { name: /Open roles by/ })).not.toBeInTheDocument();
    expect(hiring).not.toHaveTextContent(/Newest open roles/);
    const figures = within(section("Overview")).getByRole("list", { name: "Key figures" });
    expect(within(figures).getByText(STALE)).toBeInTheDocument();
    expect(within(figures).getByText("Last listed in full on 15 Oct")).toBeInTheDocument();
  });

  it("says how many open roles have been read for product areas, and each posting's state", async () => {
    const profile = competitorProfile();
    profile.hiring.recent = [
      posting({ id: 1, title: "Pending role", tags: [], tag_state: "pending" }),
      posting({ id: 2, title: "Given-up role", tags: [], tag_state: "gave_up" }),
      posting({ id: 3, title: "Untagged role", tags: [], tag_state: "untagged" }),
    ];
    await openProfile(profile);
    const hiring = section("People & hiring");
    expect(within(hiring).getByRole("figure", { name: "Capabilities in its open roles" })).toHaveTextContent("Tags read on 128 of 142 open roles so far; 14 not tagged yet.");
    const rows = within(hiring).getByRole("list", { name: "Newest open roles" });
    expect(within(rows).getByText("Not tagged yet")).toBeInTheDocument();
    expect(within(rows).getByText("Tagging failed")).toBeInTheDocument();
    expect(within(rows).getByText("No product area named")).toBeInTheDocument();
  });
});

describe("a tag opens its postings", () => {
  it("filters the open roles by a capability or a department, in the address, and clears", async () => {
    const asked: string[] = [];
    server.use(
      http.get("/api/v1/competitors/:slug/postings", ({ request }) => {
        const url = new URL(request.url);
        asked.push(url.search);
        const tag = url.searchParams.get("tag");
        const department = url.searchParams.get("department");
        const items = tag
          ? [posting({ id: 91, title: "Model security engineer", tags: [tag] })]
          : department
            ? [posting({ id: 92, title: "Security researcher", department: department as "Security" })]
            : [];
        return HttpResponse.json({ items, next_cursor: null });
      }),
    );
    const app = await openProfile();
    const hiring = section("People & hiring");
    const tags = within(hiring).getByRole("figure", { name: "Capabilities in its open roles" });
    await userEvent.click(within(tags).getByRole("link", { name: /^Model scanning: 12 open roles/ }));

    await waitFor(() => expect(app.location().search).toBe("?tag=model-scanning"));
    expect(await within(hiring).findByText("Model security engineer")).toBeInTheDocument();
    expect(within(hiring).getByRole("heading", { name: "Open roles tagged Model scanning" })).toBeInTheDocument();
    // the bar it opened is marked as the one the list is filtered to
    expect(within(tags).getByRole("link", { name: /^Model scanning: 12 open roles/ })).toHaveAttribute("aria-current", "true");
    expect(asked.some((q) => q.includes("tag=model-scanning") && q.includes("status=open"))).toBe(true);

    await userEvent.click(within(hiring).getByRole("button", { name: "Show all open roles" }));
    await waitFor(() => expect(app.location().search).toBe(""));
    expect(within(hiring).getByRole("list", { name: "Newest open roles" })).toBeInTheDocument();

    const departments = within(hiring).getByRole("figure", { name: "Open roles by department" });
    await userEvent.click(within(departments).getByRole("link", { name: /^Security: / }));
    await waitFor(() => expect(app.location().search).toBe("?department=Security"));
    expect(await within(hiring).findByText("Security researcher")).toBeInTheDocument();
    expect(within(hiring).getByRole("heading", { name: "Open roles in Security" })).toBeInTheDocument();
  });

  it("opens filtered from a shared link", async () => {
    server.use(http.get("/api/v1/competitors/:slug/postings", () => HttpResponse.json({ items: [posting({ id: 93, title: "Shared role" })], next_cursor: null })));
    serveProfile(competitorProfile());
    renderAs("pm", "/competitors/sonatype?tag=model-scanning#hiring");
    await screen.findByText("Sonatype", { selector: "h1" });
    expect(await within(section("People & hiring")).findByText("Shared role")).toBeInTheDocument();
    expect(within(section("People & hiring")).getByRole("heading", { name: "Open roles tagged Model scanning" })).toBeInTheDocument();
  });
});

describe("a sparse deep dive", () => {
  it("reads each section with nothing of its own as one quiet line, and lists only the source notes under Sources", async () => {
    await openProfile(sparseProfile());
    for (const name of ["Products vs JFrog", "Momentum", "People & hiring", "Headcount", "Money", "Customers", "Pricing", "Community", "People & partners", "Latest signals"]) {
      const el = section(name);
      expect(el.querySelector("[data-quiet-note]"), name).not.toBeNull();
      expect(el.querySelector(".border-dashed"), name).toBeNull();
    }
    const sources = section("Sources");
    expect(within(sources).queryByRole("list", { name: "Coverage notes" })).not.toBeInTheDocument();
    expect(within(sources).getByText(/No news sources of its own/)).toBeInTheDocument();
  });

  it("keeps a dashed frame for an empty chart inside a section that has data", async () => {
    const profile = competitorProfile();
    profile.community.mentions = profile.community.mentions.map((m) => ({ ...m, count: 0 }));
    await openProfile(profile);
    const hn = within(section("Community")).getByRole("figure", { name: "Hacker News mentions" });
    expect(hn.querySelector(".border-dashed")).not.toBeNull();
  });

  it("says one week in the singular", async () => {
    const profile = competitorProfile();
    profile.community.mentions = [{ week_start: localDay(-6), count: 0 }];
    await openProfile(profile);
    expect(within(section("Community")).getByText(/in 1 week\b/)).toBeInTheDocument();
    expect(section("Community")).not.toHaveTextContent(/1 weeks/);
  });
});

describe("the header and the menu", () => {
  it("shows the competitor's products as chips and no logo", async () => {
    const profile = competitorProfile();
    await openProfile(profile);
    const chips = screen.getByRole("list", { name: "Its products" });
    expect(within(chips).getAllByRole("listitem").map((li) => li.textContent)).toEqual(profile.products.map((p) => p.product));
    expect(document.querySelector("header img")).toBeNull();
  });

  it("marks the sections without data in the phone's jump select", async () => {
    await openProfile(sparseProfile());
    await userEvent.click(screen.getByRole("combobox", { name: "Jump to section" }));
    expect(await screen.findByRole("option", { name: "Pricing, no data yet" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Overview" })).toBeInTheDocument();
  });
});

describe("one rule, one look", () => {
  it("links every JFrog product in Products vs JFrog to its page", async () => {
    await openProfile();
    const table = within(section("Products vs JFrog")).getByRole("table", { name: "Products vs JFrog" });
    expect(within(table).getAllByRole("link", { name: "Artifactory" })[0]).toHaveAttribute("href", "/products/artifactory");
  });

  it("says a source's last fetch in the Freshness words", async () => {
    await openProfile();
    const collected = within(section("Sources")).getByRole("list", { name: "Collected from" });
    expect(collected.querySelector("time.first-letter\\:lowercase, .first-letter\\:lowercase")).not.toBeNull();
  });

  it("waits for the competitor index, so no name changes on load", async () => {
    let release: () => void = () => {};
    const ready = new Promise<void>((resolve) => (release = resolve));
    server.use(
      http.get("/api/v1/competitors", async () => {
        await ready;
        return HttpResponse.json(competitors);
      }),
    );
    let served = false;
    server.use(
      http.get("/api/v1/competitors/:slug/profile", () => {
        served = true;
        return HttpResponse.json(competitorProfile());
      }),
    );
    renderAs("pm", "/competitors/sonatype");
    await waitFor(() => expect(served).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 100)); // the profile and the rules have settled
    // Only the index is still loading: the page waits, so no chip or legend reads "Github" first.
    expect(screen.getByRole("status", { name: "Loading the profile" })).toBeInTheDocument();
    expect(screen.queryByRole("figure", { name: "Share of voice" })).not.toBeInTheDocument();
    await act(async () => release());
    const market = await screen.findByRole("figure", { name: "Share of voice" });
    expect(market).toHaveTextContent("GitHub");
    expect(market).not.toHaveTextContent("Github");
  });

  it("prints the threat factor's served clamps as the API does", async () => {
    await openProfile();
    const how = within(section("Overview")).getByText("How this is computed");
    await userEvent.click(how);
    expect(section("Overview")).toHaveTextContent("held between 0.5 and 2,");
  });
});

describe("a remote posting's place", () => {
  it("names each place once, however the board repeats Remote and the country", () => {
    const meta = (location: string, country: string) => postingMeta(posting({ department: "Sales", seniority: "Manager", remote: true, location, country }));
    expect(meta("Remote, Japan", "Japan")).toBe("Sales · Manager · Remote, Japan");
    expect(meta("Remote, Canada; Remote, United States", "United States")).toBe("Sales · Manager · Remote, Canada, United States");
    expect(meta("Remote; Remote, Canada; Remote, United States", "United States")).toBe("Sales · Manager · Remote, Canada, United States");
    expect(postingMeta(posting({ department: "Engineering", seniority: "Mid", remote: false, location: "Bangalore, India", country: "India" }))).toBe("Engineering · Mid · Bangalore, India");
  });
});
