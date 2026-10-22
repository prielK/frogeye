import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { source } from "@/test/fixtures";
import { server } from "@/test/msw";
import { renderAs } from "@/test/render";

const blog = source({ id: 7, name: "Sonatype blog", competitor_slug: "sonatype", enabled: true });

it("analyst can toggle a source", async () => {
  const bodies: unknown[] = [];
  server.use(
    http.get("/api/v1/sources", () => HttpResponse.json([blog])),
    http.patch("/api/v1/sources/7", async ({ request }) => {
      const body = (await request.json()) as { enabled: boolean };
      bodies.push(body);
      return HttpResponse.json({ ...blog, enabled: body.enabled });
    }),
  );
  renderAs("analyst", "/admin/sources");
  const toggle = await screen.findByRole("switch", { name: /sonatype blog/i });
  expect(toggle).toBeChecked();
  await userEvent.click(toggle);
  await waitFor(() => expect(bodies).toEqual([{ enabled: false }]));
  await waitFor(() => expect(toggle).not.toBeChecked());
});

it("non-analyst sees no toggles", async () => {
  server.use(http.get("/api/v1/sources", () => HttpResponse.json([blog])));
  renderAs("sales", "/admin/sources");
  expect(await screen.findByText("Sonatype blog")).toBeInTheDocument();
  expect(screen.queryAllByRole("switch")).toHaveLength(0);
  expect(screen.queryByRole("button", { name: /test/i })).not.toBeInTheDocument();
});

it("groups sources by kind under readable labels", async () => {
  server.use(
    http.get("/api/v1/sources", () =>
      HttpResponse.json([
        blog,
        source({ name: "Google News: Sonatype", kind: "google_news" }),
        source({ name: "GitLab 10-K", kind: "sec_edgar", health: "down", last_error: "HTTP 503" }),
      ]),
    ),
  );
  renderAs("pm", "/admin/sources");
  expect(await screen.findByRole("rowheader", { name: /rss & atom feeds/i })).toBeInTheDocument();
  expect(screen.getByRole("rowheader", { name: /google news/i })).toBeInTheDocument();
  const sec = screen.getByRole("rowheader", { name: /sec filings/i });
  expect(within(sec).getByText(/1 down/i)).toBeInTheDocument();
});

it("analyst can test-fetch a source and see sample items", async () => {
  server.use(
    http.get("/api/v1/sources", () => HttpResponse.json([blog])),
    http.post("/api/v1/sources/7/test", () =>
      HttpResponse.json({
        ok: true,
        item_count: 23,
        sample: [{ title: "Nexus One is here", url: "https://example.com/nexus-one", published_at: null }],
        error: null,
      }),
    ),
  );
  renderAs("analyst", "/admin/sources");
  await userEvent.click(await screen.findByRole("button", { name: /test sonatype blog/i }));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("Nexus One is here")).toBeInTheDocument();
  expect(within(dialog).getByText(/23 items/i)).toBeInTheDocument();
});

// ---- a job board's test fetch counts its postings; a page's, its text ----

describe("the test fetch of a job board or a watched page", () => {
  const board = source({ id: 8, name: "Sonatype on Greenhouse", kind: "job_board", url: "https://boards.greenhouse.io/sonatype", competitor_slug: "sonatype", enabled: true });
  const page = source({ id: 9, name: "Sonatype pricing page", kind: "page_watch", url: "https://www.sonatype.com/pricing", competitor_slug: "sonatype", enabled: true });
  const postings = Array.from({ length: 5 }, (_, i) => ({ title: `Staff Engineer ${i + 1}`, url: `https://boards.greenhouse.io/sonatype/jobs/${100 + i}`, published_at: null }));

  async function testFetch(target: typeof board, result: Record<string, unknown>) {
    server.use(
      http.get("/api/v1/sources", () => HttpResponse.json([target])),
      http.post(`/api/v1/sources/${target.id}/test`, () => HttpResponse.json(result)),
    );
    renderAs("analyst", "/admin/sources");
    await userEvent.click(await screen.findByRole("button", { name: new RegExp(`test ${target.name}`, "i") }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/Listed|Read the page|no main text|no open posting/);
    return dialog;
  }

  it("counts a board listed in full in postings, its first postings below", async () => {
    const dialog = await testFetch(board, { ok: true, item_count: 142, sample: postings, error: null, postings_count: 142, postings_complete: true, page_text_chars: null });
    expect(dialog).toHaveTextContent("Listed 142 postings, the whole board.");
    expect(dialog).toHaveTextContent("The first 5 are below.");
    expect(within(dialog).getByText("Staff Engineer 1")).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/items/);
  });

  it("warns that a partial listing measures nothing", async () => {
    const dialog = await testFetch(board, { ok: true, item_count: 100, sample: postings, error: null, postings_count: 100, postings_complete: false, page_text_chars: null });
    expect(dialog).toHaveTextContent("Listed 100 postings, only part of the board.");
    expect(dialog).toHaveTextContent(/A partial listing is stored but measures nothing: open roles and their changes count complete listings only\./);
  });

  it("says a board listed in full with no posting has none open", async () => {
    const dialog = await testFetch(board, { ok: true, item_count: 0, sample: [], error: null, postings_count: 0, postings_complete: true, page_text_chars: null });
    expect(dialog).toHaveTextContent("The board is listed in full and has no open posting right now.");
    expect(dialog).not.toHaveTextContent(/returned no items/);
  });

  it("counts a watched page's main text in characters", async () => {
    const dialog = await testFetch(page, {
      ok: true,
      item_count: 1,
      sample: [{ title: "Pricing | Sonatype", url: "https://www.sonatype.com/pricing", published_at: null }],
      error: null,
      postings_count: null,
      postings_complete: null,
      page_text_chars: 2140,
    });
    expect(dialog).toHaveTextContent("Read the page: 2,140 characters of main text.");
    expect(within(dialog).getByText("Pricing | Sonatype")).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/1 item/);
  });

  it("says when a watched page gave no main text", async () => {
    const dialog = await testFetch(page, { ok: true, item_count: 0, sample: [], error: null, postings_count: null, postings_complete: null, page_text_chars: 0 });
    expect(dialog).toHaveTextContent(/The page answered, but FrogEye read no main text from it\./);
  });
});

// ---- once a day for a board or page; a repository's readings ----

describe("the test fetch's footprint and units", () => {
  const board = source({ id: 8, name: "Sonatype on Greenhouse", kind: "job_board", url: "https://boards.greenhouse.io/sonatype", competitor_slug: "sonatype", enabled: true });
  const stats = source({ id: 11, name: "sonatype/nexus-public · GitHub stats", kind: "github_repo_stats", url: "https://api.github.com/repos/sonatype/nexus-public", competitor_slug: "sonatype", enabled: true });

  async function open(target: typeof board, respond: () => Response) {
    server.use(
      http.get("/api/v1/sources", () => HttpResponse.json([target])),
      http.post(`/api/v1/sources/${target.id}/test`, respond),
    );
    renderAs("analyst", "/admin/sources");
    await userEvent.click(await screen.findByRole("button", { name: new RegExp(`test ${target.name}`, "i") }));
    return screen.findByRole("dialog");
  }

  it("says a board already read today is read again tomorrow, calmly, not as a failure", async () => {
    const dialog = await open(board, () => HttpResponse.json({ detail: "Already fetched today; next fetch tomorrow" }, { status: 409 }));
    expect(await within(dialog).findByText("Already fetched today; next fetch tomorrow")).toBeInTheDocument();
    expect(dialog).toHaveTextContent(/at most once a day/);
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/didn't answer/);
  });

  it("says a board's test is that day's one request", async () => {
    const dialog = await open(board, () =>
      HttpResponse.json({ ok: true, item_count: 0, sample: [], error: null, postings_count: 0, postings_complete: true, page_text_chars: null, metrics_count: null }),
    );
    expect(dialog).toHaveTextContent(/today's one request to it/);
  });

  it("counts a repository's readings, never 0 items", async () => {
    const dialog = await open(stats, () =>
      HttpResponse.json({ ok: true, item_count: 2, sample: [], error: null, postings_count: null, postings_complete: null, page_text_chars: null, metrics_count: 2 }),
    );
    expect(await within(dialog).findByText(/Read 2 readings/)).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/returned no items|0 items/);
  });
});
