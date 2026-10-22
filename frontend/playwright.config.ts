import { defineConfig, devices } from "@playwright/test";

// Runs against an already-running stack (`docker compose up -d --build`).
export default defineConfig({
  testDir: "./e2e",
  // e2e/demo records the README demo media (playwright.demo.config.ts); not a smoke test.
  testIgnore: ["**/demo/**"],
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:8080",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
