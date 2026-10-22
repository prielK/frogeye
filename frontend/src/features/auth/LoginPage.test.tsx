import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { App } from "@/app/App";
import { server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

it("lists personas and logs in as the chosen one", async () => {
  server.use(
    http.get("/api/v1/auth/personas", () =>
      HttpResponse.json([{ name: "Priya Shah", email: "pm@frogeye.local", role: "pm" }]),
    ),
    http.post("/api/v1/auth/login", () =>
      HttpResponse.json({ id: 3, name: "Priya Shah", email: "pm@frogeye.local", role: "pm" }),
    ),
  );
  renderWithProviders(<App />, { route: "/login" });
  await userEvent.click(await screen.findByRole("button", { name: /priya shah/i }));
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
  // Readers land on the Morning Briefing, the home page.
  expect(await screen.findByRole("heading", { name: /morning briefing/i })).toBeInTheDocument();
});

it("shows an error on bad credentials", async () => {
  server.use(http.post("/api/v1/auth/login", () => new HttpResponse(null, { status: 401 })));
  renderWithProviders(<App />, { route: "/login" });
  await userEvent.type(await screen.findByLabelText(/email/i), "pm@frogeye.local");
  await userEvent.type(screen.getByLabelText(/password/i), "not-the-password");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
  expect(await screen.findByText(/invalid email or password/i)).toBeInTheDocument();
});
