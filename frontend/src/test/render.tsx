import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactElement } from "react";
import { MemoryRouter, type Location } from "react-router";
import { App } from "@/app/App";
// The intelligence routes load lazily in the app; tests load them up front, as the
// router did before, so a page's first test does not spend its wait compiling the module.
import "@/features/compare/ComparePage";
import "@/features/competitors/CompetitorPage";
import "@/features/competitors/CompetitorsPage";
import "@/features/products/ProductPage";
import "@/features/products/ProductsPage";
import { Providers } from "@/app/providers";
import type { Role } from "@/lib/api/client";
import { createQueryClient } from "@/lib/query";
import { personaFor } from "./fixtures";
import { LocationProbe } from "./LocationProbe";
import { server } from "./msw";

/**
 * Render `ui` inside the app's providers and an in-memory router starting at `route`.
 * `location()` returns the router's current location.
 */
export function renderWithProviders(ui: ReactElement, { route = "/" }: { route?: string } = {}) {
  const queryClient = createQueryClient({ retry: false });
  let current: Location | null = null;
  const result = render(
    <Providers queryClient={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        {ui}
        <LocationProbe onChange={(location) => (current = location)} />
      </MemoryRouter>
    </Providers>,
  );
  return {
    ...result,
    queryClient,
    location: (): Location => {
      if (!current) throw new Error("the router has not rendered yet");
      return current;
    },
  };
}

/** The whole app at `route`, signed in as the demo reader for `role`. */
export function renderAs(role: Role, route: string) {
  server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(personaFor[role])));
  return renderWithProviders(<App />, { route });
}

/** Pick `option` in the select whose accessible name matches `label` (Radix Select). */
export async function selectOption(label: RegExp | string, option: RegExp | string) {
  await userEvent.click(await screen.findByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}
