import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { App } from "@/app/App";
import { server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

it("signs out back to the login page", async () => {
  server.use(
    http.get("/api/v1/auth/me", () =>
      HttpResponse.json({ id: 3, name: "Priya Shah", email: "pm@frogeye.local", role: "pm" }),
    ),
  );
  renderWithProviders(<App />, { route: "/feed" });
  expect(await screen.findByRole("heading", { name: /feed/i })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /account/i }));
  await userEvent.click(await screen.findByRole("menuitem", { name: /sign out/i }));

  expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /feed/i })).not.toBeInTheDocument();
});
