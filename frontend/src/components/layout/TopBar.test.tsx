import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@/test/msw";
import { renderAs } from "@/test/render";

it("opens Search everything from the top bar, with the search box ready to type in", async () => {
  const app = renderAs("pm", "/briefing");
  await userEvent.click(await screen.findByRole("link", { name: /^search/i }));
  expect(app.location()).toMatchObject({ pathname: "/signals", search: "?mode=everything" });
  const box = await screen.findByRole("searchbox", { name: /search articles, signals and jfrog docs/i });
  await waitFor(() => expect(box).toHaveFocus());
});

it("opens it from the keyboard too (Ctrl K)", async () => {
  const app = renderAs("sales", "/admin/agents");
  await screen.findByRole("link", { name: /^search/i });
  await userEvent.keyboard("{Control>}k{/Control}");
  await waitFor(() => expect(app.location()).toMatchObject({ pathname: "/signals", search: "?mode=everything" }));
  await waitFor(() => expect(screen.getByRole("searchbox", { name: /search articles, signals and jfrog docs/i })).toHaveFocus());
});

it("says the data is a demo capture, and when it was taken, on a database restored from the snapshot", async () => {
  server.use(http.get("/api/v1/demo-snapshot", () => HttpResponse.json({ captured_at: "2026-10-22T01:13:10+00:00" })));
  renderAs("exec", "/briefing");
  const note = await screen.findByText(/captured 22 Oct 2026/);
  expect(note).toHaveTextContent("Demo data captured 22 Oct 2026");
  expect(note).toHaveTextContent(/new data arrives with the daily run \(needs an OpenAI key\)/);
});

it("says nothing about demo data otherwise", async () => {
  renderAs("exec", "/briefing");
  await screen.findByRole("link", { name: /^search/i });
  expect(screen.queryByText(/demo data/i)).not.toBeInTheDocument();
});
