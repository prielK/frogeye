import { defineConfig, devices } from "@playwright/test";

// Demo media (video frames and screenshots) for the README; see e2e/demo/demo.spec.ts.
// Not part of the smoke run: playwright.config.ts ignores e2e/demo.
export default defineConfig({
  testDir: "./e2e/demo",
  timeout: 15 * 60_000,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  outputDir: process.env.DEMO_OUT ? `${process.env.DEMO_OUT}/test-results` : "test-results",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.BASE_URL ?? "http://localhost:8080",
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "light",
    timezoneId: "Asia/Jerusalem",
    locale: "en-US",
    trace: "off",
    actionTimeout: 15_000,
    video: "off",
  },
});
