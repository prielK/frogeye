import { expect, test } from "@playwright/test";

test("analyst signs in, lands on the Briefing, and reaches the Feed, Admin > Runs, Compare and Admin > Evals", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("analyst@frogeye.local");
  await page.getByLabel("Password").fill("frogeye-demo");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/briefing/);
  await expect(page.getByRole("heading", { name: "Morning Briefing" })).toBeVisible();

  await page.getByRole("link", { name: "Feed" }).first().click();
  await expect(page).toHaveURL(/\/feed/);
  await expect(page.getByRole("heading", { name: "Feed" })).toBeVisible();

  await page.getByRole("link", { name: "Runs", exact: true }).first().click();
  await expect(page).toHaveURL(/\/admin\/runs/);
  await expect(page.getByRole("button", { name: "Run now" })).toBeVisible();

  await page.getByRole("link", { name: "Compare", exact: true }).first().click();
  await expect(page).toHaveURL(/\/compare/);
  await expect(page.getByRole("heading", { level: 1, name: "Compare" })).toBeVisible();

  await page.getByRole("link", { name: "Evals", exact: true }).first().click();
  await expect(page).toHaveURL(/\/admin\/evals/);
  await expect(page.getByRole("heading", { level: 1, name: "Evals" })).toBeVisible();
});
