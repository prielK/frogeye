import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { personaFor, run } from "@/test/fixtures";
import { server } from "@/test/msw";
import { renderAs } from "@/test/render";

// an expired session anywhere in the app signs the reader out and keeps their place.
it("sends the reader to sign in when any API call returns 401, then back where they were", async () => {
  let expired = true;
  server.use(
    http.get("/api/v1/runs", () =>
      expired ? new HttpResponse(null, { status: 401 }) : HttpResponse.json([run()]),
    ),
    http.post("/api/v1/auth/login", () => {
      expired = false;
      return HttpResponse.json(personaFor.analyst);
    }),
  );
  renderAs("analyst", "/admin/runs");

  expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();

  await userEvent.click(await screen.findByRole("button", { name: /avery cohen/i }));
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
  expect(await screen.findByRole("heading", { name: /^runs$/i })).toBeInTheDocument();
});

