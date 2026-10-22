import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { doc } from "@/test/fixtures";
import { server } from "@/test/msw";
import { renderAs, selectOption } from "@/test/render";

it("renders documents and sends competitor filter", async () => {
  const seen: URL[] = [];
  server.use(
    http.get("/api/v1/documents", ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json({ items: [doc({ title: "Sonatype ships Nexus One" })], next_cursor: null });
    }),
  );
  renderAs("pm", "/feed");
  expect(await screen.findByText("Sonatype ships Nexus One")).toBeInTheDocument();
  await selectOption(/competitor/i, "Sonatype");
  await waitFor(() => expect(seen.at(-1)!.searchParams.get("competitor")).toBe("sonatype"));
});

it("shows empty state", async () => {
  server.use(http.get("/api/v1/documents", () => HttpResponse.json({ items: [], next_cursor: null })));
  renderAs("pm", "/feed");
  expect(await screen.findByText(/run ingestion from Admin/)).toBeInTheDocument();
});

it("links each title out safely and loads the next page from the cursor", async () => {
  const cursors: (string | null)[] = [];
  server.use(
    http.get("/api/v1/documents", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      cursors.push(cursor);
      return cursor === "page-2"
        ? HttpResponse.json({ items: [doc({ title: "GitLab adds a dependency firewall" })], next_cursor: null })
        : HttpResponse.json({ items: [doc({ title: "Sonatype ships Nexus One" })], next_cursor: "page-2" });
    }),
  );
  renderAs("sales", "/feed");

  const link = await screen.findByRole("link", { name: /sonatype ships nexus one/i });
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");

  await userEvent.click(screen.getByRole("button", { name: /load more/i }));
  expect(await screen.findByText("GitLab adds a dependency firewall")).toBeInTheDocument();
  expect(cursors).toEqual([null, "page-2"]);
  expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
});

it("debounces the search box into the q parameter", async () => {
  const queries: (string | null)[] = [];
  server.use(
    http.get("/api/v1/documents", ({ request }) => {
      queries.push(new URL(request.url).searchParams.get("q"));
      return HttpResponse.json({ items: [doc()], next_cursor: null });
    }),
  );
  renderAs("exec", "/feed");
  await userEvent.type(await screen.findByRole("searchbox", { name: /search titles/i }), "registry");
  await waitFor(() => expect(queries.at(-1)).toBe("registry"));
  // One request for the initial page and one for the settled term, none per keystroke.
  expect(queries).toEqual([null, "registry"]);
});

it("keeps the typed text intact when the debounced search commits", async () => {
  const queries: (string | null)[] = [];
  server.use(
    http.get("/api/v1/documents", ({ request }) => {
      queries.push(new URL(request.url).searchParams.get("q"));
      return HttpResponse.json({ items: [doc()], next_cursor: null });
    }),
  );
  renderAs("pm", "/feed");
  const box = await screen.findByRole("searchbox", { name: /search titles/i });
  await userEvent.type(box, "a ");
  // The trimmed term commits after the debounce; the trailing space must survive in the box.
  await waitFor(() => expect(queries.at(-1)).toBe("a"));
  await userEvent.type(box, "b");
  expect(box).toHaveValue("a b");
  await waitFor(() => expect(queries.at(-1)).toBe("a b"));
});

it("follows the URL when the term changes from outside the box (Clear filters)", async () => {
  const queries: (string | null)[] = [];
  server.use(
    http.get("/api/v1/documents", ({ request }) => {
      queries.push(new URL(request.url).searchParams.get("q"));
      return HttpResponse.json({ items: [doc()], next_cursor: null });
    }),
  );
  renderAs("pm", "/feed?q=nexus");
  const box = await screen.findByRole("searchbox", { name: /search titles/i });
  expect(box).toHaveValue("nexus");
  await userEvent.click(screen.getByRole("button", { name: /clear filters/i }));
  expect(box).toHaveValue("");
  await waitFor(() => expect(queries.at(-1)).toBeNull());
});

it("links only http(s) addresses and shows anything else as plain text", async () => {
  server.use(
    http.get("/api/v1/documents", () =>
      HttpResponse.json({
        items: [
          doc({ title: "Original article", url: "https://news.example.com/story?id=7&utm_source=rss" }),
          doc({ title: "Script address", url: "javascript:alert(1)" }),
          doc({ title: "Bare identifier", url: "tag:example.com,2026:release-1" }),
        ],
        next_cursor: null,
      }),
    ),
  );
  renderAs("pm", "/feed");
  const link = await screen.findByRole("link", { name: /original article/i });
  expect(link).toHaveAttribute("href", "https://news.example.com/story?id=7&utm_source=rss");
  expect(screen.getByText("Script address")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /script address/i })).not.toBeInTheDocument();
  expect(screen.getByText("Bare identifier")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /bare identifier/i })).not.toBeInTheDocument();
});

it("lists companies by tier in the competitor filter, names A–Z, never JFrog itself", async () => {
  renderAs("pm", "/feed");
  const box = await screen.findByRole("combobox", { name: /^competitor$/i });
  await waitFor(() => expect(box).toBeEnabled());
  await userEvent.click(box);
  const options = await screen.findAllByRole("option");
  expect(options.map((o) => o.textContent)).toEqual(["All companies", "GitHub", "GitLab", "Sonatype", "Docker"]);
  expect(screen.getByText("Tier 1")).toBeInTheDocument();
  expect(screen.getByText("Tier 2")).toBeInTheDocument();
});

it("offers no JFrog scope in the Feed: JFrog-self news is never listed", async () => {
  renderAs("pm", "/feed");
  await userEvent.click(await screen.findByRole("combobox", { name: /^scope$/i }));
  const options = await screen.findAllByRole("option");
  expect(options.map((o) => o.textContent)).toEqual(["Any scope", "Competitor", "Industry"]);
});
