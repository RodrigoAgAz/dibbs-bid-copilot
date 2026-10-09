import { expect, test } from "@playwright/test";

test("sign up, log in, see today's RFQs, open one, see a recommendation", async ({ page }) => {
  const email = `e2e-${Date.now()}@broker-e2e.example.com`;
  const password = "e2e-password-123";

  // Sign up: creates an organization and its owner, lands on Today.
  await page.goto("/signup");
  await page.getByLabel("Company name").fill("E2E Federal Supply");
  await page.getByLabel("Your name").fill("Erin Endtoend");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();

  // Log out, then log back in.
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);

  // Today's RFQ lines (seeded) are listed for the new organization.
  const rows = page.getByTestId("rfq-row");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThan(0);

  // Open the top-ranked line and see its recommendation.
  const sol = (await rows.first().locator("a").first().innerText()).trim();
  await rows.first().locator("a").first().click();
  await expect(page).toHaveURL(new RegExp(`/rfq/${sol}`));
  const rec = page.getByTestId("recommendation").first();
  await expect(rec).toBeVisible();
  await expect(rec.getByTestId("recommended-bid")).toHaveText(/\$\d/);
  await expect(rec.getByText("P(win)", { exact: true })).toBeVisible();
  await expect(page.getByText("P(win) vs bid").first()).toBeVisible();
  await expect(page.getByText("Procurement history").first()).toBeVisible();
  await expect(page.getByText("Landed cost per unit").first()).toBeVisible();
});
